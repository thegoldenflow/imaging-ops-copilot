"""WP4 (spec 6.3): the audit log's event types and fields, the hash chain across events
written before and after the new fields, ai_call events from the LLM gateway, the admin
search and export, and the signing API."""

import hashlib
import json
from datetime import datetime

import pytest

from app.core.audit import GENESIS_HASH, _event, event_type_for, mrn_hash, verify_chain
from app.core.models import Role
from app.ehr.codes import MRN_SYSTEM
from app.fhir.dt import ref_id

LEGACY = {"user_id": "U-ADMIN", "user_name": "Casey Brooks", "role": "admin", "action": "read",
          "resource_type": "Patient", "resource_id": "PT-0001", "outcome": "allowed", "source_ip": None,
          "reason": "operations"}


def test_events_written_before_the_new_fields_still_verify():
    """An event from before WP4 has none of the new fields; its digest must be the pre-WP4 digest."""
    ts = datetime(2026, 10, 1, 9, 0)
    old = _event(0, GENESIS_HASH, LEGACY, ts)
    payload = {"seq": 1, "ts": ts.isoformat(timespec="seconds"), **LEGACY}
    pre_wp4 = hashlib.sha256((GENESIS_HASH + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()
    assert old.hash == pre_wp4 and old.event_type is None
    new = _event(1, old.hash, {**LEGACY, "event_type": "read", "patient_mrn_hash": "abc", "encounter_id": "stay-1",
                               "module": "patient_chart", "prompt_version": None}, ts)
    assert verify_chain([old, new]) == (True, None)
    tampered = new.model_copy(update={"module": "something_else"})
    assert verify_chain([old, tampered]) == (False, 2)  # the new fields are inside the digest once set
    dropped = new.model_copy(update={"patient_mrn_hash": None})
    assert verify_chain([old, dropped]) == (False, 2)


def test_event_types():
    assert [event_type_for(a) for a in ("read", "create", "update", "delete", "approve", "sign", "export",
                                        "simulator_event", "login", "knowledge_query")] == [
        "read", "write", "write", "write", "write", "sign", "export", "simulator_event", "login", "read"]


def test_unknown_event_types_are_refused(fresh_state):
    with pytest.raises(ValueError):
        fresh_state.audit.record(user_id="x", user_name="x", role="admin", action="read", resource_type="Patient",
                                 resource_id=None, event_type="peek")


def test_gateway_events_carry_module_patient_hash_and_encounter(fresh_state):
    from app.ehr.gateway import Actor, FhirGateway, LocalBackend

    store = fresh_state
    nurse = next(u for u in store.staff.values() if u.role == Role.NURSE and u.demo_login)
    enc = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="MEDA"))
    pid = ref_id(enc["subject"])
    mrn = next(i["value"] for i in store.fhir.read("Patient", pid)["identifier"] if i["system"] == MRN_SYSTEM)
    FhirGateway(Actor.of(nurse), "bedside_nursing", LocalBackend(store)).get_observations(enc["id"])
    event = store.audit.events()[-1]
    assert (event.event_type, event.module, event.encounter_id, event.patient_mrn_hash, event.role) == (
        "read", "bedside_nursing", enc["id"], mrn_hash(mrn), "nurse")
    assert event.reason == "" and mrn not in json.dumps(event.model_dump(), default=str)  # the MRN only as its hash
    assert store.audit.verify()[0]


def test_llm_calls_are_ai_call_events_with_the_requesting_user(fresh_state, client, login):
    store = fresh_state
    r = client.post("/api/clinical-kg/ask", headers=login("U-RAD"), json={"question": "What causes chest pain?"})
    assert r.status_code == 200
    calls = store.audit.query(event_type="ai_call")
    assert calls, "the clinical knowledge question went through the LLM gateway"
    last = calls[-1]
    assert (last.user_id, last.role, last.resource_type) == ("U-RAD", "radiologist", "LlmCall")
    assert last.module.startswith("clinical_kg") and last.prompt_version and last.outcome in ("ok", "retried_ok")
    assert last.resource_id == store.llm_calls[-1].id


def test_simulator_control_actions_are_simulator_events(fresh_state, client, login):
    r = client.post("/api/hospital/simulator/advance", headers=login("U-OPS"), json={"minutes": 5})
    assert r.status_code == 200
    assert fresh_state.audit.events()[-1].event_type == "simulator_event"


