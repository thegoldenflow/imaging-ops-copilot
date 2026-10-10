"""The order-review commentary: a runtime agent (spec 6.4, 7.2) that comments on one order after the rule layer.

For one order review (the service ran the rules first), acting as itself:

1. reads context through its read tools (getMedicationContext; for a lab or
   imaging order also getServiceOrderContext), de-identified by the runtime; the
   Tool Gateway refuses it without the patient's ai_processing consent;
2. retrieves guideline sections (knowledge/guidelines, keyword scoring) for the
   drug, its class, the rule findings and the diagnoses;
3. asks the model for the spec's schema `{concerns[{type, severity, explanation,
   evidence_refs[]}], alternatives[{suggestion, rationale}], guideline_refs[]}`
   plus a confidence per concern;
4. guards it: evidence only from the context the run read (and the rules'
   evidence), guideline references only from the retrieved sections, no claim that
   an order was changed; on a problem one more try with the problems listed, then
   whatever is wrong is dropped and recorded;
5. puts it into the reviewer's queue through submitOrderReview (an ai-review Task
   whose focus is the order). The commentary never blocks and never changes the
   order: only rules block, and a person decides.

Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.modules.med_safety import knowledge
from app.modules.med_safety import mocks  # noqa: F401  (registers the mock model)

AGENT_ID = "order_review"
ConcernType = Literal["interaction", "renal", "allergy", "dose", "duplicate_therapy", "duplicate_test", "indication",
                      "monitoring", "age_related", "other"]
_CLAIMS = [re.compile(p, re.I) for p in (
    r"\b(i|we)\s+(have\s+|'ve\s+)?(stopped|cancel+ed|changed|discontinued|held|blocked|modified|removed|reduced|"
    r"adjusted|replaced|switched)\b",
    r"\b(has|have|had)\s+(now\s+|already\s+)?been\s+(stopped|cancel+ed|changed|discontinued|held|blocked|modified|"
    r"removed|reduced|adjusted|replaced|switched)\b",
    r"\b(order|it)\s+(is|was)\s+(now\s+)?(blocked|cancel+ed|discontinued|stopped)\b")]


class Concern(BaseModel):
    type: ConcernType
    severity: Literal["major", "moderate", "minor"]
    explanation: str = Field(max_length=800)
    evidence_refs: list[str] = Field(max_length=12)
    confidence: float = Field(ge=0, le=1)


class Alternative(BaseModel):
    suggestion: str = Field(max_length=300)
    rationale: str = Field(max_length=500)


class Commentary(BaseModel):
    concerns: list[Concern] = Field(max_length=8)
    alternatives: list[Alternative] = Field(max_length=3)
    guideline_refs: list[str] = Field(max_length=6)


@dataclass
class CommentaryResult:
    run_id: str
    status: Literal["done", "refused", "unavailable"]
    output: dict | None = None
    reason: str | None = None  # why there is no commentary
    model_status: str | None = None  # ok, needs_human, unavailable (the model call)
    attempts: int = 0
    problems: list[str] = field(default_factory=list)  # what the guards found (all attempts)
    dropped_refs: list[str] = field(default_factory=list)  # references the guards removed
    evaluated: bool = True
    context_refs: list[str] = field(default_factory=list)
    guidelines: list[dict] = field(default_factory=list)  # the retrieved sections (id, title, text)
    review_task_id: str | None = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ---------- guards ----------


def check(output: Commentary, allowed_refs: set[str], allowed_guidelines: set[str]) -> list[str]:
    problems = []
    for i, c in enumerate(output.concerns, start=1):
        unknown = [r for r in c.evidence_refs if r not in allowed_refs]
        if unknown:
            problems.append(f"concern {i}: evidence not in the context: {', '.join(unknown[:5])}")
    unknown = [g for g in output.guideline_refs if g not in allowed_guidelines]
    if unknown:
        problems.append(f"guideline references not among the sections given: {', '.join(unknown[:5])}")
    texts = [c.explanation for c in output.concerns] + [x for a in output.alternatives for x in (a.suggestion,
                                                                                                  a.rationale)]
    for text in texts:
        if any(p.search(text) for p in _CLAIMS):
            problems.append(f"claims an order was changed (in: {text[:80]!r}); everything is advice")
    return problems


def clean(output: Commentary, allowed_refs: set[str], allowed_guidelines: set[str]) -> tuple[dict, list[str]]:
    """After the last attempt: drop what the guards reject (references, claiming sentences); returns (output, dropped)."""
    dropped: list[str] = []
    concerns = []
    for c in output.concerns:
        if any(p.search(c.explanation) for p in _CLAIMS):
            continue
        keep = [r for r in c.evidence_refs if r in allowed_refs]
        dropped += [r for r in c.evidence_refs if r not in allowed_refs]
        concerns.append({**c.model_dump(), "evidence_refs": list(dict.fromkeys(keep))})
    alternatives = [a.model_dump() for a in output.alternatives
                    if not any(p.search(a.suggestion) or p.search(a.rationale) for p in _CLAIMS)]
    guidelines = [g for g in output.guideline_refs if g in allowed_guidelines]
    dropped += [g for g in output.guideline_refs if g not in allowed_guidelines]
    return {"concerns": concerns, "alternatives": alternatives, "guideline_refs": list(dict.fromkeys(guidelines))}, \
        dropped


# ---------- the run ----------


def _query_terms(review: dict, resources: list[dict]) -> tuple[list[str], list[str]]:
    """(terms, context) for the guideline search: the order and its findings first, then the rest of the record."""
    order = review["order"]
    terms = [order.get("name", ""), order.get("drug_class", ""), order.get("display", "")]
    terms += [f.get("rule", "").replace("_", " ") for f in review["findings"]]
    terms += [f.get("title", "") for f in review["findings"]]
    if review["kind"] == "service":
        terms += ["repeat test", "duplicate"]
    context = []
    for r in resources:
        if r.get("resourceType") in ("Condition",):
            context.append(((r.get("code") or {}).get("coding") or [{}])[0].get("display") or "")
        if r.get("resourceType") in ("MedicationRequest", "MedicationStatement"):
            context.append(((r.get("medicationCodeableConcept") or {}).get("coding") or [{}])[0].get("display") or "")
    return [t for t in terms if t], [t for t in context if t]


def comment(run: AgentRun, review: dict) -> CommentaryResult:
    """`review`: the stored OrderReview as a dict (order summary, findings, encounter, reviewer roles)."""
    result = CommentaryResult(run.run_id, "done", evaluated=run.spec.evaluated)
    order_type, _, order_id = review["order_ref"].partition("/")
    calls = [("getMedicationContext", {"encounter_id": review["encounter_id"]})]
    if order_type == "ServiceRequest":
        calls.append(("getServiceOrderContext", {"encounter_id": review["encounter_id"], "order_id": order_id}))
    ctx = run.gather(*calls)
    if not ctx.ok:
        refused = ctx.refused[0]
        run.finish(outcome="refused")
        return CommentaryResult(run.run_id, "refused", reason=f"{refused.reason}: {refused.detail}",
                                evaluated=run.spec.evaluated)
    result.context_refs = list(dict.fromkeys(ctx.refs))
    order = next((r for r in ctx.resources if f"{r['resourceType']}/{r['id']}" == review["order_ref"]), review["order"])
    terms, context_terms = _query_terms(review, ctx.resources)
    sections = knowledge.search(terms, context=context_terms, limit=4)
    result.guidelines = [s.snippet() for s in sections]
    allowed_refs = set(result.context_refs) | {r for f in review["findings"] for r in f.get("evidence_refs", [])}
    allowed_guidelines = {s.id for s in sections}
    findings = [{k: f[k] for k in ("rule", "severity", "title", "detail", "recommendation", "evidence_refs")}
                for f in review["findings"]]
    variables = {"kind": "medication order" if order_type == "MedicationRequest" else "laboratory or imaging order",
                 "order": json.dumps(order, ensure_ascii=False), "findings": json.dumps(findings, ensure_ascii=False),
                 "guidelines": json.dumps(result.guidelines, ensure_ascii=False), "context": ctx.json(),
                 "feedback": ""}
    prompt = load_prompt(run.spec.prompt_version)
    output: Commentary | None = None  # the last valid answer (a failed retry keeps the first)
    for attempt in range(2):
        result.attempts += 1
        outcome = run.model(prompt, variables, Commentary, context=ctx)
        result.model_status = outcome.status
        if outcome.status != "ok":
            break
        output = Commentary.model_validate(outcome.data)
        found = check(output, allowed_refs, allowed_guidelines)
        if not found:
            break
        result.problems += [f"attempt {attempt + 1}: {p}" for p in found]
        variables["feedback"] = ("\nYour previous answer was rejected for these reasons; fix them:\n- "
                                 + "\n- ".join(found))
    if output is None:
        run.finish(outcome="needs_human")
        result.status, result.reason = "unavailable", f"model {result.model_status}"
        return result
    data, result.dropped_refs = clean(output, allowed_refs, allowed_guidelines)
    result.output = data
    severity = max((c["severity"] for c in data["concerns"]), key=lambda s: ("minor", "moderate", "major").index(s),
                   default="none")
    summary = "; ".join(c["type"].replace("_", " ") for c in data["concerns"][:4]) or "no concerns"
    evidence = list(dict.fromkeys(r for c in data["concerns"] for r in c["evidence_refs"]))
    review_task = run.tool("submitOrderReview", {
        "review_id": review["id"], "encounter_id": review["encounter_id"], "order_ref": review["order_ref"],
        "reviewer_roles": review["reviewer_roles"], "severity": severity,
        "summary": f"{review['order'].get('name') or review['order'].get('display', 'order')}: AI commentary "
                   f"({summary})"[:300],
        "commentary": json.dumps(data, ensure_ascii=False)[:12000], "evidence_refs": evidence[:60]})
    result.review_task_id = (review_task.output or {}).get("task_id")
    confidence = max((c["confidence"] for c in data["concerns"]), default=1.0)
    run.finish(outcome="completed", confidence=confidence)
    return result
