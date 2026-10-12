"""Documentation (spec 7.3): the worklists, drafting, review, editing, signing and delivery behind the API.

Everything about a patient is read and written as the signed-in person (FhirGateway
with their 6.3 rights: unit scope, break-glass, audit); the drafting runs the
registered agents for that person (agent runtime, Tool Gateway). Three documents:

- discharge summary (physician): draft with AI, or the empty template without the
  patient's ai_processing consent; the physician edits items (each re-validated),
  removes untraceable ones and signs; the signing service refuses a draft with a
  sign block (untraceable items), whatever path the signature takes;
- patient instructions (nurse): drafted from the signed summary only (by the nurse's
  request, the journey, or the background intake once the summary is final); the
  nurse adjusts codes, reviews both languages and signs; delivery mocks: print
  preview, SMS (consent sms), patient portal page;
- SBAR handoff (nurse): one per shift and patient; confirming it is the one
  signature that makes it final.

Signing a summary or instructions also confirms the journey's open sign-off Task for
that step (when the journey asked for it by hand), as WP6 does for the order review.
"""

from __future__ import annotations

import difflib
import json
import threading
from datetime import datetime, timedelta

from sqlalchemy import text

from app.agents import approvals, registry
from app.agents.runtime import RunRefused, runtime
from app.core.context import acting_as_system
from app.core.models import StaffUser
from app.core.store import Store, get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import MRN_SYSTEM
from app.ehr.consent import consent_status, documentation_mode, send_sms
from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway
from app.ehr.signing import signatures
from app.fhir.dt import fhir_datetime, parse
from app.modules.documentation import document as D
from app.modules.documentation import facts as F
from app.modules.documentation import handoff_agent, instructions_agent, summary_agent
from app.modules.documentation import instructions as I
from app.modules.documentation.models import DischargeSummary, Handoff, InstructionsContent, ValidationReport
from app.modules.documentation.validator import DischargeSummaryValidator, HandoffValidator

RECENT_DISCHARGE = timedelta(hours=72)
SYSTEM = Actor.system("documentation")
EDITORS = {"discharge_summary": "physician", "patient_instructions": "nurse", "handoff": "nurse"}
VIEW = "documentation"  # the purpose module of reads (audit); writes run as the document's module
MESSAGING = "patient_messaging"  # the registry entry whose writes are messages to patients (SMS, portal)
STEP_OF = {"discharge_summary": "draftDischargeSummary", "patient_instructions": "draftPatientInstructions"}
_intake = {"key": None}
_lock = threading.Lock()


class DocProblem(ValueError):
    """The request cannot be done in the document's state (409)."""


class DocDenied(PermissionError):
    """Not this person's to do (403)."""


def _fhir(user: StaffUser, module: str, request=None) -> FhirGateway:
    return FhirGateway(Actor.of(user, request), module)


def _system(module: str) -> FhirGateway:
    return FhirGateway(SYSTEM, module)


def _encounter(fhir: FhirGateway, encounter_id: str) -> dict:
    found = fhir.read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    return found.to_fhir()


def _document(fhir: FhirGateway, doc_id: str) -> dict:
    found = fhir.read("DocumentReference", doc_id)
    if found is None:
        raise LookupError(f"DocumentReference/{doc_id} not found")
    doc = found.to_fhir()
    if D.kind_of(doc) is None:
        raise LookupError(f"DocumentReference/{doc_id} is not a documentation-module document")
    return doc


def _module(kind: str) -> str:
    return D.AGENTS[kind]


def _patient_name(fhir: FhirGateway, patient_id: str | None) -> tuple[str | None, str | None, str]:
    patient = fhir.read("Patient", patient_id).to_fhir() if patient_id else None
    if not patient:
        return None, None, "en"
    names = patient.get("name") or []
    official = next((n for n in names if n.get("use") in (None, "official")), names[0] if names else {})
    name = official.get("text") or " ".join([*(official.get("given") or []), official.get("family") or ""]).strip()
    mrn = next((i.get("value") for i in patient.get("identifier") or [] if i.get("system") == MRN_SYSTEM), None)
    return name or None, mrn, F.language_of(patient)


def _now(store: Store) -> datetime:
    return hospital_now(store)


# ---------- inputs without AI (template mode, source panel) ----------


