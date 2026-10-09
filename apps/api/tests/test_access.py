"""WP4 (spec 6.3): hospital RBAC enforced in FhirGateway, one test per cell of the role table.

Rows: physician, nurse, pharmacist, clerk, operations manager, admin. Columns:
what the role sees (visibility), what it signs, what it writes. The hospital staff
come from the generator's Practitioner / PractitionerRole (app/ehr/seed/platform.py):
the demo physician and nurse work on Medicine A (MEDA). Draft documents seeded for
the signing service: a discharge summary and an SBAR handoff for one MEDA inpatient,
a medication reconciliation for another.
"""

import pytest

from app.core import registry
from app.core.models import Role
from app.ehr.codes import MRN_SYSTEM, PRACTITIONER_ROLE, TASK_CODE, concept
from app.ehr.gateway import Actor, BreakGlassRequired, FhirAccessDenied, FhirConflict, FhirGateway, LocalBackend
from app.fhir.dt import ref, ref_id

DISCHARGE, HANDOFF, MEDREC = "doc-demo-discharge", "doc-demo-handoff", "doc-demo-medrec"


def _user(store, role: Role):
    if role in (Role.OPERATIONS_MANAGER, Role.ADMIN):
        return store.staff["U-OPS" if role == Role.OPERATIONS_MANAGER else "U-ADMIN"]
    return next(u for u in store.staff.values() if u.role == role and u.demo_login)


def gw(store, role: Role, module: str = "patient_chart") -> FhirGateway:
    return FhirGateway(Actor.of(_user(store, role)), module, LocalBackend(store))


def _mrn(store, patient_id: str) -> str:
    patient = store.fhir.read("Patient", patient_id)
    return next(i["value"] for i in patient["identifier"] if i["system"] == MRN_SYSTEM)


def _inpatient(store, unit: str, but: set[str] = frozenset()) -> tuple[str, str]:
    """(encounter id, patient id) of a current inpatient on the unit."""
    for enc in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=unit):
        pid = ref_id(enc["subject"])
        if pid not in but and not any(e["id"] != enc["id"] and e["status"] in ("arrived", "triaged", "in-progress")
                                      for e in store.fhir.search("Encounter", patient=pid)):
            return enc["id"], pid
    raise AssertionError(f"no inpatient on {unit}")


def _doc_patient(store, doc_id: str) -> tuple[str, str]:
    doc = store.fhir.read("DocumentReference", doc_id)
    return doc["context"]["encounter"][0]["reference"].split("/")[1], ref_id(doc["subject"])


def _task(patient_id, enc_id, performer: str | None = None) -> dict:
    task = {"resourceType": "Task", "status": "requested", "intent": "order", "description": "Follow up",
            "code": concept(TASK_CODE, "action", "Action"), "for": ref("Patient", patient_id),
            "encounter": ref("Encounter", enc_id)}
    if performer:
        task["performerType"] = [concept(PRACTITIONER_ROLE, performer)]
    return task


def _draft(patient_id, enc_id, doc_type=("http://loinc.org", "18842-5", "Discharge summary")) -> dict:
    return {"resourceType": "DocumentReference", "status": "current", "docStatus": "preliminary",
            "type": concept(*doc_type), "subject": ref("Patient", patient_id),
            "context": {"encounter": [ref("Encounter", enc_id)]}}


def _flag(patient_id) -> dict:
    return {"resourceType": "Flag", "status": "active", "code": {"text": "Fall risk"}, "subject": ref("Patient", patient_id)}


def _last(store):
    return store.audit.events()[-1]


# ---------- the staff ----------


def test_hospital_staff_come_from_the_generated_practitioners(fresh_state):
    store = fresh_state
    hospital = [u for u in store.staff.values() if u.practitioner_id]
    assert len(hospital) == len(store.fhir.search("Practitioner"))
    demo = {u.role: u for u in hospital if u.demo_login}
    assert set(demo) == {Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST, Role.CLERK}
    assert demo[Role.PHYSICIAN].unit_ids == ["MEDA"] and demo[Role.NURSE].unit_ids == ["MEDA"]
    assert demo[Role.PHARMACIST].unit_ids == [] and demo[Role.CLERK].unit_ids == []
    roles = {r["practitioner"]["reference"].split("/")[1]: r for r in store.fhir.search("PractitionerRole")}
    for user in hospital:
        units = [ref_id(loc) for loc in roles[user.practitioner_id].get("location") or []]
        assert user.unit_ids == units


# ---------- visibility ----------


