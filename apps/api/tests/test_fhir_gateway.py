"""WP2: FhirGateway (spec 6.2): typed reads, the write allow-list with audited refusals,
bed moves, the HAPI backend (fake server and, when one runs, the real HAPI), and the
grep rules that keep FHIR access inside the gateway.

These tests are about the gateway's mechanics, so they act as a trusted system actor
under a test module registered for every allowed write; the role and registry rules
added in WP4 (6.3) are tested in tests/test_access.py."""

import base64
import dataclasses
import re
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.core import registry
from app.ehr import gateway as gw
from app.ehr.clock import hospital_now
from app.ehr.codes import MRN_SYSTEM
from app.ehr.gateway import Actor, FhirAccessDenied, FhirConflict, FhirGateway, LocalBackend
from app.ehr.hapi import (FhirServerError, FhirUnavailable, HapiBackend, HapiClient, NoAuth, SmartBackendAuth,
                          fhir_query)
from app.fhir.dt import parse
from app.fhir.types.encounter import Encounter
from app.fhir.types.patient import Patient

API = Path(__file__).resolve().parents[1]
OPS = Actor("U-OPS1", "Olivia Ops", "operations_manager")
RAD = Actor("U-RAD1", "Rad One", "radiologist")
SYSTEM = Actor("system:wp2-test", "WP2 test", "system", "system")
TEST_MODULE = {"tier": "ops", "required_signoff_role": [],
               "writes_allowed": {t: ["*"] for t in ("Task", "DocumentReference", "Communication", "Flag",
                                                     "Appointment")} | {"Encounter.location": ["append"]}}


@pytest.fixture(autouse=True)
def _registered_test_module():
    with registry.temporary("wp2_test", TEST_MODULE):
        yield


def _gateway(store, actor=SYSTEM, module="wp2_test"):
    return FhirGateway(actor, module, LocalBackend(store))


def _mrn(patient: dict) -> str:
    return next(i["value"] for i in patient["identifier"] if i["system"] == MRN_SYSTEM)


def _inpatient(store) -> tuple[dict, dict]:
    """A current inpatient in a bed: (encounter, patient)."""
    for enc in store.fhir.search("Encounter", cls="IMP", status="in-progress"):
        current = gw._current_location(enc)
        if current and current["location"]["reference"].count("-") == 2:
            return enc, store.fhir.read("Patient", enc["subject"]["reference"].split("/")[1])
    raise AssertionError("no inpatient in a bed")


def _audit_tail(store, n):
    return store.audit.events()[-n:]


# ---------- typed reads ----------


def test_reads_are_typed_and_audited(fresh_state):
    store = fresh_state
    enc, patient = _inpatient(store)
    fhir = _gateway(store)
    before = len(store.audit.events())

    found = fhir.get_patient(_mrn(patient))
    assert isinstance(found, Patient) and found.id == patient["id"]
    active = fhir.get_active_encounter(_mrn(patient))
    assert isinstance(active, Encounter) and active.id == enc["id"]
    stays = fhir.search_encounters(mrn=_mrn(patient), cls="IMP")
    assert enc["id"] in [e.id for e in stays]
    starts = [e.period.start for e in fhir.search_encounters(mrn=_mrn(patient))]
    assert starts == sorted(starts, reverse=True)  # newest first

    orders = fhir.get_orders(enc["id"])
    assert orders and all(o.encounter.reference == f"Encounter/{enc['id']}" for o in orders)
    meds = fhir.get_medications(enc["id"])
    assert meds and all(m.resourceType == "MedicationRequest" for m in meds)
    home = fhir.get_home_meds(_mrn(patient))
    assert all(m.status == "active" for m in home)
    vitals = fhir.get_observations(enc["id"], codes=["8867-4"])  # heart rate: a component of the vital panel
    assert vitals and all(any(c.code.coding[0].code == "8867-4" for c in v.component or []) for v in vitals)
    since = hospital_now(store) - timedelta(hours=12)
    recent = fhir.get_observations(enc["id"], since=since)
    assert recent and all(parse(o.effectiveDateTime) >= since for o in recent)
    assert fhir.get_documents(enc["id"]) == []

    events = store.audit.events()[before:]
    assert len(events) == 10  # one per call (the internal MRN lookups are not separate events)
    assert {e.module for e in events} == {"wp2_test"} and {e.user_id for e in events} == {"system:wp2-test"}
    assert {e.event_type for e in events} == {"read"} and events[0].patient_mrn_hash  # 6.3 audit fields
    assert [e.resource_type for e in events[:3]] == ["Patient", "Encounter", "Encounter"]
    assert events[0].resource_id == patient["id"] and events[4].resource_id == enc["id"]


