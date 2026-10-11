"""The storyline's cards: the ED's decision support and the 8.1 roadmap stubs (spec 8.2 steps 2, 3, 4, 8, 12).

Each card is a Task in a person's review queue, written through the Tool Gateway by a registered module, so it is
audited, traced and idempotent like every other AI output:

| step | card | module (registry) | tier | accepted by | on acceptance |
| --- | --- | --- | --- | --- | --- |
| 2 | triage suggestion: CTAS level and admission probability | `ed_decision_support` | clinical_ds | nurse | the EHR records the triage (app/ehr/accepted.py) |
| 3 | imaging orders: urgency and protocol per exam, from the imaging centre's own pipeline | `ed_decision_support` | clinical_ds | physician | the EHR places the orders; the imaging centre records the ordering physician's confirmation |
| 4 | critical value: INR 3.8 (8.1 stub) | `critical_value_routing` | clinical_ds | physician | acknowledged (audit) |
| 8 | NEWS2 7: the Flag, and a nurse card when no journey workflow opens one (8.1 stub) | `deterioration_alert` | clinical_ds | nurse | the response is recorded (audit) |
| 12 | coding suggestion: ICD-10-CA and CCI (8.1 stub) | `coding_assist` | documentation | clerk | accepted (audit); a card only |

The three 8.1 stubs run on preset data and contain no clinical logic: the INR threshold, the NEWS2 score and the
codes are the storyline's (script.py), and every stub card says it is a roadmap item. WP10's roadmap cards page can
point at this table and the registry entries.

Every `ensure_*` is safe to call twice (the storyline's event subscriber and the patient's journey workflow both
call them): it returns the card that exists, else writes it under an advisory lock, and registers the journey's wait
(`bridge.register_wait`) so the workflow's sign-off step resumes when the card is decided.
"""

from __future__ import annotations

import json
import zlib
from datetime import date

from sqlalchemy import text

from app.agents import approvals
from app.agents.runtime import runtime
from app.core.context import acting_as_system
from app.core.store import Store
from app.ehr.clock import hospital_now
from app.ehr.gateway import Actor, FhirGateway
from app.fhir.dt import parse
from app.modules.storyline import script as S

ED = "ed_decision_support"
CRITICAL = "critical_value_routing"
DETERIORATION = "deterioration_alert"
CODING = "coding_assist"
STEP_OF = {"triage": "triageAssist", "imaging_order": "imagingOrders", "critical": "criticalValue",
           "news2": "wardMonitoring", "coding": "codingStub"}
PRIORITY_OF = {"P1": "stat", "P2": "asap", "P3": "urgent", "P4": "routine"}
KIND_OF_MODULE = {CRITICAL: "critical", DETERIORATION: "news2", CODING: "coding"}
MODULE = "storyline"


def fhir() -> FhirGateway:
    """The storyline's reads (system actor, audited, purpose module storyline); its writes are its cards."""
    return FhirGateway(Actor.system(MODULE), MODULE)


def _read(rtype: str, rid: str) -> dict | None:
    found = fhir().read(rtype, rid)
    return found.to_fhir() if found is not None else None


def _lock(store: Store, key: str) -> None:
    if not store.detached:
        store.conn().execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": zlib.crc32(f"storyline:{key}".encode())})


def card_kind(task: dict) -> str | None:
    """The storyline card a Task is: the EHR action it carries, else the 8.1 stub module that wrote it."""
    values = approvals.inputs(task)
    if values.get("ehr_action"):
        return json.loads(values["ehr_action"]).get("kind")
    return KIND_OF_MODULE.get(approvals._agent_of(task) or "")


def find_card(store: Store, kind: str, patient: str = S.PATIENT) -> dict | None:
    for task in fhir().search("Task", patient=patient, code=approvals.REVIEW):
        if card_kind(task) == kind and task.get("status") != "cancelled":
            return task
    return None


def _wait(task_id: str, kind: str) -> None:
    from app.workflows import bridge

    bridge.register_wait(f"Task/{task_id}", S.JOURNEY, STEP_OF[kind])


def _card(module: str, kind: str, tool: str, args: dict, *, encounter: str | None) -> str | None:
    """Write the card through the Tool Gateway as the module itself; returns the Task id (None: refused)."""
    with acting_as_system(), runtime.start(module, user=None, encounter_id=encounter) as run:
        result = run.tool(tool, args)
        run.finish(outcome="completed" if result.ok else result.status)
    if not result.ok:
        return None
    task_id = result.output["task_id"]
    _wait(task_id, kind)
    return task_id


