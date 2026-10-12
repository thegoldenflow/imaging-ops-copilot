"""The discharge summary agent (spec 7.3): a runtime agent that drafts, the DischargeSummaryValidator checks.

For one encounter, as the physician it works for (or as itself in the journey):

1. reads the encounter bundle and WP6's discharge medication table through its read
   tools (de-identified by the runtime; refused without the patient's ai_processing
   consent: the module then offers the empty template, 6.3);
2. builds the facts (facts.py) and stops with "insufficient information, missing X"
   when there is no diagnosis or no signed discharge medication table (no model
   call, nothing invented);
3. long-context strategy: one SEGMENT call per chunk of resources (by type, split
   when long), each given its id list, then one SYNTHESIS call over the segment notes
   and the table, citing only listed ids; dates go in as `[DATE_n:D+k]` tokens and
   names as placeholders, restored by the LLM gateway afterwards;
4. a reference outside the allowed ids gets one more try with the problem listed;
5. validates the restored draft (the raw hallucination rate is recorded) and writes
   it through draftClinicalDocument: one draft per encounter (`dsum-<encounter>`),
   with a sign block while any item is untraceable.

The model never decides what is true: the facts come from the record, the validator
checks every name against it, and the physician edits and signs.
Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.modules.documentation import document as D
from app.modules.documentation import facts as F
from app.modules.documentation import mocks  # noqa: F401  (registers the mock models)
from app.modules.documentation.deid import Deid
from app.modules.documentation.models import DischargeSummary, SegmentNotes, ValidationReport
from app.modules.documentation.validator import DischargeSummaryValidator

AGENT_ID = "discharge_summary"
KIND = "discharge_summary"


@dataclass
class DraftResult:
    status: Literal["drafted", "exists", "insufficient", "refused", "unavailable"]
    run_id: str
    document_id: str | None = None
    missing: list[str] = field(default_factory=list)
    reason: str | None = None
    raw_report: ValidationReport | None = None
    report: ValidationReport | None = None
    calls: int = 0
    seconds: float = 0.0
    problems: list[str] = field(default_factory=list)
    content: dict | None = None


def _resources(result, rtype: str | None = None) -> list[dict]:
    return [r for r in (result.output or {}).get("resources", []) if rtype is None or r.get("resourceType") == rtype]


def find_existing(run: AgentRun, encounter_id: str) -> dict | None:
    """This module's discharge summary of the encounter (draft or signed), if there is one."""
    docs = run.tool("getDocuments", {"encounter_id": encounter_id})
    if not docs.ok:
        return None
    wanted = D.document_id(KIND, encounter_id)
    return next((d for d in _resources(docs, "DocumentReference") if d["id"] == wanted and D.read_data(d)), None)


def sources_payload(inputs: F.Inputs) -> list[dict]:
    return [{"ref": s.ref, "type": s.rtype, "kind": s.kind, "name": s.name, "code": s.code, "names": s.names,
             "date": s.date, "detail": s.detail, "category": s.category} for s in inputs.sources.values()]


def sources_from(payload: list[dict]) -> dict[str, F.Source]:
    return {s["ref"]: F.Source(ref=s["ref"], rtype=s["type"], kind=s["kind"], name=s.get("name") or "",
                               code=s.get("code") or "", names=s.get("names") or [], date=s.get("date") or "",
                               detail=s.get("detail") or "", category=s.get("category") or "")
            for s in payload}


def inputs_payload(inputs: F.Inputs) -> dict:
    return {"admitted": inputs.admitted.isoformat() if inputs.admitted else None,
            "discharged": inputs.discharged.isoformat() if inputs.discharged else None,
            "unit": inputs.unit, "language": inputs.language, "disposition": inputs.disposition,
            "table": inputs.table, "chunks": [{"name": c.name, "title": c.title, "refs": c.refs} for c in inputs.chunks]}