def test_unknown_patient_reads_are_empty_and_still_audited(fresh_state):
    fhir = _gateway(fresh_state)
    assert fhir.get_patient("00000000") is None
    assert fhir.get_active_encounter("00000000") is None
    assert fhir.get_home_meds("00000000") == []
    assert [e.resource_id for e in _audit_tail(fresh_state, 3)] == [None, None, None]


def test_bed_board_matches_bed_status(fresh_state):
    fhir = _gateway(fresh_state)
    board = fhir.get_bed_board("MEDA")
    assert board.unit_name == "Medicine A" and len(board.beds) == 32
    for bed in board.beds:
        assert bed.room == bed.id.rsplit("-", 1)[0]
        assert (bed.status == "O") == (bed.encounter_id is not None), bed
    assert board.counts().get("O", 0) > 0
    ed = fhir.get_bed_board("ED")
    assert len(ed.beds) == 30 and all(w.startswith("ed-") for w in ed.waiting)
    with pytest.raises(LookupError):
        fhir.get_bed_board("NOPE")


# ---------- write allow-list ----------


def _doc(patient_id, enc_id, doc_status="preliminary"):
    return {"resourceType": "DocumentReference", "status": "current", "docStatus": doc_status,
            "type": {"coding": [{"system": "http://loinc.org", "code": "18842-5", "display": "Discharge summary"}]},
            "subject": {"reference": f"Patient/{patient_id}"},
            "context": {"encounter": [{"reference": f"Encounter/{enc_id}"}]},
            "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"Draft").decode()}}]}


def _task(patient_id, enc_id, status="requested"):
    return {"resourceType": "Task", "status": status, "intent": "order", "description": "Review the draft",
            "for": {"reference": f"Patient/{patient_id}"}, "encounter": {"reference": f"Encounter/{enc_id}"}}


def _appointment(patient_id, status="proposed"):
    return {"resourceType": "Appointment", "status": status, "start": "2030-01-07T09:00:00-05:00",
            "end": "2030-01-07T09:30:00-05:00",
            "participant": [{"actor": {"reference": f"Patient/{patient_id}"}, "status": "needs-action"}]}


def test_allowed_writes(fresh_state):
    enc, patient = _inpatient(fresh_state)
    fhir = _gateway(fresh_state)
    task = fhir.create(_task(patient["id"], enc["id"]))
    task.status = "completed"
    assert fhir.update(task).status == "completed"
    doc = fhir.create(_doc(patient["id"], enc["id"]))
    assert doc.docStatus == "preliminary" and [d.id for d in fhir.get_documents(enc["id"])] == [doc.id]
    doc.description = "Second draft"
    fhir.update(doc)
    flag = fhir.create({"resourceType": "Flag", "status": "active", "code": {"text": "Fall risk"},
                        "subject": {"reference": f"Patient/{patient['id']}"}})
    flag.status = "inactive"
    fhir.update(flag)
    fhir.create({"resourceType": "Communication", "status": "completed",
                 "subject": {"reference": f"Patient/{patient['id']}"}, "payload": [{"contentString": "Called back"}]})
    appt = fhir.create(_appointment(patient["id"]))
    appt.start = "2030-01-07T10:00:00-05:00"
    fhir.update(appt)  # still proposed
    events = _audit_tail(fresh_state, 10)
    assert [(e.action, e.resource_type) for e in events] == [
        ("create", "Task"), ("update", "Task"), ("create", "DocumentReference"), ("read", "DocumentReference"),
        ("update", "DocumentReference"), ("create", "Flag"), ("update", "Flag"), ("create", "Communication"),
        ("create", "Appointment"), ("update", "Appointment")]
    assert {e.outcome for e in events} == {"allowed"} and events[0].resource_id == task.id


REFUSED_CREATES = [
    ("final document", lambda p, e: _doc(p, e, "final")),
    ("booked appointment", lambda p, e: _appointment(p, "booked")),
    ("observation", lambda p, e: {"resourceType": "Observation", "status": "final", "code": {"text": "HR"},
                                  "subject": {"reference": f"Patient/{p}"}}),
    ("medication order", lambda p, e: {"resourceType": "MedicationRequest", "status": "active", "intent": "order",
                                       "medicationCodeableConcept": {"text": "warfarin"},
                                       "subject": {"reference": f"Patient/{p}"}}),
    ("encounter", lambda p, e: {"resourceType": "Encounter", "status": "planned", "class": {"code": "AMB"},
                                "subject": {"reference": f"Patient/{p}"}}),
    ("patient", lambda p, e: {"resourceType": "Patient", "name": [{"text": "New Person"}]}),
]


@pytest.mark.parametrize("label,build", REFUSED_CREATES, ids=[r[0] for r in REFUSED_CREATES])
def test_refused_creates_are_audited(fresh_state, label, build):
    enc, patient = _inpatient(fresh_state)
    resource = build(patient["id"], enc["id"])
    before = fresh_state.fhir.count(resource["resourceType"])
    with pytest.raises(FhirAccessDenied):
        _gateway(fresh_state).create(resource)
    event = _audit_tail(fresh_state, 1)[0]
    assert (event.action, event.resource_type, event.outcome) == ("create", resource["resourceType"], "denied")
    assert event.module == "wp2_test" and event.reason and event.user_id == "system:wp2-test"
    assert fresh_state.fhir.count(resource["resourceType"]) == before  # nothing written


def test_refused_updates_and_deletes_are_audited(fresh_state):
    enc, patient = _inpatient(fresh_state)
    fhir = _gateway(fresh_state)
    doc = fhir.create(_doc(patient["id"], enc["id"]))
    doc.docStatus = "final"  # signing is not the gateway's to do
    appt = fhir.create(_appointment(patient["id"]))
    appt.status = "booked"
    changed_patient = {**patient, "gender": "other"}
    changed_encounter = {**enc, "status": "finished"}
    for resource in (doc, appt, changed_patient, changed_encounter):
        with pytest.raises(FhirAccessDenied):
            fhir.update(resource)
    with pytest.raises(FhirAccessDenied):
        fhir.delete("Patient", patient["id"])
    events = _audit_tail(fresh_state, 5)
    assert [(e.action, e.resource_type, e.outcome) for e in events] == [
        ("update", "DocumentReference", "denied"), ("update", "Appointment", "denied"), ("update", "Patient", "denied"),
        ("update", "Encounter", "denied"), ("delete", "Patient", "denied")]
    assert fresh_state.fhir.read("DocumentReference", doc.id)["docStatus"] == "preliminary"
    assert fresh_state.fhir.read("Patient", patient["id"])["gender"] == patient["gender"]


# ---------- bed moves ----------


def _free_bed(store, unit, but=None):
    return next(b for b in _gateway(store).get_bed_board(unit).beds if b.status == "U" and b.id != but)


def test_bed_manager_moves_a_patient(fresh_state):
    store = fresh_state
    enc, _patient = _inpatient(store)
    old_bed = gw._current_location(enc)["location"]["reference"].split("/")[1]
    unit = old_bed.split("-")[0]
    new_bed = _free_bed(store, unit).id
    moved = _gateway(store, actor=OPS).append_encounter_location(enc["id"], new_bed)
    history = moved.location
    assert history[-1].location.reference == f"Location/{new_bed}" and history[-1].status == "active"
    assert history[-2].location.reference == f"Location/{old_bed}" and history[-2].status == "completed"
    assert history[-2].period.end == history[-1].period.start
    assert len(history) == len(enc["location"]) + 1  # appended, nothing rewritten
    board = {b.id: b for b in _gateway(store).get_bed_board(unit).beds}
    assert board[new_bed].status == "O" and board[new_bed].encounter_id == enc["id"]
    assert board[old_bed].status == "K" and board[old_bed].encounter_id is None  # to housekeeping
    event = _audit_tail(store, 2)[0]
    assert (event.action, event.resource_type, event.resource_id) == ("update", "Encounter", enc["id"])


def test_bed_moves_are_refused_for_other_roles_and_states(fresh_state):
    store = fresh_state
    enc, _patient = _inpatient(store)
    unit = gw._current_location(enc)["location"]["reference"].split("/")[1].split("-")[0]
    free = _free_bed(store, unit).id
    with pytest.raises(FhirAccessDenied):
        _gateway(store, actor=RAD).append_encounter_location(enc["id"], free)
    assert _audit_tail(store, 1)[0].outcome == "denied"
    taken = next(b for b in _gateway(store).get_bed_board(unit).beds if b.status == "O" and b.encounter_id != enc["id"])
    with pytest.raises(FhirConflict):
        _gateway(store).append_encounter_location(enc["id"], taken.id)
    with pytest.raises(FhirConflict):
        _gateway(store).append_encounter_location(enc["id"], unit)  # a unit, not a bed
    finished = store.fhir.search("Encounter", cls="IMP", status="finished", limit=1)[0]
    with pytest.raises(FhirConflict):
        _gateway(store).append_encounter_location(finished["id"], free)
    with pytest.raises(LookupError):
        _gateway(store).append_encounter_location("no-such-encounter", free)


# ---------- the HAPI backend against a fake FHIR server ----------


def _bundle(resources, next_url=None):
    out = {"resourceType": "Bundle", "type": "searchset",
           "entry": [{"resource": r, "search": {"mode": "match"}} for r in resources]}
    if next_url:
        out["link"] = [{"relation": "next", "url": next_url}]
    return out


def _obs(rid, enc, code, when):
    return {"resourceType": "Observation", "id": rid, "status": "final", "code": {"coding": [{"code": code}]},
            "subject": {"reference": "Patient/p1"}, "encounter": {"reference": f"Encounter/{enc}"},
            "effectiveDateTime": when, "meta": {"versionId": "1", "lastUpdated": when}}


def test_hapi_backend_pages_and_keeps_local_search_semantics():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=_bundle([_obs("o3", "e1", "718-7", "2026-10-08T09:00:00-04:00")]))
        return httpx.Response(200, json=_bundle([
            _obs("o2", "e1", "718-7", "2026-10-08T08:00:00-04:00"),
            _obs("o1", "e1", "2160-0", "2026-10-08T07:00:00-04:00"),
        ], next_url="http://fake/fhir/Observation?page=2"))

    client = HapiClient("http://fake/fhir", client=httpx.Client(transport=httpx.MockTransport(handler)))
    backend = HapiBackend(client)
    found = backend.search("Observation", encounter="e1", code="718-7", order="date")
    assert [r["id"] for r in found] == ["o2", "o3"]  # the server's extra o1 is filtered out; sorted by date
    first = calls[0].url.params
    assert first["encounter"] == "e1" and first["code"] == "718-7" and first["_count"] == "200"
    assert calls[0].headers["Cache-Control"] == "no-cache"  # never a server-cached search result
    assert len(calls) == 2 and calls[1].url.params.get("page") == "2"  # followed the next link
    fhir = FhirGateway(SYSTEM, "wp2_test", backend)
    assert fhir.backend.name == "hapi"