# ---------- step 2: triage ----------


def ensure_triage_card(store: Store) -> str | None:
    from app.modules.control_tower import triage

    _lock(store, "triage")
    if (found := find_card(store, "triage")) is not None:
        return found["id"]
    plan = store.modules["hospital_plan"]
    visit = plan["visits"].get(S.VISIT)
    if visit is None or _read("Encounter", S.VISIT) is None:
        return None
    patient = _read("Patient", S.PATIENT) or {}
    vitals = {k: float(v) for k, v in visit["vitals"].items()}
    arrival = parse(visit["arrival"])
    birth = date.fromisoformat(patient.get("birthDate", "1954-01-01"))
    age = arrival.year - birth.year - ((arrival.month, arrival.day) < (birth.month, birth.day))
    home = [m["medicationCodeableConcept"]["coding"][0]["code"]
            for m in fhir().search("MedicationStatement", patient=S.PATIENT, status="active")]
    prior = sum(1 for e in fhir().search("Encounter", patient=S.PATIENT, cls="IMP")
                if arrival.replace(year=arrival.year - 1) <= parse(e["period"]["start"]) < arrival)
    suggestion = triage.suggest(age=age, complaint=visit["complaint"], arrival_mode=visit["mode"], vitals=vitals,
                                home_meds=home, prior_admissions=prior)
    state = store.modules.get("storyline") or {}
    state.setdefault("marks", {})["triage_suggestion"] = {"ctas": suggestion["ctas"],
                                                          "admission": suggestion["admission"]}
    store.modules["storyline"] = state
    args = {"patient_id": S.PATIENT, "encounter_id": S.VISIT, "reviewer_role": "nurse",
            "summary": f"Triage suggestion: CTAS {suggestion['ctas']} for an ambulance arrival after a fall",
            "recommendation": triage.card_text(suggestion, vitals),
            "action": {"kind": "triage", "visit": S.VISIT, "ctas": suggestion["ctas"], "vitals": visit["vitals"],
                       "flags": ["fall-risk"]},
            "evidence_refs": [f"Encounter/{S.VISIT}", "MedicationStatement/medst-j0001-warfarin"],
            "priority": "urgent"}
    return _card(ED, "triage", "suggestEhrAction", args, encounter=S.VISIT)


# ---------- step 3: imaging ----------


def _requisition_text(store: Store, code: str, exam: str) -> str:
    patient = store.patients[S.IMAGING_PATIENT]
    referrer = store.referrers[S.IMAGING_REFERRER]
    lines = ["REQUISITION FOR DIAGNOSTIC IMAGING (hospital order, Emergency Department)",
             f"Patient: {patient.full_name}    DOB: {patient.dob:%Y-%m-%d}    Health card: {patient.health_card} "
             f"{patient.health_card_version}",
             f"Exam requested: {exam}",
             "Clinical information: 72-year-old woman, fall at home today, unable to bear weight, left leg shortened "
             "and externally rotated. Query left hip fracture (neck of femur)." +
             (" Preoperative planning if displaced." if code == "CT_HIP" else ""),
             "Relevant history: Atrial fibrillation on warfarin (INR pending); hypertension; osteoporosis",
             "Allergies: No known drug allergies",
             "Previous imaging: None known",
             "Priority: URGENT - ED patient",
             f"Referring physician: {referrer.name}, {referrer.clinic}. Fax {referrer.fax}"]
    return "\n".join(lines)


def imaging_requests(store: Store) -> list[dict]:
    """The two requisitions at the imaging centre, run through its pipeline now (extraction, triage P1-P4,
    protocol suggestion: the imaging systems 5 and 6, their own agents and audit)."""
    from datetime import datetime

    from app.core.models import Requisition
    from app.modules.protocols import service as protocols
    from app.modules.requisitions import service as requisitions
    from app.modules.triage import service as triage

    out = []
    for rid, (code, exam) in S.IMAGING_REQUEST.items():
        req = store.requisitions.get(rid)
        if req is None:
            req = Requisition(id=rid, patient_id=S.IMAGING_PATIENT, referrer_id=S.IMAGING_REFERRER,
                              received_at=datetime.now(), channel="online_form", text=_requisition_text(store, code, exam))
            store.requisitions[rid] = req
        if req.status == "received":
            requisitions.process(store, req)
        tri = triage.records(store).get(rid)
        pro = protocols.records(store).get(rid)
        out.append({"requisition": rid, "code": code, "exam": exam, "priority": tri.ai_priority if tri else None,
                    "rationale": tri.ai_rationale if tri else None, "red_flags": tri.ai_red_flags if tri else [],
                    "protocol_id": pro.primary_id if pro else None, "protocol_rationale": pro.rationale if pro else None})
    return out