def render_text(content: dict, inputs: dict, encounter_id: str, *, final: bool = False) -> str:
    """The readable document (text/plain attachment)."""
    def items(rows, fmt) -> list[str]:
        return [f"  - {fmt(r)}" for r in rows] or ["  - none recorded"]

    meds = content.get("medications") or {}
    lines = ["DISCHARGE SUMMARY" + ("" if final else " - DRAFT (AI-assisted; not signed)"),
             f"Encounter {encounter_id}; admitted {str(inputs.get('admitted') or '?')[:10]}"
             + (f", discharged {str(inputs['discharged'])[:10]}" if inputs.get("discharged") else ", in hospital"),
             "", "Reason for admission: " + ((content.get("reason_for_admission") or {}).get("text") or "not recorded"),
             "", "Hospital course:"]
    lines += items(content.get("hospital_course") or [], lambda s: f"{s.get('date') or '?'}: {s.get('text')}")
    lines += ["", "Discharge diagnoses:"] + items(content.get("discharge_diagnoses") or [], lambda d: d.get("name"))
    lines += ["", "Procedures:"] + items(content.get("procedures") or [],
                                         lambda p: f"{p.get('name')} ({p.get('date') or 'date not recorded'})")
    lines += ["", "Medications at discharge:"]
    for cat in ("new", "changed", "stopped", "continued"):
        rows = meds.get(cat) or []
        lines.append(f"  {cat.capitalize()}: " + ("; ".join(
            f"{m.get('name')} {m.get('dose') or ''}".strip() + (f" ({m['note']})" if m.get("note") else "")
            for m in rows) or "none"))
    lines += ["", "Pending results:"] + items(content.get("pending_results") or [], lambda r: r.get("text") or r.get("test"))
    lines += ["", "Follow-up:"] + items(content.get("follow_up") or [], lambda f: f.get("text"))
    lines += ["", "Return precautions (red flags):"] + items(content.get("red_flags") or [], lambda f: f.get("text"))
    lines += ["", "Every item cites the FHIR resources it comes from (structured content in the JSON attachment). "
                  "Synthetic data (demo)."]
    return "\n".join(lines)


def _check_refs(output: dict, allowed: set[str]) -> list[str]:
    bad = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for r in node.get("source_refs") or []:
                if r not in allowed:
                    bad.append(f"{path}: {r} is not an allowed id")
            for k, v in node.items():
                if k != "source_refs":
                    walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(output, "")
    return bad


def _call(run: AgentRun, prompt, variables: dict, schema, ctx, allowed: set[str], result: DraftResult):
    """One structured call; a reference outside `allowed` gets one more try with the problems listed."""
    data = None
    for attempt in range(2):
        result.calls += 1
        outcome = run.model(prompt, variables, schema, context=ctx)
        if outcome.status != "ok":
            return None, outcome.status
        data = outcome.data
        bad = _check_refs(data, allowed)
        if not bad:
            return data, "ok"
        result.problems += [f"{variables['stage'].lower()} attempt {attempt + 1}: {b}" for b in bad[:10]]
        variables = {**variables, "feedback": "\nYour previous answer cited ids that are not allowed; cite only the "
                                              "allowed ids:\n- " + "\n- ".join(bad[:10])}
    return data, "ok"