def test_hapi_client_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gone"):
            return httpx.Response(404, json={"resourceType": "OperationOutcome"})
        if request.url.path.endswith("/broken"):
            return httpx.Response(503, text="maintenance")
        if request.method == "GET":
            return httpx.Response(200, json={"resourceType": "Task", "id": "exists"})
        return httpx.Response(422, text="bad")

    client = HapiClient("http://fake/fhir", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.read("Task", "gone") is None
    with pytest.raises(FhirUnavailable):
        client.read("Task", "broken")
    with pytest.raises(FhirServerError):
        client.create({"resourceType": "Task", "id": "exists"})  # a create never overwrites
    with pytest.raises(FhirServerError):
        client.update({"resourceType": "Task", "id": "x"})

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(FhirUnavailable):
        HapiClient("http://fake/fhir", client=httpx.Client(transport=httpx.MockTransport(down))).read("Task", "x")


def test_fhir_query_translation():
    assert fhir_query("Patient", {"mrn": "40000001"}) == [("identifier", f"{MRN_SYSTEM}|40000001")]
    assert fhir_query("MedicationStatement", {"encounter": "e1", "status": "active"}) == [
        ("context", "e1"), ("status", "active")]
    assert fhir_query("Condition", {"status": ["active", "resolved"]}) == [("clinical-status", "active,resolved")]
    assert fhir_query("Location", {"location": "MEDA-01", "unit": "MEDA"}) == [("partof", "Location/MEDA-01")]
    # Parameters FHIR cannot express exactly are left to the local matching (never narrowed wrongly)
    assert fhir_query("DocumentReference", {"status": "preliminary"}) == []
    assert fhir_query("Encounter", {"unit": "MEDA", "active_at": "x"}) == []


def test_auth_modes_and_settings(monkeypatch):
    assert NoAuth().headers() == {}
    client = HapiClient("http://fake/fhir", SmartBackendAuth("client", "https://ehr.example/token"),
                        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
    with pytest.raises(NotImplementedError):
        client.read("Patient", "x")
    from app.core import config

    monkeypatch.setenv("FHIR_BACKEND", "epic")
    with pytest.raises(ValueError):
        config.load_settings()
    monkeypatch.setenv("FHIR_BACKEND", "hapi")
    monkeypatch.setenv("FHIR_AUTH_MODE", "basic")
    with pytest.raises(ValueError):
        config.load_settings()
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, fhir_backend="hapi"))
    monkeypatch.setattr(gw, "_hapi_backend", None)
    assert gw.backend_from_settings().name == "hapi"
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, fhir_backend="local"))
    assert gw.backend_from_settings().name == "local"