def read_inputs(fhir: FhirGateway, encounter_id: str) -> F.Inputs:
    """The discharge summary's inputs read as a person (no model involved): the same facts the agent sees."""
    from app.agents.handlers import BUNDLE_TYPES
    from app.modules.med_safety.discharge import discharge_medication_table

    encounter = _encounter(fhir, encounter_id)
    resources = [encounter]
    patient_id = access.patient_of(encounter)
    if patient_id:
        resources.append(fhir.read("Patient", patient_id).to_fhir())
    for rtype in BUNDLE_TYPES:
        if rtype in (fhir.policy.read if fhir.policy else access.ALL_TYPES) and rtype != "DocumentReference":
            resources += [r.to_fhir() for r in fhir.get_encounter_resources(rtype, encounter_id)]
    table = discharge_medication_table(fhir, encounter_id, final_only=False)
    return F.build(resources, table.model_dump(mode="json", by_alias=True) if table else None)


# ---------- views ----------


def _ai_flags(kind: str) -> dict:
    spec = registry.agent(_module(kind))
    return {"agent_id": _module(kind), "evaluated": bool(spec and spec.evaluated),
            "agent_version": spec.version if spec else None}


def _sources_view(payload: list[dict]) -> list[dict]:
    order = ["encounter", "diagnosis", "procedure", "medication", "medrec", "observation", "result", "report", "plan",
             "task", "pending", "flag"]
    return sorted(payload, key=lambda s: (order.index(s["kind"]) if s["kind"] in order else 99, s.get("date") or "",
                                          s["ref"]))


def document_view(store: Store, user: StaffUser, doc_id: str, request=None) -> dict:
    fhir = _fhir(user, VIEW, request)
    doc = _document(fhir, doc_id)
    kind = D.kind_of(doc)
    data = D.read_data(doc) or {}
    encounter_id = access.encounter_of(doc)
    patient_id = access.patient_of(doc)
    name, mrn, language = _patient_name(fhir, patient_id)
    final = doc.get("docStatus") == "final"
    report = data.get("validation") or {}
    blocking = any(i.get("severity") == "red" for i in report.get("issues") or [])
    role = str(user.role)
    signed = signatures(doc)
    can_edit = not final and role == EDITORS[kind]
    can_sign = can_edit and not blocking
    why_not = None
    if final:
        why_not = "Signed: final"
    elif role != EDITORS[kind]:
        why_not = f"Only a {EDITORS[kind]} signs this document"
    elif blocking:
        why_not = "Untraceable items are marked red: fix or remove them before signing"
    view = {"id": doc["id"], "kind": kind, "encounter_id": encounter_id, "patient_id": patient_id, "patient_name": name,
            "mrn": mrn, "patient_language": language, "doc_status": doc.get("docStatus"), "date": doc.get("date"),
            "mode": data.get("mode"), "content": data.get("content"), "validation": report,
            "raw_validation": data.get("raw_validation"), "blocking": blocking, "sign_block": D.sign_block(doc),
            "can_edit": can_edit, "can_sign": can_sign, "why_not": why_not,
            "signatures": signed, "authenticator": (doc.get("authenticator") or {}).get("display"),
            "edits": data.get("edits") or [], "run_id": data.get("run_id"), "prompt_version": data.get("prompt_version"),
            "notes": data.get("notes") or [], "problems": data.get("problems") or [], **_ai_flags(kind)}
    if kind in ("discharge_summary", "handoff"):
        view["sources"] = _sources_view(data.get("sources") or [])
    if kind == "discharge_summary":
        view["inputs"] = {k: v for k, v in (data.get("inputs") or {}).items() if k != "table"}
        view["table_status"] = ((data.get("inputs") or {}).get("table") or {}).get("status")
    if kind == "patient_instructions":
        content = InstructionsContent.model_validate(data["content"])
        items = data.get("summary_items") or {}
        conditions = [i.get("code") for p, i in items.items() if p.startswith("discharge_diagnoses") and i.get("code")]
        from app.modules.documentation.render import render

        view["renderings"] = {lang: render(content, lang, patient_conditions=conditions)
                              for lang in ("en", "zh-Hans", "zh-Hant")}
        view["default_language"] = data.get("default_language") or "en"
        view["summary"] = data.get("summary")
        view["summary_items"] = items
        view["deliveries"] = data.get("deliveries") or []
        view["consent_sms"] = consent_status(_system(_module(kind)), patient_id)["sms"] if patient_id else "missing"
        from app.modules.documentation import phrases as P

        view["menus"] = {"purposes": {k: v["en"] for k, v in P.PURPOSES.items()},
                         "times": {k: v["en"] for k, v in P.TIMES.items()},
                         "cautions": {k: v["en"] for k, v in P.CAUTIONS.items()},
                         "departments": {k: v["en"] for k, v in P.DEPARTMENTS.items()},
                         "red_flags": {k: v[0]["en"] for k, v in P.RED_FLAGS.items()},
                         "daily_care": {k: f"{v[1]['en']}" for k, v in P.DAILY_CARE.items()}}
    if kind == "handoff":
        view["shift"] = data.get("shift")
        view["facts"] = data.get("facts")
    return view