def draft(run: AgentRun, *, force: bool = False) -> DraftResult:
    started = time.monotonic()
    result = DraftResult("drafted", run.run_id)
    encounter_id = run.encounter_id
    if not force and (existing := find_existing(run, encounter_id)) is not None:
        result.status, result.document_id = "exists", existing["id"]
        return result
    ctx = run.gather(("getEncounterBundle", {"encounter_id": encounter_id}),
                     ("getDischargeMedications", {"encounter_id": encounter_id}))
    if not ctx.ok:
        refused = ctx.refused[0]
        result.status, result.reason = "refused", f"{refused.reason}: {refused.detail}"
        return result
    bundle, meds = ctx.results
    resources = _resources(bundle) + [r for r in _resources(meds) if r not in _resources(bundle)]
    inputs = F.build(resources, (meds.output or {}).get("table"))
    if inputs.missing:
        result.status, result.missing = "insufficient", inputs.missing
        result.seconds = time.monotonic() - started
        return result
    deid = Deid(ctx, inputs.admitted.date() if inputs.admitted else None)
    prompt = load_prompt(run.spec.prompt_version)
    allowed = set(inputs.ids())
    encounter = json.dumps({"id": encounter_id, "admitted": deid.day(inputs.admitted.date().isoformat())
                            if inputs.admitted else "", "discharged": deid.day(inputs.discharged.date().isoformat())
                            if inputs.discharged else "", "disposition": inputs.disposition,
                            "length_of_stay_days": F.stay_days(inputs)}, ensure_ascii=False)
    segments = []
    for chunk in inputs.chunks:
        body = (f"Chunk: {chunk.name} - {chunk.title}\nCHUNK_JSON:\n"
                f"{json.dumps(deid.facts(chunk.facts), ensure_ascii=False, separators=(',', ':'))}")
        data, status = _call(run, prompt, {"stage": "SEGMENT", "encounter": encounter, "body": body,
                                           "allowed_ids": json.dumps(chunk.refs), "feedback": ""},
                             SegmentNotes, ctx, set(chunk.refs), result)
        if data is None:
            result.status, result.reason = "unavailable", f"model {status} (segment {chunk.name})"
            result.seconds = time.monotonic() - started
            return result
        notes = [n for n in data.get("notes") or [] if all(r in chunk.refs for r in n.get("source_refs") or [])]
        segments.append({"chunk": chunk.name, "notes": notes})
    table = deid.value("", F.table_lines(inputs.table)) if inputs.table else None
    body = ("SEGMENTS_JSON:\n" + json.dumps(deid.value("", segments), ensure_ascii=False, separators=(",", ":"))
            + "\nTABLE_JSON:\n" + json.dumps(table, ensure_ascii=False, separators=(",", ":")))
    data, status = _call(run, prompt, {"stage": "SYNTHESIS", "encounter": encounter, "body": body,
                                       "allowed_ids": json.dumps(sorted(allowed)), "feedback": ""},
                         DischargeSummary, ctx, allowed, result)
    if data is None:
        result.status, result.reason = "unavailable", f"model {status} (synthesis)"
        result.seconds = time.monotonic() - started
        return result
    content = DischargeSummary.model_validate(data).model_dump(mode="json")
    validator = DischargeSummaryValidator(inputs.sources, inputs.table)
    report = validator.validate(content)
    result.raw_report = result.report = report
    result.content = content
    payload = {"schema": D.SCHEMAS[KIND], "kind": KIND, "encounter_id": encounter_id, "mode": "ai",
               "content": content, "ai_draft": content, "validation": report.model_dump(mode="json"),
               "raw_validation": report.model_dump(mode="json"), "sources": sources_payload(inputs),
               "inputs": inputs_payload(inputs), "run_id": run.run_id, "prompt_version": prompt.version,
               "model_calls": result.calls, "problems": result.problems, "edits": []}
    text = render_text(content, payload["inputs"], encounter_id)
    out = run.tool("draftClinicalDocument", {
        "encounter_id": encounter_id, "kind": KIND, "document_id": D.document_id(KIND, encounter_id),
        "title": "Discharge summary (draft)", "texts": [{"language": "en", "text": text[:30000]}],
        "data": json.dumps(payload, ensure_ascii=False),
        "source_refs": sorted(allowed)[:200],
        **({"sign_block": report.summary()} if report.blocking else {})})
    if not out.ok:
        result.status, result.reason = "refused", f"{out.reason}: {out.detail}"
    else:
        result.document_id = out.output["document_id"]
    result.seconds = time.monotonic() - started
    return result
