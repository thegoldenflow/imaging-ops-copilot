"""The patient instructions agent (spec 7.3): bilingual instructions from the signed discharge summary only.

For one encounter, as the nurse it works for (or as itself after the summary is
signed, in the journey or the background intake):

1. reads the encounter, the patient and the encounter's discharge summary
   (getSignedDischargeSummary); without a summary signed final it stops with
   "insufficient information" (it never reads the raw record);
2. gives the model the summary's items (de-identified: names and dates as tokens)
   and the menus; the model answers with codes per item (InstructionsPlan); codes or
   items that do not exist get one more try with the problems listed, then are
   dropped;
3. builds the one structure (instructions.build: facts copied from the summary, a
   medicine the model left out added by rule), validates it and renders English,
   Simplified and Traditional Chinese from it, with the readability checks;
4. writes the draft (`pinstr-<encounter>`, three text attachments and the structure)
   through draftClinicalDocument; a nurse reviews and signs.

The patient's language preference is applied when rendering, after the model: the
default language of the draft is the patient's communication.language.
Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.modules.documentation import document as D
from app.modules.documentation import instructions as I
from app.modules.documentation import mocks  # noqa: F401  (registers the mock models)
from app.modules.documentation import phrases as P
from app.modules.documentation.deid import Deid
from app.modules.documentation.facts import language_of, unit_of
from app.modules.documentation.models import InstructionsPlan, ValidationReport
from app.modules.documentation.render import display_language

AGENT_ID = "patient_instructions"
KIND = "patient_instructions"


@dataclass
class InstructionsResult:
    status: Literal["drafted", "exists", "insufficient", "refused", "unavailable"]
    run_id: str
    document_id: str | None = None
    missing: list[str] = field(default_factory=list)
    reason: str | None = None
    report: ValidationReport | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _resources(result, rtype: str) -> list[dict]:
    return [r for r in (result.output or {}).get("resources", []) if r.get("resourceType") == rtype]


def signed_summary(resources: list[dict], encounter_id: str) -> dict | None:
    wanted = D.document_id("discharge_summary", encounter_id)
    return next((d for d in resources if d.get("resourceType") == "DocumentReference" and d["id"] == wanted
                 and d.get("docStatus") == "final"), None)


def payload_for(content, *, summary: dict, items: dict, language: str, report: ValidationReport, renderings: dict,
                mode: str, run_id: str | None, prompt_version: str | None, plan: dict | None, notes: list[str]) -> dict:
    return {"schema": D.SCHEMAS[KIND], "kind": KIND, "encounter_id": D.read_data(summary).get("encounter_id"),
            "mode": mode, "content": content.model_dump(mode="json", by_alias=True),
            "ai_plan": plan, "summary": f"DocumentReference/{summary['id']}", "summary_items": items,
            "patient_language": language, "default_language": display_language(language),
            "validation": report.model_dump(mode="json"),
            "readability": {lang: r["readability"] for lang, r in renderings.items()},
            "terms": renderings["zh-Hans"]["terms"], "notes": notes, "run_id": run_id,
            "prompt_version": prompt_version, "edits": []}


def texts_for(renderings: dict) -> list[dict]:
    return [{"language": lang, "title": r["title"], "text": r["text"][:30000]} for lang, r in renderings.items()]


def draft(run: AgentRun, *, force: bool = False) -> InstructionsResult:
    result = InstructionsResult("drafted", run.run_id)
    encounter_id = run.encounter_id
    ctx = run.gather(("getSignedDischargeSummary", {"encounter_id": encounter_id}))
    if not ctx.ok:
        refused = ctx.refused[0]
        result.status, result.reason = "refused", f"{refused.reason}: {refused.detail}"
        return result
    (read,) = ctx.results
    if not force:
        docs = run.tool("getDocuments", {"encounter_id": encounter_id})
        wanted = D.document_id(KIND, encounter_id)
        existing = next((d for d in _resources(docs, "DocumentReference") if d["id"] == wanted and D.read_data(d)),
                        None) if docs.ok else None
        if existing is not None:
            result.status, result.document_id = "exists", existing["id"]
            return result
    raw = (read.output or {}).get("resources", [])
    summary = signed_summary(raw, encounter_id)
    if summary is None:
        result.status, result.missing = "insufficient", ["a discharge summary signed final by the physician"]
        return result
    encounter = next((r for r in raw if r["resourceType"] == "Encounter"), {})
    patient = next((r for r in raw if r["resourceType"] == "Patient"), None)
    language = language_of(patient)
    data = D.read_data(summary) or {}
    items = I.summary_items(data.get("content") or {})
    admitted = (data.get("inputs") or {}).get("admitted")
    deid = Deid(ctx, date.fromisoformat(admitted[:10]) if admitted else None)
    shown = [deid.value("", row) for row in I.model_items(items)]
    prompt = load_prompt(run.spec.prompt_version)
    variables = {"items": json.dumps(shown, ensure_ascii=False, separators=(",", ":")), "menus": P.menu_text(),
                 "feedback": ""}
    plan = None
    for attempt in range(2):
        outcome = run.model(prompt, variables, InstructionsPlan, context=ctx)
        if outcome.status != "ok":
            result.status, result.reason = "unavailable", f"model {outcome.status}"
            return result
        plan = InstructionsPlan.model_validate(outcome.data)
        problems = I.guard(plan, items)
        if not problems:
            break
        result.problems += [f"attempt {attempt + 1}: {p}" for p in problems[:10]]
        variables["feedback"] = ("\nYour previous answer used items or codes that do not exist; use only the summary's "
                                 "items and the menu codes:\n- " + "\n- ".join(problems[:10]))
    unit = unit_of(encounter) or ""
    content, notes = I.build(plan, items, summary_ref=f"DocumentReference/{summary['id']}", unit=unit)
    result.notes = notes
    conditions = [i.get("code") for p, i in items.items() if p.startswith("discharge_diagnoses") and i.get("code")]
    report, renderings = I.validate_and_render(content, items, patient_conditions=conditions)
    result.report = report
    payload = payload_for(content, summary=summary, items=items, language=language, report=report,
                          renderings=renderings, mode="ai", run_id=run.run_id, prompt_version=prompt.version,
                          plan=plan.model_dump(mode="json"), notes=notes)
    out = run.tool("draftClinicalDocument", {
        "encounter_id": encounter_id, "kind": KIND, "document_id": D.document_id(KIND, encounter_id),
        "title": "Patient discharge instructions (draft)", "texts": texts_for(renderings),
        "data": json.dumps(payload, ensure_ascii=False),
        "source_refs": [f"DocumentReference/{summary['id']}"],
        **({"sign_block": report.summary()} if report.blocking else {})})
    if not out.ok:
        result.status, result.reason = "refused", f"{out.reason}: {out.detail}"
    else:
        result.document_id = out.output["document_id"]
    return result