def _doc_status(doc: dict | None) -> str:
    if doc is None:
        return "none"
    if doc.get("docStatus") == "final":
        return "final"
    return "blocked" if D.sign_block(doc) else "draft"


def encounter_view(store: Store, user: StaffUser, encounter_id: str, request=None) -> dict:
    """What the documentation screens need about one encounter: the three documents' status and why not."""
    fhir = _fhir(user, VIEW, request)
    enc = _encounter(fhir, encounter_id)
    patient_id = access.patient_of(enc)
    name, mrn, language = _patient_name(fhir, patient_id)
    docs = {d.id: d.to_fhir() for d in fhir.get_documents(encounter_id)}
    summary = docs.get(D.document_id("discharge_summary", encounter_id))
    instructions = docs.get(D.document_id("patient_instructions", encounter_id))
    shift = D.shift_key(_now(store))
    handoff = docs.get(D.document_id("handoff", encounter_id, shift))
    from app.modules.med_safety.discharge import discharge_medication_table

    table = discharge_medication_table(fhir, encounter_id, final_only=False)
    mode = documentation_mode(_system("discharge_summary"), patient_id) if patient_id else None
    period = enc.get("period") or {}
    return {"encounter_id": encounter_id, "patient_id": patient_id, "patient_name": name, "mrn": mrn,
            "language": language, "status": enc.get("status"), "admitted": period.get("start"),
            "discharged": period.get("end"), "unit": F.unit_of(enc),
            "reason": ", ".join(F._display(c) for c in enc.get("reasonCode") or []),
            "ai_mode": mode.mode if mode else "template_only", "ai_mode_reason": mode.reason if mode else None,
            "discharge_table": table.status if table else "none",
            "summary": {"id": summary["id"], "status": _doc_status(summary)} if summary else None,
            "instructions": {"id": instructions["id"], "status": _doc_status(instructions)} if instructions else None,
            "handoff": {"id": handoff["id"], "status": _doc_status(handoff), "shift": shift} if handoff else None,
            "shift": shift}


def worklist(store: Store, user: StaffUser, request=None) -> list[dict]:
    """The user's units: inpatients and stays discharged in the last 72 hours, with their documents' status."""
    role = str(user.role)
    if role not in ("physician", "nurse"):
        raise DocDenied("Documentation is for physicians and nurses")
    fhir = _fhir(user, VIEW, request)
    now = _now(store)
    rows: dict[str, dict] = {}
    for unit in user.unit_ids:
        for entry in fhir.get_census(unit):
            if entry.encounter_class == "IMP":
                rows[entry.encounter_id] = {"encounter_id": entry.encounter_id, "patient_id": entry.patient_id,
                                            "mrn": entry.mrn, "name": entry.name, "bed": entry.bed_id,
                                            "status": "in-progress", "unit": unit}
    system = _system(VIEW)
    for enc in system.search("Encounter", cls="IMP", status="finished", date_from=now - timedelta(days=30)):
        end = parse((enc.get("period") or {}).get("end"))
        if end is None or end < now - RECENT_DISCHARGE or enc["id"] in rows:
            continue
        last = (enc.get("location") or [{}])[-1]
        unit = ((last.get("location") or {}).get("reference") or "").split("/")[-1].split("-")[0]
        attending = any((p.get("individual") or {}).get("reference") == f"Practitioner/{user.practitioner_id}"
                        for p in enc.get("participant") or [])
        if unit in user.unit_ids or attending:
            rows[enc["id"]] = {"encounter_id": enc["id"], "patient_id": access.patient_of(enc), "mrn": None,
                               "name": None, "bed": None, "status": "finished", "unit": unit,
                               "discharged": (enc.get("period") or {}).get("end")}
    if not rows:
        return []
    with system.batch("documentation worklist"):
        docs = system.search("DocumentReference", encounter=list(rows))
        plans = system.search("MedicationRequest", encounter=list(rows), category="plan")
        patients = {p["id"]: p for p in system.search("Patient", ids=sorted({r["patient_id"] for r in rows.values()
                                                                               if r["patient_id"]}))}
    shift = D.shift_key(now)
    by_enc: dict[str, dict[str, dict]] = {}
    for d in docs:
        by_enc.setdefault(access.encounter_of(d) or "", {})[d["id"]] = d
    leaving = {access.encounter_of(p) for p in plans if p.get("status") == "active"}
    out = []
    for enc_id, row in rows.items():
        p = patients.get(row["patient_id"]) or {}
        if row["mrn"] is None:
            row["mrn"] = next((i.get("value") for i in p.get("identifier") or [] if i.get("system") == MRN_SYSTEM), None)
        if row["name"] is None:
            n = next((n for n in p.get("name") or [] if n.get("use") in (None, "official")), {})
            row["name"] = n.get("text") or " ".join([*(n.get("given") or []), n.get("family") or ""]).strip() or None
        mine = by_enc.get(enc_id, {})
        medrec = next((d for d in mine.values() if d["id"] == f"medrec-{enc_id}-discharge"), None)
        row.update({
            "language": F.language_of(p),
            "discharge_planned": enc_id in leaving or row["status"] == "finished",
            "discharge_table": ("final" if medrec and medrec.get("docStatus") == "final" else
                                "awaiting_cosign" if medrec and signatures(medrec) else "in_review" if medrec else "none"),
            "summary": _doc_status(mine.get(D.document_id("discharge_summary", enc_id))),
            "instructions": _doc_status(mine.get(D.document_id("patient_instructions", enc_id))),
            "handoff": _doc_status(mine.get(D.document_id("handoff", enc_id, shift))) if row["status"] == "in-progress"
            else "n/a",
        })
        out.append(row)
    rank = {"blocked": 0, "draft": 1, "none": 2, "final": 3, "n/a": 4}
    key = (lambda r: (not r["discharge_planned"], r["discharge_table"] != "final" and r["summary"] == "none",
                      rank[r["summary"]], r["encounter_id"])) if role == "physician" else \
        (lambda r: (rank[r["instructions"]] if r["summary"] == "final" else 5, rank[r["handoff"]], r["encounter_id"]))
    return sorted(out, key=key)


