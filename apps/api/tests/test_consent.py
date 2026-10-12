"""WP4 (spec 6.3): consent management. Missing or declined consent degrades, never fails:
one test per degradation behaviour, plus consent changes (audit, domain events, API)."""

import pytest

from app.core.models import Role
from app.ehr import consent
from app.ehr.codes import MRN_SYSTEM
from app.ehr.events import InProcessEventBus
from app.ehr.gateway import Actor, FhirGateway, LocalBackend
from app.fhir.dt import ref_id

CATEGORIES = ("ai_processing", "followup_call", "sms")


def _system(store, module):
    return FhirGateway(Actor.system(module), module, LocalBackend(store))


def _patients_by_state(store, category: str) -> dict[str, str]:
    """One patient id per state (permit, deny, missing) of a consent category, from the generated data."""
    consents: dict[str, str] = {}
    for c in store.fhir.search("Consent"):
        if c["category"][0]["coding"][0]["code"] == category:
            consents[ref_id(c["patient"])] = c["provision"]["type"]
    out: dict[str, str] = {}
    for p in store.fhir.search("Patient"):
        out.setdefault(consents.get(p["id"], "missing"), p["id"])
        if len(out) == 3:
            return out
    raise AssertionError(f"not every state of {category} occurs")


def test_status_reads_the_generated_consents(fresh_state):
    store = fresh_state
    fhir = _system(store, "consent_management")
    for category in CATEGORIES:
        for state, pid in _patients_by_state(store, category).items():
            assert consent.consent_status(fhir, pid)[category] == state


def test_no_followup_call_consent_means_a_manual_followup_task(fresh_state):
    store = fresh_state
    fhir = _system(store, "followup_calls")
    states = _patients_by_state(store, "followup_call")
    plan = consent.plan_followup_call(fhir, states["permit"])
    assert (plan.mode, plan.task_id) == ("call", None)
    for state in ("missing", "deny"):
        plan = consent.plan_followup_call(fhir, states[state])
        assert plan.mode == "manual_task" and plan.task_id
        task = store.fhir.read("Task", plan.task_id)
        assert task["performerType"][0]["coding"][0]["code"] == "nurse" and task["status"] == "requested"
        assert task["for"]["reference"] == f"Patient/{states[state]}" and "by hand" in task["description"]


def test_no_ai_processing_consent_means_template_only(fresh_state):
    store = fresh_state
    states = _patients_by_state(store, "ai_processing")
    doctor = next(u for u in store.staff.values() if u.role == Role.PHYSICIAN and u.demo_login)
    fhir = FhirGateway(Actor.of(doctor), "discharge_summary", LocalBackend(store))
    system = _system(store, "discharge_summary")
    assert consent.documentation_mode(system, states["permit"]).mode == "draft"
    for state in ("missing", "deny"):
        mode = consent.documentation_mode(system, states[state])
        assert mode.mode == "template_only" and "AI processing" in mode.reason
    # Reading consent is part of the physician's view of their own patients only
    medicine = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="MEDA"))
    assert consent.documentation_mode(fhir, ref_id(medicine["subject"])).mode in ("draft", "template_only")


def test_no_sms_consent_means_no_sms_and_a_desk_task(fresh_state):
    store = fresh_state
    fhir = _system(store, "patient_messaging")
    states = _patients_by_state(store, "sms")
    sent = consent.send_sms(fhir, states["permit"], "Your follow-up visit is on Monday at 10:00.")
    assert sent.sent and sent.task_id is None
    assert store.fhir.read("Communication", sent.communication_id)["status"] == "preparation"
    for state in ("missing", "deny"):
        out = consent.send_sms(fhir, states[state], "Your follow-up visit is on Monday at 10:00.")
        assert not out.sent and out.task_id
        comm = store.fhir.read("Communication", out.communication_id)
        assert comm["status"] == "not-done" and "SMS" in comm["statusReason"]["text"]
        task = store.fhir.read("Task", out.task_id)
        assert task["performerType"][0]["coding"][0]["code"] == "clerk"


def test_a_consent_change_is_audited_and_published(fresh_state):
    store = fresh_state
    clerk = next(u for u in store.staff.values() if u.role == Role.CLERK and u.demo_login)
    fhir = FhirGateway(Actor.of(clerk), "consent_management", LocalBackend(store))
    states = _patients_by_state(store, "sms")
    local_bus = InProcessEventBus()
    seen = []
    local_bus.subscribe(["consent.revoked", "consent.granted"], seen.append, consumer="test.consent")
    revoked = consent.change_consent(fhir, states["permit"], "sms", False)
    assert (revoked["previous"], revoked["current"], revoked["event"].type) == ("permit", "deny", "consent.revoked")
    granted = consent.change_consent(fhir, states["missing"], "sms", True)
    assert (granted["previous"], granted["event"].type) == ("missing", "consent.granted")
    assert consent.change_consent(fhir, states["deny"], "sms", False)["event"] is None  # no change, no event
    assert consent.consent_status(fhir, states["permit"])["sms"] == "deny"
    assert consent.consent_status(fhir, states["missing"])["sms"] == "permit"
    local_bus.drain()
    assert [(e.type, e.patient, e.attrs["category"], e.actor) for e in seen] == [
        ("consent.revoked", states["permit"], "sms", f"user:{clerk.id}"),
        ("consent.granted", states["missing"], "sms", f"user:{clerk.id}")]
    changes = store.audit.query(event_type="consent_change")
    assert len(changes) == 3 and {e.module for e in changes} == {"consent_management"}
    assert changes[0].reason == "sms: permit -> deny" and changes[0].patient_mrn_hash


def test_consent_api_roles_and_scope(fresh_state, client, login):
    store = fresh_state
    states = _patients_by_state(store, "followup_call")
    mrn = next(i["value"] for i in store.fhir.read("Patient", states["permit"])["identifier"]
               if i["system"] == MRN_SYSTEM)
    clerk = next(u for u in store.staff.values() if u.role == Role.CLERK and u.demo_login)
    body = {"category": "followup_call", "decision": "deny"}
    r = client.post(f"/api/hospital/patients/{mrn}/consents", headers=login(clerk.id), json=body)
    assert r.status_code == 200 and r.json()["event"] == "consent.revoked"
    assert r.json()["consents"]["followup_call"] == "deny"
    for user_id in ("U-OPS", "U-ADMIN"):
        assert client.post(f"/api/hospital/patients/{mrn}/consents", headers=login(user_id),
                           json=body).status_code == 403
    pharmacist = next(u for u in store.staff.values() if u.role == Role.PHARMACIST and u.demo_login)
    assert client.post(f"/api/hospital/patients/{mrn}/consents", headers=login(pharmacist.id),
                       json=body).status_code == 403


def test_unknown_categories_are_refused(fresh_state):
    fhir = _system(fresh_state, "consent_management")
    with pytest.raises(ValueError):
        consent.change_consent(fhir, "pat-0001", "research", True)