def ensure_imaging_card(store: Store) -> str | None:
    from app.modules.protocols.library import BY_ID

    _lock(store, "imaging")
    if (found := find_card(store, "imaging_order")) is not None:
        return found["id"]
    if _read("Encounter", S.VISIT) is None:
        return None
    requests = imaging_requests(store)
    priority = min((r["priority"] for r in requests if r["priority"]), default="P3")
    lines = [f"Urgency {priority} (AI triage at the imaging centre): {requests[0]['rationale'] or 'no rationale'}"]
    orders = []
    for r in requests:
        protocol = BY_ID.get(r["protocol_id"] or "")
        lines.append(f"- {r['exam']}: protocol {protocol.id if protocol else 'none'} "
                     f"({protocol.name if protocol else 'needs a radiologist'}); urgency {r['priority']}")
        orders.append({"code": r["code"], "priority": PRIORITY_OF.get(r["priority"] or "P3", "urgent"),
                       "protocol": f"{protocol.id}: {protocol.name}" if protocol else None,
                       "minutes": 25 if r["code"] == "XR_HIP" else 45, "conclusion": S.IMAGING_FINDINGS[r["code"]],
                       "requisition": r["requisition"]})
    lines.append(f"Requisitions {', '.join(r['requisition'] for r in requests)} for the imaging centre's record "
                 f"{S.IMAGING_PATIENT}, linked to MRN {S.MRN} by the preset MPI record (8.1 roadmap).")
    lines.append("Accepting places both orders in the EHR with this priority and protocol.")
    args = {"patient_id": S.PATIENT, "encounter_id": S.VISIT, "reviewer_role": "physician",
            "summary": f"Imaging orders: X-ray and CT of the left hip (urgency {priority})",
            "recommendation": "\n".join(lines),
            "action": {"kind": "imaging_order", "visit": S.VISIT, "orders": orders,
                       "requisitions": [r["requisition"] for r in requests]},
            "evidence_refs": [f"Encounter/{S.VISIT}"], "priority": PRIORITY_OF.get(priority, "urgent")}
    return _card(ED, "imaging_order", "suggestEhrAction", args, encounter=S.VISIT)


def confirm_imaging(store: Store, user_id: str) -> None:
    """The ED physician accepted the imaging card: the imaging centre records the confirmation of its AI urgency
    and the approval of the suggested protocol, by the ordering physician (audited as the radiologist's would be)."""
    from datetime import datetime

    from app.modules.protocols import service as protocols
    from app.modules.triage import service as triage

    user = store.staff.get(user_id)
    for rid in S.REQUISITIONS:
        req, tri, pro = store.requisitions.get(rid), triage.records(store).get(rid), protocols.records(store).get(rid)
        if req is None:
            continue
        if tri is not None and not tri.review_action:
            tri.review_action, tri.final_priority = "confirmed", tri.ai_priority or tri.final_priority
            tri.reviewed_by, tri.reviewed_at = (user.name if user else user_id), datetime.now()
        if pro is not None and not pro.approved_id:
            pro.approved_id, pro.approved_by, pro.approved_at = pro.primary_id, (user.name if user else user_id), \
                datetime.now()
        if req.status == "ready":
            req.status = "approved"
        store.touch()
        store.audit.record(user_id=user_id, user_name=user.name if user else user_id,
                           role=str(user.role) if user else "physician", action="approve", resource_type="protocol",
                           resource_id=rid, outcome="allowed", reason="ordering physician accepted the AI urgency "
                           "and protocol (hospital order)", module="ed_decision_support")


# ---------- step 4: critical value (8.1 stub) ----------