def test_admin_search_and_export(fresh_state, client, login):
    store = fresh_state
    enc = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="ICU"))
    mrn = next(i["value"] for i in store.fhir.read("Patient", ref_id(enc["subject"]))["identifier"]
               if i["system"] == MRN_SYSTEM)
    doctor = next(u for u in store.staff.values() if u.role == Role.PHYSICIAN and u.demo_login)
    assert client.get(f"/api/hospital/patients/{mrn}", headers=login(doctor.id)).status_code == 403
    admin = login("U-ADMIN")
    found = client.get("/api/admin/audit", headers=admin, params={"mrn": mrn}).json()
    assert found["total"] >= 1 and all(e["patient_mrn_hash"] == mrn_hash(mrn) for e in found["events"])
    assert found["events"][0]["outcome"] == "denied" and "break_glass" in found["event_types"]
    reads = client.get("/api/admin/audit", headers=admin, params={"event_type": "read", "limit": 5}).json()
    assert len(reads["events"]) == 5 and {e["event_type"] for e in reads["events"]} == {"read"}
    r = client.get("/api/admin/audit/export", headers=admin, params={"mrn": mrn})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("seq,ts,event_type") and len(lines) == found["total"] + 1
    export = store.audit.events()[-1]
    assert (export.event_type, export.user_id, export.module) == ("export", "U-ADMIN", "audit")
    assert mrn not in export.reason
    assert client.get("/api/admin/audit/export", headers=login("U-OPS")).status_code == 403


def test_signing_through_the_api(fresh_state, client, login):
    store = fresh_state
    nurse = next(u for u in store.staff.values() if u.role == Role.NURSE and u.demo_login)
    pharmacist = next(u for u in store.staff.values() if u.role == Role.PHARMACIST and u.demo_login)
    r = client.post("/api/hospital/documents/doc-demo-handoff/sign", headers=login(nurse.id))
    assert r.status_code == 200 and r.json()["doc_status"] == "final"
    assert client.post("/api/hospital/documents/doc-demo-handoff/sign", headers=login(nurse.id)).status_code == 409
    assert client.post("/api/hospital/documents/doc-demo-discharge/sign",
                       headers=login(pharmacist.id)).status_code == 403
    assert client.post("/api/hospital/documents/doc-demo-discharge/sign", headers=login("U-OPS")).status_code == 403
    assert client.post("/api/hospital/documents/nope/sign", headers=login(nurse.id)).status_code == 404
    # A document change through the gateway can never make it final (only the signing service does)
    assert store.fhir.read("DocumentReference", "doc-demo-discharge")["docStatus"] == "preliminary"
    registry = client.get("/api/hospital/registry", headers=login(nurse.id)).json()["modules"]
    assert registry["nursing_handoff"]["required_signoff_role"] == ["nurse"]


def test_chart_sections_follow_the_role(fresh_state, client, login):
    store = fresh_state
    doc = store.fhir.read("DocumentReference", "doc-demo-discharge")
    pid = ref_id(doc["subject"])
    mrn = next(i["value"] for i in store.fhir.read("Patient", pid)["identifier"] if i["system"] == MRN_SYSTEM)
    staff = {u.role: u for u in store.staff.values() if u.demo_login}
    doctor = client.get(f"/api/hospital/patients/{mrn}", headers=login(staff[Role.PHYSICIAN].id)).json()
    assert {"encounter", "consents", "medications", "observations", "documents"} <= set(doctor["sections"])
    discharge = next(d for d in doctor["documents"] if d["id"] == "doc-demo-discharge")
    assert discharge["can_sign"] and discharge["required_signoff_role"] == ["physician"] and discharge["text"]
    clerk = client.get(f"/api/hospital/patients/{mrn}", headers=login(staff[Role.CLERK].id)).json()
    assert set(clerk["sections"]) == {"encounter", "consents"} and clerk["patient"]["name"]
    assert "reason" in clerk["encounter"] and clerk["encounter"]["reason"] is None
    ops = client.get(f"/api/hospital/patients/{mrn}", headers=login("U-OPS")).json()
    assert ops["patient"]["name"] is None and ops["patient"]["birth_date"] is None and set(ops["sections"]) == {
        "encounter"}
    census = client.get("/api/hospital/census", headers=login("U-OPS"), params={"unit": "MEDA"}).json()["patients"]
    assert census and all(p["name"] is None for p in census)
    census = client.get("/api/hospital/census", headers=login(staff[Role.NURSE].id),
                        params={"unit": "MEDA"}).json()["patients"]
    assert all(p["name"] for p in census)
    assert client.get("/api/hospital/census", headers=login(staff[Role.NURSE].id),
                      params={"unit": "ICU"}).status_code == 403
    assert client.get(f"/api/hospital/patients/{mrn}", headers=login("U-ADMIN")).status_code == 403