# ---------- the real HAPI server, when it runs (docker compose --profile ehr up -d hapi) ----------


def _hapi_client() -> HapiClient | None:
    from app.core.config import settings

    client = HapiClient(settings.fhir_base_url, timeout_s=5)
    try:
        client._client.get(f"{settings.fhir_base_url}/metadata", headers={"Accept": "application/fhir+json"})
    except httpx.HTTPError:
        return None
    return client


def _fixture(tag: str) -> list[dict]:
    """A patient in the Medicine A waiting area with an order, a medication, a lab and vitals."""
    p, e = f"{tag}-pat", f"{tag}-enc"
    subject = {"reference": f"Patient/{p}"}
    encounter = {"reference": f"Encounter/{e}"}
    return [
        {"resourceType": "Patient", "id": p, "identifier": [{"system": MRN_SYSTEM, "value": "9" + tag[-7:]}],
         "name": [{"text": "Test Patient"}], "birthDate": "1950-01-01"},
        {"resourceType": "Encounter", "id": e, "status": "in-progress", "subject": subject,
         "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP"},
         "period": {"start": "2026-10-01T08:00:00-04:00"},
         "location": [{"location": {"reference": "Location/MEDA"}, "status": "active",
                       "period": {"start": "2026-10-01T08:00:00-04:00"}}]},
        {"resourceType": "ServiceRequest", "id": f"{tag}-ord", "status": "active", "intent": "order",
         "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]}, "subject": subject,
         "encounter": encounter, "authoredOn": "2026-10-01T09:00:00-04:00"},
        {"resourceType": "MedicationRequest", "id": f"{tag}-med", "status": "active", "intent": "order",
         "medicationCodeableConcept": {"coding": [{"code": "161"}]}, "subject": subject, "encounter": encounter,
         "authoredOn": "2026-10-01T09:30:00-04:00"},
        {"resourceType": "MedicationStatement", "id": f"{tag}-home", "status": "active",
         "medicationCodeableConcept": {"coding": [{"code": "11289"}]}, "subject": subject,
         "effectivePeriod": {"start": "2020-01-01"}},
        {"resourceType": "Observation", "id": f"{tag}-lab", "status": "final",
         "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]}, "subject": subject,
         "encounter": encounter, "effectiveDateTime": "2026-10-01T10:00:00-04:00",
         "valueQuantity": {"value": 11.2, "unit": "g/dL"}},
        {"resourceType": "Observation", "id": f"{tag}-vit", "status": "final",
         "code": {"coding": [{"system": "http://loinc.org", "code": "85353-1"}]}, "subject": subject,
         "encounter": encounter, "effectiveDateTime": "2026-10-01T11:00:00-04:00",
         "component": [{"code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
                        "valueQuantity": {"value": 88, "unit": "/min"}}]},
    ]