# ---------- drafting ----------


def _run(agent_id: str, user: StaffUser | None, encounter_id: str):
    try:
        return runtime.start(agent_id, user=user, encounter_id=encounter_id)
    except RunRefused as e:
        raise DocDenied(str(e)) from e
    except registry.AgentNotDeployable as e:
        raise DocProblem(f"The AI is switched off for this agent ({e}); use the template") from e


def _require(user: StaffUser, role: str) -> None:
    if str(user.role) != role:
        raise DocDenied(f"This is the {role}'s to do, not the {user.role}'s")


def _existing(fhir: FhirGateway, doc_id: str) -> dict | None:
    found = fhir.read("DocumentReference", doc_id)
    return found.to_fhir() if found is not None else None


def _write_template(fhir: FhirGateway, kind: str, doc_id: str, encounter_id: str, patient_id: str, *, title: str,
                    texts: list[dict], payload: dict, block: str | None, sources: list[str], store: Store) -> dict:
    when = fhir_datetime(_now(store))
    doc = D.new_document(kind, doc_id, patient_id=patient_id, encounter_id=encounter_id, title=title, texts=texts,
                         data=payload, block=block, when=when, author="Template (no AI: the patient has not "
                                                                      "consented to AI processing)", sources=sources)
    return fhir.create(doc).to_fhir()


def draft_summary(store: Store, user: StaffUser, encounter_id: str, *, force: bool = False, request=None) -> dict:
    """AI draft (or the template without consent). Returns the document view, or {status: insufficient, missing}."""
    _require(user, "physician")
    fhir = _fhir(user, "discharge_summary", request)
    enc = _encounter(fhir, encounter_id)
    patient_id = access.patient_of(enc)
    doc_id = D.document_id("discharge_summary", encounter_id)
    _xact_lock(store, doc_id)
    current = _existing(fhir, doc_id)
    if current is not None and current.get("docStatus") == "final":
        raise DocProblem("The discharge summary is already signed")
    if current is not None and not force:
        return document_view(store, user, doc_id, request)
    mode = documentation_mode(_system("discharge_summary"), patient_id)
    if mode.mode == "template_only":
        if current is not None:
            return document_view(store, user, doc_id, request)
        inputs = read_inputs(fhir, encounter_id)
        if inputs.missing:
            return {"status": "insufficient", "missing": inputs.missing, "mode": "template"}
        content = DischargeSummary(reason_for_admission={"text": "", "source_refs": []}).model_dump(mode="json")
        report = DischargeSummaryValidator(inputs.sources, inputs.table).validate(content)
        payload = {"schema": D.SCHEMAS["discharge_summary"], "kind": "discharge_summary", "encounter_id": encounter_id,
                   "mode": "template", "template_reason": mode.reason, "content": content, "ai_draft": None,
                   "validation": report.model_dump(mode="json"), "raw_validation": None,
                   "sources": summary_agent.sources_payload(inputs), "inputs": summary_agent.inputs_payload(inputs),
                   "edits": []}
        _write_template(fhir, "discharge_summary", doc_id, encounter_id, patient_id,
                        title="Discharge summary (template)", payload=payload, block=None,
                        texts=[{"language": "en", "text": summary_agent.render_text(content, payload["inputs"],
                                                                                    encounter_id)}],
                        sources=list(inputs.sources)[:200], store=store)
        return document_view(store, user, doc_id, request)
    with _run(summary_agent.AGENT_ID, user, encounter_id) as run:
        result = summary_agent.draft(run, force=force)
        run.finish(outcome={"drafted": "completed", "exists": "completed", "insufficient": "needs_human"}.get(
            result.status, result.status))
    if result.status == "insufficient":
        return {"status": "insufficient", "missing": result.missing, "mode": "ai"}
    if result.status in ("refused", "unavailable"):
        raise DocProblem(f"The AI draft could not be written ({result.reason}); use the template")
    view = document_view(store, user, result.document_id, request)
    view["draft_seconds"] = round(result.seconds, 2)
    view["model_calls"] = result.calls
    return view