def ensure_critical_card(store: Store) -> str | None:
    _lock(store, "critical")
    if (found := find_card(store, "critical")) is not None:
        return found["id"]
    obs = _read("Observation", "obs-ord-ed-j0001-inr")
    if obs is None:
        return None
    value = (obs.get("valueQuantity") or {}).get("value")
    if value is None or value < S.INR_THRESHOLD:
        return None
    args = {"encounter_id": S.VISIT, "reviewer_role": "physician",
            "summary": f"Critical value: INR {value:g} before surgery (roadmap stub)",
            "recommendation": f"INR {value:g} at {parse(obs['effectiveDateTime']):%H:%M}: at or above the preset "
                              f"critical threshold {S.INR_THRESHOLD:g} for a patient going to surgery. Routed to the ED "
                              f"physician; acknowledge to confirm you have seen it.\n{S.ROADMAP_NOTE}",
            "evidence_refs": ["Observation/obs-ord-ed-j0001-inr", f"Encounter/{S.VISIT}"], "priority": "stat"}
    return _card(CRITICAL, "critical", "submitForReview", args, encounter=S.VISIT)


# ---------- step 8: NEWS2 (8.1 stub) ----------


def news2_alert(store: Store, observation_id: str) -> dict:
    """The preset NEWS2 score crossed the urgent-response line: a NEWS2 Flag (createFlag publishes flag.raised,
    which the patient's journey turns into a nurse Task); without a running journey, a nurse card instead."""
    from app.workflows import progress

    _lock(store, "news2")
    score = _read("Observation", observation_id)
    value = (score or {}).get("valueInteger")
    if value is None or value < S.NEWS2_SCORE:
        return {}
    flags = fhir().search("Flag", encounter=S.STAY, code="news2", status="active")
    out: dict = {"flag": flags[0]["id"] if flags else None}
    if not flags:
        with acting_as_system(), runtime.start(DETERIORATION, user=None, encounter_id=S.STAY) as run:
            result = run.tool("createFlag", {"encounter_id": S.STAY, "code": "news2",
                                             "display": f"NEWS2 {value}: urgent clinical review (roadmap stub)"})
            run.finish(outcome="completed" if result.ok else result.status)
        out["flag"] = result.output["flag_id"] if result.ok else None
    journey = progress.get(S.JOURNEY)
    if journey is None or journey.status != "running":
        if (found := find_card(store, "news2")) is not None:
            out["card"] = found["id"]
        else:
            out["card"] = _card(DETERIORATION, "news2", "submitForReview", {
                "encounter_id": S.STAY, "reviewer_role": "nurse",
                "summary": f"NEWS2 {value} at {parse(score['effectiveDateTime']):%H:%M}: assess now (roadmap stub)",
                "recommendation": f"NEWS2 {value} (preset score from the flowsheet; 7 or more needs an urgent "
                                  f"response): assess the patient, inform the physician and record the response.\n"
                                  f"{S.ROADMAP_NOTE}",
                "evidence_refs": [f"Observation/{observation_id}", f"Encounter/{S.STAY}"], "priority": "urgent"},
                encounter=S.STAY)
    return out


# ---------- step 12: coding (8.1 stub) ----------


def ensure_coding_card(store: Store) -> str | None:
    _lock(store, "coding")
    if (found := find_card(store, "coding")) is not None:
        return found["id"]
    summary = _read("DocumentReference", f"dsum-{S.STAY}")
    if summary is None or summary.get("docStatus") != "final":
        return None
    dx, secondary, proc = S.CODING["diagnosis"], S.CODING["secondary"], S.CODING["procedure"]
    lines = [f"Main diagnosis: {dx[0]} {dx[1]} ({dx[2]})"]
    lines += [f"Secondary: {c} {d} ({sys})" for c, d, sys in secondary]
    lines += [f"Procedure: {proc[0]} {proc[1]} ({proc[2]})", "From the signed discharge summary dsum-stay-j0001.",
              S.ROADMAP_NOTE]
    args = {"encounter_id": S.STAY, "reviewer_role": "clerk",
            "summary": f"Coding suggestion: {dx[0]} and {proc[0].split('.^')[0]} (roadmap stub)",
            "recommendation": "\n".join(lines), "evidence_refs": [f"DocumentReference/dsum-{S.STAY}"],
            "priority": "routine"}
    return _card(CODING, "coding", "submitForReview", args, encounter=S.STAY)


def hospital_time(store: Store) -> str:
    return hospital_now(store).isoformat()
