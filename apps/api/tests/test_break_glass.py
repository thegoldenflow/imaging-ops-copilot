"""WP4 (spec 6.3): break-glass emergency access, end to end through the API.

Reason required (at least 10 characters), a 4-hour grant per user and patient,
a `break_glass` audit event, reads under the grant marked in their audit reason,
the admin's 24-hour review queue, and the review written back to the audit log.
"""

from datetime import datetime, timedelta

import pytest

from app.core.audit import mrn_hash
from app.core.models import Role
from app.ehr import breakglass
from app.ehr.codes import MRN_SYSTEM
from app.ehr.gateway import Actor, BreakGlassRequired, FhirGateway, LocalBackend
from app.fhir.dt import ref_id


def _physician(store):
    return next(u for u in store.staff.values() if u.role == Role.PHYSICIAN and u.demo_login)


def _icu_patient(store) -> tuple[str, str]:
    """(patient id, MRN) of an ICU inpatient: outside the demo physician's unit (Medicine A)."""
    for enc in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="ICU"):
        pid = ref_id(enc["subject"])
        if not any(e["status"] in ("arrived", "triaged", "in-progress") and e["id"] != enc["id"]
                   for e in store.fhir.search("Encounter", patient=pid)):
            patient = store.fhir.read("Patient", pid)
            return pid, next(i["value"] for i in patient["identifier"] if i["system"] == MRN_SYSTEM)
    raise AssertionError("no ICU inpatient")


def test_break_glass_flow_through_the_api(fresh_state, client, login):
    store = fresh_state
    doctor = _physician(store)
    pid, mrn = _icu_patient(store)
    headers = login(doctor.id)

    r = client.get(f"/api/hospital/patients/{mrn}", headers=headers)
    assert r.status_code == 403 and r.json()["code"] == "break_glass_required"
    denied = store.audit.events()[-1]
    assert (denied.outcome, denied.patient_mrn_hash, denied.user_id) == ("denied", mrn_hash(mrn), doctor.id)

    r = client.post("/api/hospital/break-glass", headers=headers, json={"mrn": mrn, "reason": "too short"})
    assert r.status_code == 422 and "10 characters" in r.json()["detail"]
    r = client.post("/api/hospital/break-glass", headers=headers,
                    json={"mrn": mrn, "reason": "Rapid response call on the ICU, covering for the attending"})
    assert r.status_code == 200
    grant = r.json()
    assert datetime.fromisoformat(grant["expires_at"]) - datetime.fromisoformat(grant["granted_at"]) == timedelta(hours=4)
    event = next(e for e in reversed(store.audit.events()) if e.event_type == "break_glass")
    assert (event.user_id, event.resource_id, event.patient_mrn_hash) == (doctor.id, pid, mrn_hash(mrn))
    assert grant["id"] in event.reason and "Rapid response" in event.reason

    r = client.get(f"/api/hospital/patients/{mrn}", headers=headers)
    assert r.status_code == 200
    chart = r.json()
    assert chart["break_glass"]["id"] == grant["id"] and {"encounter", "observations"} <= set(chart["sections"])
    reads = store.audit.query(user_id=doctor.id, patient_mrn_hash=mrn_hash(mrn), event_type="read", outcome="allowed")
    assert reads and all(f"break-glass {grant['id']}" in e.reason for e in reads)

    active = client.get("/api/hospital/break-glass/active", headers=headers).json()["grants"]
    assert [(g["id"], g["mrn"]) for g in active] == [(grant["id"], mrn)] and 0 < active[0]["minutes_left"] <= 240

    # The admin's review queue: due within 24 hours, with what was opened under the grant.
    admin = login("U-ADMIN")
    assert client.get("/api/admin/break-glass", headers=headers).status_code == 403
    queue = client.get("/api/admin/break-glass", headers=admin).json()["grants"]
    item = next(g for g in queue if g["id"] == grant["id"])
    assert item["review_status"] == "pending" and not item["overdue"] and "mrn" not in item
    assert datetime.fromisoformat(item["review_due"]) - datetime.fromisoformat(item["granted_at"]) == timedelta(hours=24)
    assert item["accessed_count"] >= 2 and "Patient" in item["accessed"]
    r = client.post(f"/api/admin/break-glass/{grant['id']}/review", headers=admin,
                    json={"decision": "justified", "note": "Rapid response confirmed in the ICU log"})
    assert r.status_code == 200 and r.json()["review_status"] == "justified"
    review = store.audit.events()[-1]
    assert (review.event_type, review.outcome, review.resource_id, review.user_id) == (
        "break_glass", "justified", grant["id"], "U-ADMIN")
    assert client.post(f"/api/admin/break-glass/{grant['id']}/review", headers=admin,
                       json={"decision": "not_justified"}).status_code == 409  # reviewed once
    assert all(g["id"] != grant["id"] for g in client.get("/api/admin/break-glass", headers=admin).json()["grants"])
    assert store.audit.verify()[0]