def _summary(fhir: FhirGateway, mrn: str, enc: str) -> dict:
    return {
        "patient": fhir.get_patient(mrn).id,
        "encounters": [e.id for e in fhir.search_encounters(mrn=mrn)],
        "active": fhir.get_active_encounter(mrn).id,
        "orders": [o.id for o in fhir.get_orders(enc)],
        "medications": [m.id for m in fhir.get_medications(enc)],
        "home": [m.id for m in fhir.get_home_meds(mrn)],
        "vitals": [o.id for o in fhir.get_observations(enc, codes=["8867-4"])],
        "observations": [o.id for o in fhir.get_observations(enc)],
        "waiting": enc in fhir.get_bed_board("MEDA").waiting,
        "documents": [d.docStatus for d in fhir.get_documents(enc)],
    }


def test_both_backends_answer_the_same(fresh_state):
    client = _hapi_client()
    if client is None:
        pytest.skip("HAPI FHIR is not running (docker compose --profile ehr up -d hapi)")
    tag = f"wp2t-{uuid.uuid4().int % 10**7:07d}"
    resources = _fixture(tag)
    mrn, enc = resources[0]["identifier"][0]["value"], resources[1]["id"]
    created_docs = []
    try:
        for r in resources:
            fresh_state.fhir.create(r)
            client.create(r)
        local, hapi = _gateway(fresh_state), FhirGateway(SYSTEM, "wp2_test", HapiBackend(client))
        for fhir in (local, hapi):  # a write through each backend
            created_docs.append(fhir.create(_doc(resources[0]["id"], enc)).id)
        a, b = _summary(local, mrn, enc), _summary(hapi, mrn, enc)
        assert a == b
        assert a["waiting"] and a["vitals"] == [f"{tag}-vit"] and a["documents"] == ["preliminary"]
    finally:  # the local writes roll back with the test; the server's are deleted
        if len(created_docs) > 1:
            client.delete("DocumentReference", created_docs[1])
        for r in reversed(resources):
            client.delete(r["resourceType"], r["id"])
        client.close()