def draft_instructions(store: Store, user: StaffUser | None, encounter_id: str, *, force: bool = False,
                       request=None) -> dict:
    """From the signed summary: AI draft, or the template without consent. `user` None: the background intake."""
    if user is not None:
        _require(user, "nurse")
    fhir = _fhir(user, "patient_instructions", request) if user else _system("patient_instructions")
    enc = _encounter(fhir, encounter_id)
    patient_id = access.patient_of(enc)
    doc_id = D.document_id("patient_instructions", encounter_id)
    if user is not None:
        _xact_lock(store, encounter_id)  # the same key as the background intake's try-lock
    current = _existing(fhir, doc_id)
    if current is not None and (current.get("docStatus") == "final" or not force):
        return document_view(store, user, doc_id, request) if user else {"id": doc_id, "status": "exists"}
    summary = _existing(fhir, D.document_id("discharge_summary", encounter_id))
    if summary is None or summary.get("docStatus") != "final":
        return {"status": "insufficient", "missing": ["a discharge summary signed final by the physician"]}
    mode = documentation_mode(_system("patient_instructions"), patient_id)
    if mode.mode == "template_only":
        if user is None:
            return {"status": "template_only", "reason": mode.reason}
        data = D.read_data(summary) or {}
        items = I.summary_items(data.get("content") or {})
        content = I.template(items, summary_ref=f"DocumentReference/{summary['id']}", unit=F.unit_of(enc) or "")
        conditions = [i.get("code") for p, i in items.items() if p.startswith("discharge_diagnoses") and i.get("code")]
        report, renderings = I.validate_and_render(content, items, patient_conditions=conditions)
        _, _, language = _patient_name(fhir, patient_id)
        payload = instructions_agent.payload_for(content, summary=summary, items=items, language=language,
                                                 report=report, renderings=renderings, mode="template", run_id=None,
                                                 prompt_version=None, plan=None, notes=[mode.reason])
        _write_template(fhir, "patient_instructions", doc_id, encounter_id, patient_id,
                        title="Patient discharge instructions (template)", payload=payload,
                        block=report.summary() if report.blocking else None,
                        texts=instructions_agent.texts_for(renderings), sources=[f"DocumentReference/{summary['id']}"],
                        store=store)
        return document_view(store, user, doc_id, request)
    if user is None:
        with acting_as_system(), _run(instructions_agent.AGENT_ID, None, encounter_id) as run:
            result = instructions_agent.draft(run, force=force)
            run.finish(outcome="completed" if result.document_id else "needs_human")
        return {"id": result.document_id, "status": result.status, "reason": result.reason}
    with _run(instructions_agent.AGENT_ID, user, encounter_id) as run:
        result = instructions_agent.draft(run, force=force)
        run.finish(outcome="completed" if result.document_id else "needs_human")
    if result.status == "insufficient":
        return {"status": "insufficient", "missing": result.missing}
    if result.status in ("refused", "unavailable"):
        raise DocProblem(f"The AI draft could not be written ({result.reason}); use the template")
    return document_view(store, user, result.document_id, request)