def test_visibility_physician(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    other_enc, other = _inpatient(store, "ICU")
    fhir = gw(store, Role.PHYSICIAN)
    patient = fhir.get_patient(_mrn(store, pid))
    assert patient.name and patient.birthDate  # the whole record of the unit's patients
    assert fhir.get_observations(enc) and fhir.get_medications(enc) is not None and fhir.get_orders(enc)
    assert fhir.get_bed_board("MEDA").unit_id == "MEDA"
    with pytest.raises(BreakGlassRequired):
        fhir.get_patient(_mrn(store, other))
    assert (_last(store).outcome, _last(store).resource_type) == ("denied", "Patient")
    with pytest.raises(BreakGlassRequired):
        fhir.get_observations(other_enc)
    with pytest.raises(FhirAccessDenied):
        fhir.get_bed_board("ICU")  # another unit's board: not even with break-glass


def test_visibility_nurse(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    _, other = _inpatient(store, "ORTH")
    fhir = gw(store, Role.NURSE)
    assert fhir.get_patient(_mrn(store, pid)).name
    assert fhir.get_observations(enc) and fhir.get_documents(enc) is not None
    assert {c["resourceType"] for c in fhir.get_consents(pid)} <= {"Consent"}
    with pytest.raises(BreakGlassRequired):
        fhir.get_home_meds(_mrn(store, other))
    with pytest.raises(FhirAccessDenied):
        fhir.search_encounters(unit="ORTH")


def test_visibility_pharmacist(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "ICU")  # any unit: medication review is hospital-wide
    fhir = gw(store, Role.PHARMACIST)
    assert fhir.get_patient(_mrn(store, pid)).name
    assert fhir.get_medications(enc) is not None and fhir.get_home_meds(_mrn(store, pid)) is not None
    labs = fhir.get_observations(enc)
    assert all(o.category[0].coding[0].code == "laboratory" for o in labs)  # lab results only, no vital signs
    for refused in (lambda: fhir.get_orders(enc), lambda: fhir.read("Condition", "x"), lambda: fhir.get_consents(pid)):
        with pytest.raises(FhirAccessDenied):
            refused()
    medrec_enc, _ = _doc_patient(store, MEDREC)
    discharge_enc, _ = _doc_patient(store, DISCHARGE)
    assert [d.id for d in fhir.get_documents(medrec_enc)] == [MEDREC]
    assert DISCHARGE not in [d.id for d in fhir.get_documents(discharge_enc)]  # not a medication document


def test_visibility_clerk(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "SURG")
    fhir = gw(store, Role.CLERK)
    patient = fhir.get_patient(_mrn(store, pid))
    assert patient.name and patient.telecom  # demographics, any unit
    encounter = fhir.search_encounters(patient_id=pid, limit=1)[0]
    assert encounter.id and "reasonCode" not in encounter.to_fhir()  # the visit without its clinical content
    assert fhir.get_consents(pid) is not None
    for refused in (lambda: fhir.get_observations(enc), lambda: fhir.get_medications(enc),
                    lambda: fhir.get_documents(enc)):
        with pytest.raises(FhirAccessDenied):
            refused()


def test_visibility_operations_manager(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "ICU")
    fhir = gw(store, Role.OPERATIONS_MANAGER)
    patient = fhir.get_patient(_mrn(store, pid))
    assert patient.name is None and patient.birthDate is None and patient.telecom is None
    assert [i.system for i in patient.identifier] == [MRN_SYSTEM]  # an individual patient is an MRN and a bed
    census = fhir.get_census("ICU")
    assert census and all(c.name is None and c.mrn for c in census)
    assert all("reasonCode" not in e.to_fhir() for e in fhir.search_encounters(unit="ICU"))
    orders = fhir.get_orders(enc)
    assert all(o.category[0].coding[0].code in ("bed-request", "surgery") for o in orders)
    for refused in (lambda: fhir.get_observations(enc), lambda: fhir.get_medications(enc),
                    lambda: fhir.get_documents(enc)):
        with pytest.raises(FhirAccessDenied):
            refused()


def test_visibility_admin(fresh_state):
    store = fresh_state
    _, pid = _inpatient(store, "MEDA")
    fhir = gw(store, Role.ADMIN)
    assert fhir.read("Location", "MEDA").id == "MEDA" and fhir.read("Organization", "org-demo-hospital")
    for refused in (lambda: fhir.get_patient(_mrn(store, pid)), lambda: fhir.get_bed_board("MEDA"),
                    lambda: fhir.read("Patient", pid)):
        with pytest.raises(FhirAccessDenied):
            refused()
    assert _last(store).outcome == "denied"


# ---------- signing ----------


def test_sign_physician(fresh_state):
    store = fresh_state
    fhir = gw(store, Role.PHYSICIAN, "signing")
    result = fhir.sign_document(DISCHARGE)
    assert result.doc_status == "final" and result.signed_roles == ["physician"]
    doc = store.fhir.read("DocumentReference", DISCHARGE)
    assert doc["docStatus"] == "final" and doc["authenticator"]["reference"] == f"Practitioner/{fhir.actor.practitioner_id}"
    event = _last(store)
    assert (event.event_type, event.action, event.module, event.outcome) == ("sign", "sign", "discharge_summary",
                                                                            "allowed")
    with pytest.raises(FhirAccessDenied):
        fhir.sign_document(HANDOFF)  # a nurse's document
    with pytest.raises(FhirConflict):
        fhir.sign_document(DISCHARGE)  # already final


def test_sign_nurse(fresh_state):
    store = fresh_state
    fhir = gw(store, Role.NURSE, "signing")
    assert fhir.sign_document(HANDOFF).doc_status == "final"
    with pytest.raises(FhirAccessDenied):
        fhir.sign_document(DISCHARGE)
    assert store.fhir.read("DocumentReference", DISCHARGE)["docStatus"] == "preliminary"
    assert (_last(store).event_type, _last(store).outcome) == ("sign", "denied")


def test_sign_pharmacist(fresh_state):
    """Medication reconciliation is co-signed: the pharmacist's signature alone leaves it preliminary."""
    store = fresh_state
    first = gw(store, Role.PHARMACIST, "signing").sign_document(MEDREC)
    assert (first.doc_status, first.missing_roles) == ("preliminary", ["physician"])
    with pytest.raises(FhirConflict):
        gw(store, Role.PHARMACIST, "signing").sign_document(MEDREC)  # the same role twice
    second = gw(store, Role.PHYSICIAN, "signing").sign_document(MEDREC)
    assert (second.doc_status, second.signed_roles) == ("final", ["pharmacist", "physician"])
    with pytest.raises(FhirAccessDenied):
        gw(store, Role.PHARMACIST, "signing").sign_document(DISCHARGE)


@pytest.mark.parametrize("role", [Role.CLERK, Role.OPERATIONS_MANAGER, Role.ADMIN], ids=lambda r: str(r))
def test_sign_roles_without_signing_rights(fresh_state, role):
    store = fresh_state
    for doc in (DISCHARGE, HANDOFF, MEDREC):
        with pytest.raises(FhirAccessDenied):
            gw(store, role, "signing").sign_document(doc)
        assert store.fhir.read("DocumentReference", doc)["docStatus"] == "preliminary"


# ---------- writes ----------


def test_write_physician(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    task = gw(store, Role.PHYSICIAN, "discharge_summary").create(_task(pid, enc))
    task.status = "completed"
    assert gw(store, Role.PHYSICIAN, "discharge_summary").update(task).status == "completed"
    doc = gw(store, Role.PHYSICIAN, "discharge_summary").create(_draft(pid, enc))
    assert doc.docStatus == "preliminary"
    for module, resource in (("bedside_nursing", _flag(pid)), ("nursing_handoff", _draft(pid, enc))):
        with pytest.raises(FhirAccessDenied):
            gw(store, Role.PHYSICIAN, module).create(resource)
    _, other = _inpatient(store, "ICU")
    with pytest.raises(BreakGlassRequired):
        gw(store, Role.PHYSICIAN, "discharge_summary").create(_task(other, enc))


def test_write_nurse(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    fhir = gw(store, Role.NURSE, "bedside_nursing")
    fhir.create(_flag(pid))
    fhir.create({"resourceType": "Communication", "status": "completed", "subject": ref("Patient", pid),
                 "payload": [{"contentString": "Family updated"}]})
    task = fhir.create(_task(pid, enc, "nurse"))
    task.status = "completed"
    fhir.update(task)
    with pytest.raises(FhirAccessDenied):
        gw(store, Role.NURSE, "registration").create(
            {"resourceType": "Appointment", "status": "proposed", "start": "2030-01-07T09:00:00-05:00",
             "end": "2030-01-07T09:30:00-05:00", "participant": [{"actor": ref("Patient", pid), "status": "needs-action"}]})


def test_write_pharmacist(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "ORTH")
    medrec_type = ("urn:demo-hospital:doc-type", "medrec", "Medication reconciliation record")
    doc = gw(store, Role.PHARMACIST, "medication_reconciliation").create(_draft(pid, enc, medrec_type))
    assert doc.docStatus == "preliminary"
    for module, resource in (("medication_reconciliation", _task(pid, enc)), ("order_review", _flag(pid)),
                             ("discharge_summary", _draft(pid, enc))):
        with pytest.raises(FhirAccessDenied):
            gw(store, Role.PHARMACIST, module).create(resource)


def test_write_clerk(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "SURG")
    fhir = gw(store, Role.CLERK, "registration")
    appt = fhir.create({"resourceType": "Appointment", "status": "proposed", "start": "2030-01-07T09:00:00-05:00",
                        "end": "2030-01-07T09:30:00-05:00",
                        "participant": [{"actor": ref("Patient", pid), "status": "needs-action"}]})
    assert appt.status == "proposed"
    fhir.create(_task(pid, enc, "clerk"))  # a registration task
    with pytest.raises(FhirAccessDenied):
        fhir.create(_task(pid, enc, "nurse"))
    with pytest.raises(FhirAccessDenied):
        gw(store, Role.CLERK, "bedside_nursing").create(_flag(pid))


def test_write_operations_manager(fresh_state):
    store = fresh_state
    fhir = gw(store, Role.OPERATIONS_MANAGER, "control_tower")
    unit, free = next((u, b) for u in ("ICU", "MEDB", "MEDA", "SURG", "ORTH")
                      for b in fhir.get_bed_board(u).beds if b.status == "U")
    enc, pid = _inpatient(store, unit)
    fhir.create(_task(pid, enc, "ops"))  # an action task
    moved = fhir.append_encounter_location(enc, free.id)
    assert moved.location[-1].location.reference == f"Location/{free.id}"
    with pytest.raises(FhirAccessDenied):
        fhir.create(_task(pid, enc))  # no performer: not an action task
    with pytest.raises(FhirAccessDenied):
        gw(store, Role.OPERATIONS_MANAGER, "discharge_summary").create(_draft(pid, enc))


def test_write_admin(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    for module, resource in (("control_tower", _task(pid, enc, "ops")), ("bedside_nursing", _flag(pid))):
        with pytest.raises(FhirAccessDenied):
            gw(store, Role.ADMIN, module).create(resource)
    with pytest.raises(FhirAccessDenied):
        gw(store, Role.ADMIN, "control_tower").append_encounter_location(enc, "MEDA-01-A")


def test_imaging_roles_have_no_hospital_access(fresh_state):
    store = fresh_state
    _, pid = _inpatient(store, "MEDA")
    for user_id in ("U-RAD", "U-FD", "U-MD"):
        fhir = FhirGateway(Actor.of(store.staff[user_id]), "patient_chart", LocalBackend(store))
        with pytest.raises(FhirAccessDenied):
            fhir.get_patient(_mrn(store, pid))


# ---------- the safety-tier registry ----------


def test_registry_entries_are_valid():
    entries = registry.modules()
    assert {"control_tower", "discharge_summary", "medication_reconciliation", "followup_calls"} <= set(entries)
    for name, entry in entries.items():
        assert entry.tier in ("ops", "documentation", "clinical_ds"), name
        if entry.tier != "ops":
            assert entry.required_signoff_role, f"{name}: documentation and clinical_ds modules need a signer"
            assert entry.allows("DocumentReference", "preliminary"), name
    assert entries["medication_reconciliation"].cosign and entries["discharge_summary"].consent_required == [
        "ai_processing"]


def test_writes_of_unregistered_modules_are_refused(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    nurse = gw(store, Role.NURSE, "shadow_module")
    with pytest.raises(FhirAccessDenied, match="no entry in the safety-tier registry"):
        nurse.create(_flag(pid))
    event = _last(store)
    assert (event.outcome, event.module, event.resource_type) == ("denied", "shadow_module", "Flag")
    with registry.temporary("shadow_module", {"tier": "ops", "required_signoff_role": ["nurse"],
                                              "writes_allowed": {"Flag": ["*"]}}):
        gw(store, Role.NURSE, "shadow_module").create(_flag(pid))  # registered: allowed
    with pytest.raises(FhirAccessDenied, match="may not write Communication"):
        gw(store, Role.NURSE, "patient_instructions").create(
            {"resourceType": "Communication", "status": "completed", "subject": ref("Patient", pid)})
    system = FhirGateway(Actor.system("job"), "shadow_module", LocalBackend(store))
    with pytest.raises(FhirAccessDenied):
        system.create(_task(pid, enc))  # trusted system actors still need a registered module


def test_documents_of_an_unregistered_module_cannot_be_signed(fresh_state):
    store = fresh_state
    enc, pid = _inpatient(store, "MEDA")
    with registry.temporary("pilot_notes", {"tier": "documentation", "required_signoff_role": ["physician"],
                                            "writes_allowed": {"DocumentReference": ["preliminary", "final"]}}):
        doc = gw(store, Role.PHYSICIAN, "pilot_notes").create(_draft(pid, enc))
    with pytest.raises(FhirAccessDenied, match="no entry"):
        gw(store, Role.PHYSICIAN, "signing").sign_document(doc.id)
    with pytest.raises(FhirAccessDenied, match="only a person"):
        FhirGateway(Actor.system("job"), "signing", LocalBackend(store)).sign_document(DISCHARGE)
