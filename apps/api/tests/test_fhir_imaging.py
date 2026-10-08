"""WP2: the imaging exam <-> FHIR adapter (spec 6.1): Exam -> FHIR -> Exam is lossless."""

import base64

import pytest

from app.core.models import AppointmentStatus
from app.ehr.imaging import ImagingExam, exam_from_fhir, exam_names, exam_to_fhir, load_exam
from app.fhir.types import validate
from app.modules.reports.service import generate_draft, report_for_study, save, sign
from app.modules.scheduling import noshow


def _samples(store) -> dict[str, ImagingExam]:
    """20 exams covering every appointment status, studies with and without reports,
    dictated and AI-drafted reports (draft and signed) and studies without an appointment."""
    noshow.score_upcoming(store, noshow.get_model(store))  # no-show risk and its factors on upcoming exams
    appts = sorted(store.appointments.values(), key=lambda a: a.id)
    by_appt = {s.appointment_id: s for s in store.studies.values() if s.appointment_id}
    picked: dict[str, ImagingExam] = {}

    def take(label: str, n: int, pred) -> None:
        found = [a for a in appts if a.id not in {e.key for e in picked.values()} and pred(a)][:n]
        assert len(found) == n, f"seed has fewer than {n} exams for {label}"
        for i, a in enumerate(found):
            picked[f"{label}-{i}"] = load_exam(store, appointment_id=a.id)

    take("booked", 2, lambda a: a.status == AppointmentStatus.BOOKED)
    take("confirmed-with-risk", 1, lambda a: a.status == AppointmentStatus.CONFIRMED and a.risk_factors)
    take("confirmed", 1, lambda a: a.status == AppointmentStatus.CONFIRMED)
    take("cancelled", 2, lambda a: a.status == AppointmentStatus.CANCELLED and a.cancel_reason)
    take("no-show", 2, lambda a: a.status == AppointmentStatus.NO_SHOW)
    take("completed-no-study", 2, lambda a: a.status == AppointmentStatus.COMPLETED and a.id not in by_appt)
    take("read", 4, lambda a: a.id in by_appt and report_for_study(store, by_appt[a.id].id) is not None)
    take("unread", 1, lambda a: a.id in by_appt and report_for_study(store, by_appt[a.id].id) is None)

    # The chest X-ray worklist: studies without an appointment, drafted by the (mock) AI
    worklist = sorted((s for s in store.studies.values() if s.appointment_id is None), key=lambda s: s.id)
    draft = generate_draft(store, worklist[0])
    save(store, draft)
    picked["ai-draft"] = load_exam(store, study_id=worklist[0].id)
    signed = generate_draft(store, worklist[1])
    signed.sections[0].final_text, signed.sections[0].status = "Edited by the radiologist.", "edited"
    signed.sections[1].final_text, signed.sections[1].status = "", "deleted"
    save(store, signed)
    sign(store, signed, "Dr. Demo Radiologist", confirmed_urgent=[0], signer_id="U-RAD")
    picked["ai-signed"] = load_exam(store, study_id=worklist[1].id)
    picked["worklist-unread"] = load_exam(store, study_id=worklist[2].id)

    # Booked from a requisition with an approved protocol (as the phase 2 pipeline books it)
    booked = picked["booked-1"].appointment
    requisition = sorted(store.requisitions)[0]
    picked["requisition-0"] = ImagingExam(booked.model_copy(update={
        "id": "AP-FROM-REQ", "requisition_id": requisition, "protocol_id": "CT-AP-CONTRAST", "extra_reminder": True}))
    # An outside prior (imported by system 10) with the fields only priors have
    exam = picked["read-0"]
    picked["outside-prior"] = ImagingExam(None, exam.study.model_copy(update={
        "id": "ST-PRIOR", "appointment_id": None, "source_facility": "Riverside Health Centre",
        "prior_for_appointment_id": exam.appointment.id, "indication": "Outside prior: CT chest"}))
    assert len(picked) == 20
    return picked


@pytest.fixture
def samples(fresh_state):
    return _samples(fresh_state)


def test_twenty_exams_round_trip_without_loss(fresh_state, samples):
    names = exam_names(fresh_state)
    for label, exam in samples.items():
        resources = exam_to_fhir(exam, names)
        back = exam_from_fhir(resources.service_request, resources.encounter, resources.diagnostic_report)
        assert back == exam, label


def test_fhir_side_is_valid_and_round_trips_too(fresh_state, samples):
    """Every resource validates against the FHIR types, and FHIR -> exam -> FHIR gives the same resources."""
    names = exam_names(fresh_state)
    for label, exam in samples.items():
        resources = exam_to_fhir(exam, names)
        for resource in resources.all():
            assert validate(resource).to_fhir() == resource, label
            assert "" not in _strings(resource), f"{label}: FHIR has no empty strings"
        back = exam_from_fhir(resources.service_request, resources.encounter, resources.diagnostic_report)
        assert exam_to_fhir(back, names).all() == resources.all(), label


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def test_standard_elements_carry_the_exam(fresh_state, samples):
    """What a FHIR consumer (order review, discharge summary) reads without the extensions."""
    exam = samples["ai-signed"]
    res = exam_to_fhir(exam, exam_names(fresh_state))
    sr, enc, dr = res.service_request, res.encounter, res.diagnostic_report
    assert sr["code"]["coding"][0]["system"] == "urn:demo-imaging:exam" and sr["code"]["coding"][0]["code"] == "XR_CHEST"
    assert sr["priority"] == "urgent" and sr["subject"] == {"reference": f"Patient/{exam.study.patient_id}"}
    assert enc["class"]["code"] == "AMB" and enc["status"] == "finished"
    assert {"system": "urn:dicom:uid", "value": f"urn:oid:{exam.study.study_uid}"} in enc["identifier"]
    assert dr["status"] == "final" and dr["resultsInterpreter"] == [{"reference": "Practitioner/U-RAD",
                                                                     "display": "Dr. Demo Radiologist"}]
    assert dr["basedOn"] == [{"reference": f"ServiceRequest/{sr['id']}"}]
    assert dr["encounter"] == {"reference": f"Encounter/{enc['id']}"}
    text = base64.b64decode(dr["presentedForm"][0]["data"]).decode()
    assert "Edited by the radiologist." in text and exam.report.sections[1].label not in text  # deleted section left out
    assert dr["conclusion"] == next(s.final_text for s in exam.report.sections if s.key == "impression")

    booked = exam_to_fhir(samples["booked-0"])
    assert booked.encounter["status"] == "planned" and booked.service_request["status"] == "active"
    assert booked.diagnostic_report is None
    cancelled = exam_to_fhir(samples["cancelled-0"])
    assert cancelled.encounter["status"] == "cancelled" and cancelled.service_request["status"] == "revoked"
    with_req = exam_to_fhir(samples["requisition-0"])
    assert with_req.service_request["requisition"]["value"] == samples["requisition-0"].appointment.requisition_id


def test_an_exam_needs_an_appointment_or_a_study():
    with pytest.raises(ValueError):
        exam_to_fhir(ImagingExam())
