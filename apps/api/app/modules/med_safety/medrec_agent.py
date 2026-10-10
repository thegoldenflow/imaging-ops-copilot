"""The reconciliation's AI part: a runtime agent (spec 6.4, 7.2) that judges new medications' indications.

For one reconciliation (the service ran the rule layer: omission, dose or frequency
change, duplicate therapy, new medication), acting as itself:

1. reads the encounter's medication context (getMedicationContext, de-identified;
   refused without the patient's ai_processing consent);
2. asks the model, for every new medication without a diagnosis named on its order,
   whether the diagnosis list holds an indication (with a confidence and the
   Condition it relies on), and for every rule-found discrepancy a plain-language
   sentence;
3. guards it: judgements only for the medications asked about, indications only
   from the diagnosis list, notes only for the listed discrepancies (one more try
   with the problems listed, then what is wrong is dropped);
4. settles the items (reconcile.apply_judgements: indicated with a confidence at or
   above the registry's threshold stays a new medication, anything else becomes
   "new without indication" for the pharmacist) and writes the draft record through
   draftMedRec (a DocumentReference, docStatus preliminary).

The AI never decides: the pharmacist handles every discrepancy and signs.
Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.modules.med_safety import knowledge
from app.modules.med_safety import mocks  # noqa: F401  (registers the mock model)
from app.modules.med_safety.document import record_id, render
from app.modules.med_safety.reconcile import Judgement, RecItem, apply_judgements, candidates

AGENT_ID = "medication_reconciliation"
DEFAULT_THRESHOLD = 0.7


class IndicationJudgement(BaseModel):
    item_id: str
    indicated: bool
    indication_ref: str  # a Condition reference from the diagnosis list, or "" when there is none
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(max_length=500)


class DiscrepancyNote(BaseModel):
    item_id: str
    explanation: str = Field(max_length=500)


class MedRecOutput(BaseModel):
    judgements: list[IndicationJudgement] = Field(max_length=40)
    notes: list[DiscrepancyNote] = Field(max_length=60)


@dataclass
class JudgeResult:
    run_id: str
    status: Literal["done", "refused", "unavailable"]
    items: list[RecItem] = field(default_factory=list)  # settled (or unchanged when refused / unavailable)
    reason: str | None = None
    model_status: str | None = None
    attempts: int = 0
    problems: list[str] = field(default_factory=list)
    evaluated: bool = True
    judged: int = 0
    document_id: str | None = None
    condition_refs: list[str] = field(default_factory=list)

    def detail(self) -> dict:
        return {"run_id": self.run_id, "reason": self.reason, "model_status": self.model_status,
                "attempts": self.attempts, "problems": self.problems, "evaluated": self.evaluated,
                "judged": self.judged}


def check(output: MedRecOutput, candidate_ids: set[str], discrepancy_ids: set[str], condition_refs: set[str]) -> list[str]:
    problems = []
    for j in output.judgements:
        if j.item_id not in candidate_ids:
            problems.append(f"judgement for {j.item_id!r}, which is not a medication to judge")
        if j.indication_ref and j.indication_ref not in condition_refs:
            problems.append(f"{j.item_id}: indication {j.indication_ref!r} is not on the diagnosis list")
    for n in output.notes:
        if n.item_id not in discrepancy_ids:
            problems.append(f"note for {n.item_id!r}, which is not a listed discrepancy")
    return problems


def _describe(item: RecItem) -> dict:
    med = item.order or item.inpatient or item.home
    return {"item_id": item.id, "medication": med.name if med else "", "class": med.drug_class if med else "",
            "dose": med.text if med else "", "rxcui": med.rxcui if med else ""}


def write_draft(run: AgentRun, session: dict, items: list[RecItem]) -> str | None:
    """The draft record through draftMedRec; returns its id. The arguments depend only on the reconciliation and its
    settled items (the record's own id is left out: the tool writes it when no other draft is named), so the same
    call again, from a retried journey step, gets the first result back from the gateway's idempotency key."""
    text, data = render(session, items)
    named = session.get("document_id")
    draft = run.tool("draftMedRec", {
        "encounter_id": session["encounter_id"], "session_id": session["id"], "kind": session["kind"],
        "text": text[:30000], "data": data,
        **({"document_id": named} if named and named != record_id(session["encounter_id"], session["kind"]) else {}),
        "source_refs": list(dict.fromkeys(r for i in items for r in i.evidence_refs))[:200]})
    return (draft.output or {}).get("document_id")


def judge(run: AgentRun, session: dict, items: list[RecItem]) -> JudgeResult:
    """`session`: the stored MedRecSession as a dict (id, kind, encounter_id, document_id)."""
    result = JudgeResult(run.run_id, "done", items=items, evaluated=run.spec.evaluated)
    ctx = run.gather(("getMedicationContext", {"encounter_id": session["encounter_id"]}))
    if not ctx.ok:
        refused = ctx.refused[0]
        run.finish(outcome="refused")
        result.status, result.reason = "refused", f"{refused.reason}: {refused.detail}"
        return result
    conditions = [r for r in ctx.resources if r["resourceType"] == "Condition"]
    condition_refs = {f"Condition/{c['id']}" for c in conditions}
    result.condition_refs = sorted(condition_refs)
    todo = candidates(items)
    discrepancies = [i for i in items if i.discrepancy]
    judgements: dict[str, Judgement] = {}
    notes: dict[str, str] = {}
    if todo or discrepancies:
        encounter = next((r for r in ctx.resources if r["resourceType"] == "Encounter"), {})
        terms = [d["medication"] for d in map(_describe, todo)] + [d["class"] for d in map(_describe, todo)]
        terms += ["indication", "medication reconciliation", "omission"]
        variables = {
            "kind": session["kind"],
            "candidates": json.dumps([_describe(i) for i in todo], ensure_ascii=False),
            "discrepancies": "\n".join(f"{i.id}: {i.explanation}" for i in discrepancies) or "none",
            "conditions": json.dumps([{"ref": f"Condition/{c['id']}",
                                       "code": ((c.get("code") or {}).get("coding") or [{}])[0].get("code"),
                                       "display": ((c.get("code") or {}).get("coding") or [{}])[0].get("display"),
                                       "status": ((c.get("clinicalStatus") or {}).get("coding") or [{}])[0]
                                       .get("code")} for c in conditions], ensure_ascii=False),
            "encounter": json.dumps({"id": encounter.get("id"), "reasonCode": encounter.get("reasonCode")},
                                    ensure_ascii=False),
            "guidelines": json.dumps([s.snippet() for s in knowledge.search(terms, limit=3)], ensure_ascii=False),
            "feedback": ""}
        prompt = load_prompt(run.spec.prompt_version)
        output: MedRecOutput | None = None
        ids, discrepancy_ids = {i.id for i in todo}, {i.id for i in discrepancies}
        for attempt in range(2):
            result.attempts += 1
            outcome = run.model(prompt, variables, MedRecOutput, context=ctx)
            result.model_status = outcome.status
            if outcome.status != "ok":
                break
            output = MedRecOutput.model_validate(outcome.data)
            found = check(output, ids, discrepancy_ids, condition_refs)
            if not found:
                break
            result.problems += [f"attempt {attempt + 1}: {p}" for p in found]
            variables["feedback"] = ("\nYour previous answer was rejected for these reasons; fix them:\n- "
                                     + "\n- ".join(found))
        if output is None:
            run.finish(outcome="needs_human")
            result.status, result.reason = "unavailable", f"model {result.model_status}"
            return result
        for j in output.judgements:
            if j.item_id in ids:
                judgements[j.item_id] = Judgement(item_id=j.item_id, indicated=j.indicated,
                                                  indication_ref=j.indication_ref or None, confidence=j.confidence,
                                                  explanation=j.explanation)
        notes = {n.item_id: n.explanation for n in output.notes if n.item_id in discrepancy_ids}
    threshold = run.spec.confidence_threshold or DEFAULT_THRESHOLD
    settled = apply_judgements(items, judgements, threshold, unavailable="the AI gave no judgement for it",
                               condition_refs=condition_refs)
    result.items = [i.model_copy(update={"ai_note": notes.get(i.id)}) if i.id in notes else i for i in settled]
    result.judged = len(judgements)
    result.document_id = write_draft(run, session, result.items)
    confidence = min((i.confidence for i in result.items if i.source == "ai" and i.confidence is not None),
                     default=1.0)
    run.finish(outcome="completed", confidence=confidence)
    return result