def draft_handoff(store: Store, user: StaffUser, encounter_id: str, *, force: bool = False, request=None) -> dict:
    _require(user, "nurse")
    fhir = _fhir(user, "nursing_handoff", request)
    enc = _encounter(fhir, encounter_id)
    if enc.get("status") != "in-progress":
        raise DocProblem("Handoffs are for patients still in hospital")
    patient_id = access.patient_of(enc)
    now = _now(store)
    doc_id = D.document_id("handoff", encounter_id, D.shift_key(now))
    _xact_lock(store, doc_id)
    current = _existing(fhir, doc_id)
    if current is not None and (current.get("docStatus") == "final" or not force):
        return document_view(store, user, doc_id, request)
    mode = documentation_mode(_system("nursing_handoff"), patient_id)
    if mode.mode == "template_only":
        if current is not None:
            return document_view(store, user, doc_id, request)
        from app.modules.documentation.tools import handoff_resources  # the same read, as the nurse, no model

        resources = handoff_resources(fhir, encounter_id, fhir_datetime(now - handoff_agent.WINDOW),
                                      lambda rtype: fhir.policy is None or rtype in fhir.policy.read)
        facts, sources = handoff_agent.build_facts(resources, now)
        content = Handoff(situation={"text": "", "source_refs": []}).model_dump(mode="json")
        report = HandoffValidator(sources).validate(content)
        payload = {"schema": D.SCHEMAS["handoff"], "kind": "handoff", "encounter_id": encounter_id,
                   "mode": "template", "template_reason": mode.reason, "shift": D.shift_key(now),
                   "at": now.isoformat(), "content": content, "ai_draft": None, "facts": facts,
                   "validation": report.model_dump(mode="json"), "raw_validation": None,
                   "sources": [{"ref": s.ref, "type": s.rtype, "kind": s.kind, "name": s.name, "code": s.code,
                                "names": s.names, "date": s.date, "detail": s.detail, "category": s.category}
                               for s in sources.values()], "edits": []}
        _write_template(fhir, "handoff", doc_id, encounter_id, patient_id,
                        title=f"Nursing handoff (SBAR) {D.shift_key(now)} (template)", payload=payload, block=None,
                        texts=[{"language": "en", "text": handoff_agent.render_text(content, facts,
                                                                                   shift=D.shift_key(now))}],
                        sources=sorted(sources)[:200], store=store)
        return document_view(store, user, doc_id, request)
    with _run(handoff_agent.AGENT_ID, user, encounter_id) as run:
        result = handoff_agent.draft(run, now)
        run.finish(outcome="completed" if result.document_id else "needs_human")
    if result.status in ("refused", "unavailable"):
        raise DocProblem(f"The AI draft could not be written ({result.reason}); use the template")
    return document_view(store, user, result.document_id, request)


# ---------- editing ----------


def _flat_text(content: dict) -> str:
    return json.dumps(content, sort_keys=True, ensure_ascii=False)


def edit_ratio(ai: dict | None, final: dict) -> float | None:
    """How much the person changed the AI draft (0 = unchanged), like the imaging report's edit ratio."""
    if not ai:
        return None
    return round(1 - difflib.SequenceMatcher(None, _flat_text(ai), _flat_text(final)).ratio(), 3)


def _revalidate(kind: str, data: dict, content: dict) -> tuple[dict, ValidationReport, list[dict], dict]:
    """(normalised content, report, texts, extra payload) for edited content."""
    if kind == "discharge_summary":
        content = DischargeSummary.model_validate(content).model_dump(mode="json")
        sources = summary_agent.sources_from(data.get("sources") or [])
        report = DischargeSummaryValidator(sources, (data.get("inputs") or {}).get("table")).validate(content)
        texts = [{"language": "en", "text": summary_agent.render_text(content, data.get("inputs") or {},
                                                                       data.get("encounter_id") or "")}]
        return content, report, texts, {}
    if kind == "handoff":
        content = Handoff.model_validate(content).model_dump(mode="json")
        sources = summary_agent.sources_from(data.get("sources") or [])
        report = HandoffValidator(sources).validate(content)
        texts = [{"language": "en", "text": handoff_agent.render_text(content, data.get("facts") or {},
                                                                       shift=data.get("shift") or "")}]
        return content, report, texts, {}
    model = InstructionsContent.model_validate(content)
    items = data.get("summary_items") or {}
    conditions = [i.get("code") for p, i in items.items() if p.startswith("discharge_diagnoses") and i.get("code")]
    report, renderings = I.validate_and_render(model, items, patient_conditions=conditions)
    return (model.model_dump(mode="json", by_alias=True), report, instructions_agent.texts_for(renderings),
            {"readability": {lang: r["readability"] for lang, r in renderings.items()},
             "terms": renderings["zh-Hans"]["terms"]})


def save(store: Store, user: StaffUser, doc_id: str, content: dict, request=None) -> dict:
    """The editor's change: re-validated, the draft updated as the person (not the AI), the sign block set or lifted."""
    fhir = _fhir(user, VIEW, request)
    doc = _document(fhir, doc_id)
    kind = D.kind_of(doc)
    _require(user, EDITORS[kind])
    if doc.get("docStatus") != "preliminary":
        raise DocProblem("A signed document is not changed")
    data = D.read_data(doc) or {}
    try:
        content, report, texts, extra = _revalidate(kind, data, content)
    except ValueError as e:
        raise DocProblem(f"The content does not fit the document's structure: {str(e)[:300]}") from e
    now = _now(store)
    edits = [*(data.get("edits") or []), {"by": user.id, "name": user.name, "role": str(user.role),
                                         "at": now.isoformat(), "issues_red": len(report.red())}]
    data.update({"content": content, "validation": report.model_dump(mode="json"), "edits": edits[-50:],
                 "edit_ratio": edit_ratio(data.get("ai_draft") or data.get("ai_plan"), content), **extra})
    updated = D.apply_content(doc, texts=texts, data=data, title=doc.get("description") or D.KINDS[kind][2],
                              block=report.summary() if report.blocking else None, when=fhir_datetime(now))
    _fhir(user, _module(kind), request).update(updated)
    return document_view(store, user, doc_id, request)


