"""The nursing handoff agent (spec 7.3): one SBAR per shift and patient from the last 12 hours.

For one encounter and the shift ending (`at`, hospital time, given by the caller):

1. reads the 12-hour context (getHandoffContext: observations since the cut-off,
   open tasks, active flags, the stay's diagnoses, procedures and medication orders);
2. turns it into facts (the vital-sign panels of the window with their values, the
   laboratory results, new orders written in the window, stopped ones), de-identified
   (dates relative to the admission, names as placeholders);
3. asks the model for the four SBAR sections, every statement citing ids from the
   list, and for the topics with nothing in the facts (`not_recorded`: intake and
   output, which the record does not hold, or no results, tasks, ...) instead of
   "not recorded" statements, which would have nothing to cite; ids outside the list
   get one more try;
4. validates (HandoffValidator: sources, drugs; numbers not in the cited sources are
   a warning; a topic declared not recorded must have no input) and writes the draft
   `sbar-<encounter>-<shift>`. The nurse confirms it,
   which makes it final: one signature, no co-signature.
Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.fhir.dt import fhir_datetime, parse
from app.modules.documentation import document as D
from app.modules.documentation import facts as F
from app.modules.documentation import mocks  # noqa: F401  (registers the mock models)
from app.modules.documentation.deid import Deid
from app.modules.documentation.models import GAP_LABELS, Handoff, ValidationReport
from app.modules.documentation.validator import HandoffValidator

AGENT_ID = "nursing_handoff"
KIND = "handoff"
WINDOW = timedelta(hours=12)
UNIT_NAMES = {"MEDA": "Medicine A", "MEDB": "Medicine B", "SURG": "Surgery", "ORTH": "Orthopedics",
              "ICU": "the ICU", "ED": "the emergency department"}
STOPPED = ("stopped", "cancelled", "completed", "entered-in-error")


@dataclass
class HandoffResult:
    status: Literal["drafted", "exists", "refused", "unavailable"]
    run_id: str
    document_id: str | None = None
    reason: str | None = None
    report: ValidationReport | None = None
    problems: list[str] = field(default_factory=list)


def _code(concept: dict | None) -> str:
    return ((concept or {}).get("coding") or [{}])[0].get("code") or ""


def _display(concept: dict | None) -> str:
    concept = concept or {}
    return concept.get("text") or ((concept.get("coding") or [{}])[0].get("display")) or ""


def build_facts(resources: list[dict], at: datetime) -> tuple[dict, dict[str, F.Source]]:
    """The model's facts and the validator's sources for the 12 hours before `at`."""
    since = at - WINDOW
    encounter = next(r for r in resources if r["resourceType"] == "Encounter")
    enc_ref = f"Encounter/{encounter['id']}"
    admitted = parse((encounter.get("period") or {}).get("start"))
    sources: dict[str, F.Source] = {enc_ref: F.Source(enc_ref, "Encounter", "encounter",
                                                      name=", ".join(_display(c) for c in encounter.get("reasonCode") or []),
                                                      detail=f"Stay {encounter['id']}, admitted "
                                                             f"{(admitted.date().isoformat() if admitted else '?')}"
                                                             + (f", day {(at.date() - admitted.date()).days + 1} of the "
                                                                f"stay" if admitted else ""))}
    unit = F.unit_of(encounter) or ""
    facts: dict = {"encounter": enc_ref, "unit": unit, "unit_name": UNIT_NAMES.get(unit, unit),
                   "admitted": admitted.date().isoformat() if admitted else "",
                   "stay_day": (at.date() - admitted.date()).days + 1 if admitted else None,
                   "window": {"from": since.strftime("%H:%M"), "to": at.strftime("%H:%M")},
                   "diagnoses": [], "procedures": [], "medications": [], "vital_signs": [], "results": [],
                   "tasks": [], "flags": [], "new_orders": [], "stopped_orders": []}
    from app.modules.documentation.lexicon import synonyms

    for r in sorted(resources, key=lambda r: (r["resourceType"], r.get("effectiveDateTime") or "", r["id"])):
        ref, rtype = f"{r['resourceType']}/{r['id']}", r["resourceType"]
        if rtype == "Condition":
            name, code = _display(r.get("code")), _code(r.get("code"))
            sources[ref] = F.Source(ref, rtype, "diagnosis", name=name, code=code,
                                    names=[name, *synonyms("condition", code)], detail=name)
            facts["diagnoses"].append({"id": ref, "name": name, "code": code})
        elif rtype == "Procedure":
            name, code = _display(r.get("code")), _code(r.get("code"))
            day = ((r.get("performedPeriod") or {}).get("start") or "")[:10]
            sources[ref] = F.Source(ref, rtype, "procedure", name=name, code=code,
                                    names=[name, *synonyms("procedure", code)], date=day, detail=f"{name} {day}")
            facts["procedures"].append({"id": ref, "name": name, "code": code, "date": day})
        elif rtype == "MedicationRequest":
            name, code = _display(r.get("medicationCodeableConcept")), _code(r.get("medicationCodeableConcept"))
            dose = ((r.get("dosageInstruction") or [{}])[0]).get("text") or ""
            sources[ref] = F.Source(ref, rtype, "medication", name=name, code=code,
                                    names=[name, *synonyms("medication", code)], detail=f"{name} {dose} ({r.get('status')})",
                                    category=r.get("intent") or "")
            authored = parse(r.get("authoredOn"))
            row = {"id": ref, "name": name, "rxcui": code, "dose": dose}
            if r.get("status") == "active" and r.get("intent") == "order":
                facts["medications"].append(row)
                if authored and authored >= since:
                    facts["new_orders"].append(row)
            elif r.get("status") in STOPPED and r.get("intent") == "order":
                ended = parse(((r.get("dispenseRequest") or {}).get("validityPeriod") or {}).get("end"))
                if ended and ended >= since:
                    facts["stopped_orders"].append(row)
        elif rtype == "Observation":
            when = parse(r.get("effectiveDateTime"))
            if when is None or when < since or when > at:
                continue
            if F._is_vital_panel(r):
                line = F.vitals_line(r)
                sources[ref] = F.Source(ref, rtype, "observation", name="vital signs", date=when.date().isoformat(),
                                        detail=f"Vital signs {when:%H:%M}: {line}")
                facts["vital_signs"].append({"id": ref, "time": when.strftime("%H:%M"), "values": F._vitals(r),
                                             "line": line})
            else:
                name = F.LAB_SHORT.get(_code(r.get("code"))) or _display(r.get("code"))
                value = F.lab_value(r)
                sources[ref] = F.Source(ref, rtype, "result", name=name, code=_code(r.get("code")),
                                        date=when.date().isoformat(), detail=f"{name} {value} at {when:%H:%M}")
                facts["results"].append({"id": ref, "name": name, "value": value, "flag": F._interpretation(r) or "N",
                                         "time": when.strftime("%H:%M")})
        elif rtype == "Task":
            name = _display(r.get("code")) or _code(r.get("code"))
            sources[ref] = F.Source(ref, rtype, "task", name=name, detail=r.get("description") or name)
            facts["tasks"].append({"id": ref, "name": name, "description": r.get("description") or name,
                                   "priority": r.get("priority") or "routine"})
        elif rtype == "Flag":
            name = _display(r.get("code"))
            sources[ref] = F.Source(ref, rtype, "flag", name=name, detail=name)
            facts["flags"].append({"id": ref, "name": name})
    return facts, sources


def render_text(content: dict, facts: dict, *, shift: str, final: bool = False) -> str:
    lines = [f"NURSING HANDOFF (SBAR), shift {shift}" + ("" if final else " - DRAFT (AI-assisted; confirm to finalise)"),
             f"{facts.get('encounter')}, {facts.get('unit')}, window {facts['window']['from']}-{facts['window']['to']}",
             "", "S - Situation: " + ((content.get("situation") or {}).get("text") or "")]
    for key, title in (("background", "B - Background"), ("assessment", "A - Assessment"),
                       ("recommendation", "R - Recommendation")):
        lines += ["", f"{title}:"] + [f"  - {s.get('text')}" for s in content.get(key) or []]
        if key == "assessment" and content.get("not_recorded"):
            lines.append("  - Not recorded: " + ", ".join(GAP_LABELS.get(t, t) for t in content["not_recorded"]) + ".")
    return "\n".join(lines)


def draft(run: AgentRun, at: datetime) -> HandoffResult:
    """Draft (or redraft) the shift's handoff; the service decides whether one is needed."""
    result = HandoffResult("drafted", run.run_id)
    encounter_id = run.encounter_id
    shift = D.shift_key(at)
    doc_id = D.document_id(KIND, encounter_id, shift)
    ctx = run.gather(("getHandoffContext", {"encounter_id": encounter_id, "since": fhir_datetime(at - WINDOW)}))
    if not ctx.ok:
        refused = ctx.refused[0]
        result.status, result.reason = "refused", f"{refused.reason}: {refused.detail}"
        return result
    resources = (ctx.results[0].output or {}).get("resources", [])
    facts, sources = build_facts(resources, at)
    admitted = parse((next(r for r in resources if r["resourceType"] == "Encounter").get("period") or {}).get("start"))
    deid = Deid(ctx, admitted.date() if admitted else None)
    prompt = load_prompt(run.spec.prompt_version)
    allowed = sorted(sources)
    variables = {"facts": json.dumps(deid.value("", facts), ensure_ascii=False, separators=(",", ":")),
                 "allowed_ids": json.dumps(allowed), "feedback": ""}
    content = None
    for attempt in range(2):
        outcome = run.model(prompt, variables, Handoff, context=ctx)
        if outcome.status != "ok":
            result.status, result.reason = "unavailable", f"model {outcome.status}"
            return result
        content = Handoff.model_validate(outcome.data).model_dump(mode="json")
        bad = [r for section in ("situation", "background", "assessment", "recommendation")
               for s in ([content[section]] if section == "situation" else content[section])
               for r in s.get("source_refs") or [] if r not in sources]
        if not bad:
            break
        result.problems += [f"attempt {attempt + 1}: {r} is not an allowed id" for r in bad[:10]]
        variables["feedback"] = "\nYour previous answer cited ids that are not allowed:\n- " + "\n- ".join(bad[:10])
    report = HandoffValidator(sources).validate(content)
    result.report = report
    payload = {"schema": D.SCHEMAS[KIND], "kind": KIND, "encounter_id": encounter_id, "mode": "ai", "shift": shift,
               "at": at.isoformat(), "content": content, "ai_draft": content, "facts": facts,
               "validation": report.model_dump(mode="json"), "raw_validation": report.model_dump(mode="json"),
               "sources": [{"ref": s.ref, "type": s.rtype, "kind": s.kind, "name": s.name, "code": s.code,
                            "names": s.names, "date": s.date, "detail": s.detail, "category": s.category}
                           for s in sources.values()],
               "run_id": run.run_id, "prompt_version": prompt.version, "problems": result.problems, "edits": []}
    out = run.tool("draftClinicalDocument", {
        "encounter_id": encounter_id, "kind": KIND, "document_id": doc_id,
        "title": f"Nursing handoff (SBAR) {shift} (draft)",
        "texts": [{"language": "en", "text": render_text(content, facts, shift=shift)}],
        "data": json.dumps(payload, ensure_ascii=False), "source_refs": allowed[:200],
        **({"sign_block": report.summary()} if report.blocking else {})})
    if not out.ok:
        result.status, result.reason = "refused", f"{out.reason}: {out.detail}"
    else:
        result.document_id = out.output["document_id"]
    return result