def test_the_grant_is_per_user_and_patient_and_ends_after_four_hours(fresh_state):
    store = fresh_state
    doctor = _physician(store)
    pid, mrn = _icu_patient(store)
    grant = breakglass.request_access(store, user_id=doctor.id, user_name=doctor.name, role="physician",
                                      patient_id=pid, mrn=mrn, reason="Cardiac arrest on the ICU")
    FhirGateway(Actor.of(doctor), "patient_chart", LocalBackend(store)).get_patient(mrn)  # open now
    nurse = next(u for u in store.staff.values() if u.role == Role.NURSE and u.demo_login)
    with pytest.raises(BreakGlassRequired):  # another user's grant opens nothing for them
        FhirGateway(Actor.of(nurse), "patient_chart", LocalBackend(store)).get_patient(mrn)
    breakglass.grants(store)[grant.id] = grant.model_copy(update={
        "granted_at": grant.granted_at - timedelta(hours=4, minutes=1),
        "expires_at": grant.expires_at - timedelta(hours=4, minutes=1)})
    with pytest.raises(BreakGlassRequired):
        FhirGateway(Actor.of(doctor), "patient_chart", LocalBackend(store)).get_patient(mrn)
    item = next(g for g in breakglass.queue(store) if g["id"] == grant.id)
    assert not item["active"] and not item["overdue"]
    late = datetime.now() + timedelta(hours=25)
    assert next(g for g in breakglass.queue(store, now=late) if g["id"] == grant.id)["overdue"]


def test_who_may_break_the_glass(fresh_state, client, login):
    store = fresh_state
    pid, mrn = _icu_patient(store)
    body = {"mrn": mrn, "reason": "Emergency consult requested by the ICU"}
    for user_id in ("U-OPS", "U-ADMIN", "U-RAD"):
        assert client.post("/api/hospital/break-glass", headers=login(user_id), json=body).status_code == 403
    pharmacist = next(u for u in store.staff.values() if u.role == Role.PHARMACIST and u.demo_login)
    assert client.post("/api/hospital/break-glass", headers=login(pharmacist.id), json=body).status_code == 403
    doctor = _physician(store)
    medicine = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="MEDA"))
    own_mrn = next(i["value"] for i in store.fhir.read("Patient", ref_id(medicine["subject"]))["identifier"]
                   if i["system"] == MRN_SYSTEM)
    r = client.post("/api/hospital/break-glass", headers=login(doctor.id), json={**body, "mrn": own_mrn})
    assert r.status_code == 409  # already one of the unit's patients
    assert client.post("/api/hospital/break-glass", headers=login(doctor.id),
                       json={**body, "mrn": "00000000"}).status_code == 404
    with pytest.raises(breakglass.BreakGlassError):
        breakglass.review(store, breakglass.request_access(
            store, user_id="U-ADMIN", user_name="Casey Brooks", role="admin", patient_id=pid, mrn=mrn,
            reason="Testing the self-review rule").id, decision="justified", note="", reviewer_id="U-ADMIN",
            reviewer_name="Casey Brooks", reviewer_role="admin")


def test_the_reason_and_mrn_are_encrypted_at_rest(fresh_state):
    from sqlalchemy import text

    store = fresh_state
    doctor = _physician(store)
    pid, mrn = _icu_patient(store)
    breakglass.request_access(store, user_id=doctor.id, user_name=doctor.name, role="physician", patient_id=pid,
                              mrn=mrn, reason="Sepsis alert, attending unreachable")
    store.flush()
    row = store.conn().execute(text("SELECT reason, mrn FROM break_glass_grants")).first()
    assert "Sepsis" not in row.reason and mrn not in row.mrn