# ---------- signing ----------


def _final_texts(kind: str, data: dict) -> list[dict]:
    content = data.get("content") or {}
    if kind == "discharge_summary":
        return [{"language": "en", "text": summary_agent.render_text(content, data.get("inputs") or {},
                                                                     data.get("encounter_id") or "", final=True)}]
    if kind == "handoff":
        return [{"language": "en", "text": handoff_agent.render_text(content, data.get("facts") or {},
                                                                     shift=data.get("shift") or "", final=True)}]
    return []


def confirm_journey_tasks(user: StaffUser, encounter_id: str, kind: str, *, source_ip: str | None = None) -> list[str]:
    """The journey's open by-hand sign-off Task for this step (insufficient information, no consent, the AI off) is
    confirmed by the signature (approvals.decide: an approve audit event, task.decided for the workflow)."""
    step = STEP_OF.get(kind)
    if step is None:
        return []
    done = []
    for task in approvals.queue(user, limit=200):
        if task["kind"] == approvals.WORKFLOW_SIGNOFF and task["encounter_id"] == encounter_id and (
                task.get("step") == step or task.get("agent_id") == _module(kind)):
            try:
                approvals.decide(user, task["task_id"], "approve", f"Signed in Documentation ({kind.replace('_', ' ')})",
                                 source_ip=source_ip)
            except (approvals.DecisionError, approvals.DecisionDenied):
                continue
            done.append(task["task_id"])
    return done


def sign(store: Store, user: StaffUser, doc_id: str, request=None) -> dict:
    """Re-validate, write the final text, then the signing service (which refuses a sign-blocked draft)."""
    fhir = _fhir(user, VIEW, request)
    doc = _document(fhir, doc_id)
    kind = D.kind_of(doc)
    _require(user, EDITORS[kind])
    if doc.get("docStatus") != "preliminary":
        raise DocProblem("Already signed")
    data = D.read_data(doc) or {}
    content, report, texts, extra = _revalidate(kind, data, data.get("content") or {})
    if report.blocking:
        raise DocProblem("Untraceable items block signing: " + "; ".join(
            f"{i.path}: {i.message}" for i in report.red()[:5]))
    now = _now(store)
    data.update({"content": content, "validation": report.model_dump(mode="json"), **extra,
                 "edit_ratio": edit_ratio(data.get("ai_draft") or data.get("ai_plan"), content),
                 "signed_at": now.isoformat()})
    final_texts = _final_texts(kind, data) or texts
    title = (doc.get("description") or D.KINDS[kind][2]).replace(" (draft)", "").replace(" (template)", "")
    updated = D.apply_content(doc, texts=final_texts, data=data, title=title, block=None, when=fhir_datetime(now))
    writer = _fhir(user, _module(kind), request)
    writer.update(updated)
    try:
        writer.sign_document(doc_id)
    except FhirAccessDenied as e:
        raise DocDenied(str(e)) from e
    confirm_journey_tasks(user, access.encounter_of(doc), kind, source_ip=getattr(getattr(request, "client", None),
                                                                                   "host", None))
    return document_view(store, user, doc_id, request)


# ---------- delivery (mocks) ----------


def portal_path(doc_id: str) -> str:
    return f"/documentation/portal/{doc_id}"


def deliver(store: Store, user: StaffUser, doc_id: str, channel: str, request=None) -> dict:
    """Print preview (a record that it was printed), SMS (needs the patient's sms consent; otherwise not sent and a
    clerk Task), or the patient portal page. Only signed instructions are delivered."""
    _require(user, "nurse")
    fhir = _fhir(user, MESSAGING, request)  # messages to the patient are the patient messaging module's writes
    doc = _document(fhir, doc_id)
    if D.kind_of(doc) != "patient_instructions":
        raise DocProblem("Only patient instructions are delivered")
    if doc.get("docStatus") != "final":
        raise DocProblem("Sign the instructions before they go to the patient")
    patient_id, encounter_id = access.patient_of(doc), access.encounter_of(doc)
    data = D.read_data(doc) or {}
    language = data.get("default_language") or "en"
    now = _now(store)
    out: dict = {"channel": channel, "language": language, "at": now.isoformat()}
    if channel == "sms":
        body = {"en": "Your discharge instructions are ready in the patient portal: {link}",
                "zh-Hans": "您的出院指导已发布到患者门户：{link}",
                "zh-Hant": "您的出院指導已發布到病人入口網站：{link}"}[language].format(
            link=f"https://portal.demo-hospital.example{portal_path(doc_id)}")
        outcome = send_sms(fhir, patient_id, body, encounter_id=encounter_id, at=now)
        out.update({"sent": outcome.sent, "reason": outcome.reason, "communication_id": outcome.communication_id,
                    "task_id": outcome.task_id})
    elif channel == "portal":
        comm = {"resourceType": "Communication", "status": "completed", "subject": doc["subject"],
                "encounter": {"reference": f"Encounter/{encounter_id}"}, "sent": fhir_datetime(now),
                "recipient": [doc["subject"]],
                "medium": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationMode",
                                        "code": "EMAILWRIT", "display": "Patient portal"}]}],
                "payload": [{"contentString": f"Discharge instructions published to the patient portal "
                                              f"({language}): {portal_path(doc_id)}"}],
                "about": [{"reference": f"DocumentReference/{doc_id}"}]}
        stored = fhir.create(comm)
        out.update({"sent": True, "communication_id": stored.id, "url": portal_path(doc_id)})
    elif channel == "print":
        get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="print",
                                 event_type="export", resource_type="DocumentReference", resource_id=doc_id,
                                 outcome="allowed", reason=f"patient instructions printed ({language})",
                                 encounter_id=encounter_id, module="patient_instructions")
        out.update({"sent": True})
    else:
        raise DocProblem("channel is print, sms or portal")
    return out


def portal_view(store: Store, user: StaffUser, doc_id: str, request=None) -> dict:
    """What the patient would see on the portal (mock): the signed instructions in the patient's language."""
    view = document_view(store, user, doc_id, request)
    if view["kind"] != "patient_instructions" or view["doc_status"] != "final":
        raise DocProblem("Only signed patient instructions are on the portal")
    return {"id": doc_id, "language": view["default_language"], "renderings": view["renderings"],
            "patient_name": view["patient_name"], "signed": view["signatures"]}


# ---------- demo ----------


def demo_cases() -> dict[str, str | None]:
    """The planted documentation cases' encounters (app/ehr/seed/documentation.py), for the demo and the e2e tests."""
    system = _system(VIEW)
    out: dict[str, str | None] = {}
    for name, (rtype, rid) in {"warfarin_zh": ("Procedure", "proc-doc-ablation"),
                               "hallucination": ("Condition", "cond-doc-htn-note")}.items():
        found = system.backend.read(rtype, rid)
        out[name] = access.encounter_of(found) if found else None
    pending = [s for s in system.backend.search("ServiceRequest", status="active") if s["id"].startswith("ord-doc-pending-")]
    out["discharged"] = access.encounter_of(pending[0]) if pending else None
    return out


# ---------- background: instructions once the summary is final ----------


def _xact_lock(store: Store, key: str) -> None:
    """Serialise drafting one document (a person's request, the journey, the background intake) until commit."""
    if not store.detached:
        store.conn().execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"documentation:{key}"})


def _try_lock(store: Store, key: str) -> bool:
    if store.detached:
        return True
    return bool(store.conn().execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                                     {"k": f"documentation:{key}"}).scalar())


def intake(store: Store, *, limit: int = 2) -> list[str]:
    """Signed discharge summaries without patient instructions get their AI draft (as the agent itself)."""
    key = f"{store.version}"
    with _lock:
        if _intake["key"] == key:
            return []
    system = _system(VIEW)
    done = []
    summaries = [d for d in system.search("DocumentReference", status="final", code="18842-5")
                 if d["id"].startswith("dsum-")]
    for doc in summaries:
        encounter_id = access.encounter_of(doc)
        if encounter_id is None or system.backend.read("DocumentReference",
                                                       D.document_id("patient_instructions", encounter_id)):
            continue
        if not _try_lock(store, encounter_id):
            continue
        try:
            out = draft_instructions(store, None, encounter_id)
        except (LookupError, DocProblem, DocDenied, registry.AgentNotDeployable):
            continue
        if out.get("id"):
            done.append(out["id"])
        if len(done) >= limit:
            return done
    with _lock:
        _intake["key"] = key
    return done


def reset_state() -> None:
    """Forget the intake's change key (tests start from the rolled-back seed)."""
    with _lock:
        _intake["key"] = None


def intake_step(store: Store) -> None:
    """Background step of the API's narrator loop."""
    intake(store)