# ---------- grep rules ----------

HTTP_CLIENT = re.compile(r"^\s*(import|from)\s+(httpx|requests|urllib3|urllib\.request|http\.client|aiohttp|pycurl)\b",
                         re.MULTILINE)


def _python_files(*folders):
    for folder in folders:
        yield from (API / folder).rglob("*.py")


def test_no_fhir_http_outside_the_gateway_backend():
    """Spec 6.2: no module talks HTTP to a FHIR server; only app/ehr/hapi.py does (the loader uses it too)."""
    offenders = []
    for path in _python_files("app", "scripts"):
        rel = path.relative_to(API).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel != "app/ehr/hapi.py" and HTTP_CLIENT.search(text) and re.search(r"fhir", text, re.IGNORECASE):
            offenders.append(rel)  # an HTTP client in a file that deals with FHIR
        url_allowed = rel in ("app/ehr/hapi.py", "app/core/config.py") or rel.startswith("scripts/")
        if not url_allowed and re.search(r"FHIR_BASE_URL|fhir_base_url|:8080/fhir", text):
            offenders.append(rel)  # only the backend, the settings and the loader scripts know the server URL
    assert offenders == []


def test_modules_go_through_the_gateway():
    """Modules never touch the FHIR store or a backend directly."""
    pattern = re.compile(r"\.fhir\.(search|read|create|update|delete|count|ids)\b|store\.fhir\b|fhirstore|"
                         r"app\.ehr\.hapi|HapiClient|HapiBackend|LocalBackend")
    offenders = [p.relative_to(API).as_posix() for p in _python_files("app/modules") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []

