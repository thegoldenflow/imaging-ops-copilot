"""Imaging exam <-> FHIR (spec 6.1): the imaging center's booked exam as hospital resources.

The imaging modules keep their own tables. An exam there is an `Appointment`,
plus its `ImagingStudy` once performed and its `Report` once read (or a study
without an appointment, like the chest X-ray worklist). It maps to

    ServiceRequest      the order: exam code, priority, referrer, requisition, protocol, indication
    Encounter (AMB)     the visit: booked slot, site, status; the study once performed
    DiagnosticReport    the report, when there is one: status, signer, conclusion, full text

Fields with a FHIR element are written there and read back from there. The
imaging-only workflow fields (no-show risk, reminder state, AI draft sections,
edit ratio, ...) go into typed complex extensions `urn:demo-hospital:ext:imaging-*`
(one sub-extension per field), so `exam_from_fhir(exam_to_fhir(exam)) == exam`.
FHIR has no empty strings, so empty text is left out and an empty optional text
comes back as None.

References point at the imaging records (`Patient/PT-...`, `Practitioner/R-...`):
linking imaging patients to hospital MRNs is the master-patient-index roadmap card (8.1).
"""

from __future__ import annotations

import base64
import types
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from app.core.models import Appointment, ImagingStudy
from app.ehr.codes import EXT, LOCAL, coding, concept
from app.fhir.dt import LOCAL_TZ, parse, ref, ref_id
from app.modules.reports.service import Report, report_for_study

IMG = "urn:demo-imaging:"
APPOINTMENT_ID, STUDY_ID, REPORT_ID = IMG + "appointment", IMG + "study", IMG + "report"
REQUISITION_ID, EXAM_CODE, PROTOCOL = IMG + "requisition", IMG + "exam", IMG + "protocol"
DICOM_UID = "urn:dicom:uid"
RADIOLOGY = ("http://terminology.hl7.org/CodeSystem/v2-0074", "RAD", "Radiology")
IMAGING_CATEGORY = ("http://snomed.info/sct", "363679005", "Imaging")

PRIORITY = {"P1": "stat", "P2": "asap", "P3": "urgent", "P4": "routine"}
ENCOUNTER_STATUS = {"booked": "planned", "confirmed": "planned", "completed": "finished",
                    "cancelled": "cancelled", "no_show": "cancelled"}
ORDER_STATUS = {"booked": "active", "confirmed": "active", "completed": "completed",
                "cancelled": "revoked", "no_show": "revoked"}
REPORT_STATUS = {"draft": "preliminary", "signed": "final"}

# Fields without a FHIR element, carried in the imaging-* extensions.
APPOINTMENT_EXTRA = ("status", "scanner_id", "reminder_confirmed", "cancel_reason", "no_show_risk", "risk_factors",
                     "extra_reminder")
STUDY_EXTRA = ("appointment_id", "patient_id", "referrer_id", "exam_code", "performed_at", "image_key",
               "source_facility", "prior_for_appointment_id", "site_id", "scanner_id", "priority", "protocol_id")
REPORT_EXTRA = ("study_id", "referrer_id", "source", "ai_status", "ai_error", "image_quality", "sections",
                "urgent_findings", "uncertainties", "model", "prompt_version", "llm_mode", "llm_call_id", "created_at",
                "edit_ratio", "sent_to_referrer_at")


@dataclass
class ImagingExam:
    appointment: Appointment | None = None
    study: ImagingStudy | None = None
    report: Report | None = None

    @property
    def key(self) -> str:
        return self.appointment.id if self.appointment else self.study.id


@dataclass
class ExamResources:
    service_request: dict
    encounter: dict
    diagnostic_report: dict | None = None

    def all(self) -> list[dict]:
        return [r for r in (self.service_request, self.encounter, self.diagnostic_report) if r]


# ---------- extension codec ----------


def _instant(value: datetime) -> str:
    """A naive local datetime as FHIR dateTime, keeping microseconds (fhir_datetime drops them)."""
    return value.replace(tzinfo=LOCAL_TZ).isoformat()


def _value(value) -> dict:
    if isinstance(value, Enum):
        return {"valueCode": value.value}
    if isinstance(value, bool):
        return {"valueBoolean": value}
    if isinstance(value, int):
        return {"valueInteger": value}
    if isinstance(value, float):
        return {"valueDecimal": value}
    if isinstance(value, datetime):
        return {"valueDateTime": _instant(value)}
    if isinstance(value, str):
        return {"valueString": value}
    raise TypeError(f"No FHIR value type for {type(value).__name__}")


def _encode(obj: BaseModel, fields: Iterable[str]) -> list[dict]:
    out = []
    for name in fields:
        value = getattr(obj, name)
        for item in value if isinstance(value, list) else [value]:
            if item is None or item == "":
                continue
            if isinstance(item, BaseModel):
                out.append({"url": name, "extension": _encode(item, type(item).model_fields)})
            else:
                out.append({"url": name, **_value(item)})
    return out


def _shape(annotation) -> tuple[bool, type[BaseModel] | None]:
    """(is a list, nested model class or None) for a field annotation."""
    if get_origin(annotation) in (Union, types.UnionType):
        annotation = next(a for a in get_args(annotation) if a is not type(None))
    is_list = get_origin(annotation) is list
    if is_list:
        annotation = get_args(annotation)[0]
    nested = annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None
    return is_list, nested


def _read(sub: dict):
    if "valueDateTime" in sub:
        return parse(sub["valueDateTime"])
    return next(v for k, v in sub.items() if k.startswith("value"))


def _decode(subs: list[dict], model: type[BaseModel], fields: Iterable[str]) -> dict:
    data = {}
    for name in fields:
        info = model.model_fields[name]
        is_list, nested = _shape(info.annotation)
        values = [_decode(s.get("extension", []), nested, nested.model_fields) if nested else _read(s)
                  for s in subs if s["url"] == name]
        if is_list:
            data[name] = values
        elif values:
            data[name] = values[0]
        elif info.is_required():
            optional = get_origin(info.annotation) in (Union, types.UnionType) and type(None) in get_args(info.annotation)
            data[name] = None if optional else ""  # FHIR has no empty strings
    return data


def _pack(name: str, obj: BaseModel, fields: Iterable[str]) -> dict:
    return {"url": EXT + name, "extension": _encode(obj, fields)}


def _unpack(resource: dict, name: str, model: type[BaseModel], fields: Iterable[str]) -> dict | None:
    ext = next((e for e in resource.get("extension") or [] if e["url"] == EXT + name), None)
    return None if ext is None else _decode(ext.get("extension", []), model, fields)


# ---------- small helpers ----------


def _identifier(system: str, value: str) -> dict:
    return {"system": system, "value": value}


def _find(identifiers: list[dict] | None, system: str) -> str | None:
    return next((i["value"] for i in identifiers or [] if i.get("system") == system), None)


def _exam_concept(code: str, names: Mapping[str, str] | None) -> dict:
    return concept(EXAM_CODE, code, (names or {}).get(code))


def _report_text(report: Report) -> str:
    parts = [f"{s.label}: {s.final_text}" for s in report.sections if s.status != "deleted" and s.final_text]
    return "\n\n".join(parts)


def _reverse(mapping: dict[str, str]) -> dict[str, str]:
    return {v: k for k, v in mapping.items()}


# ---------- exam -> FHIR ----------


def exam_to_fhir(exam: ImagingExam, exam_names: Mapping[str, str] | None = None) -> ExamResources:
    """The exam as ServiceRequest + Encounter (+ DiagnosticReport). `exam_names` (code -> name)
    adds display text to the exam codes."""
    appt, study, report = exam.appointment, exam.study, exam.report
    if appt is None and study is None:
        raise ValueError("An imaging exam needs an appointment or a study")
    key = exam.key
    order_id, encounter_id = f"img-ord-{key}", f"img-enc-{key}"
    main = appt or study  # the record the order's standard elements come from
    patient = ref("Patient", main.patient_id)
    exam_code = _exam_concept(main.exam_code, exam_names)
    urgency = appt.urgency if appt else study.priority
    protocol = appt.protocol_id if appt else study.protocol_id

    sr: dict = {"resourceType": "ServiceRequest", "id": order_id,
                "identifier": [_identifier(APPOINTMENT_ID, appt.id)] if appt else [_identifier(STUDY_ID, study.id)],
                "status": ORDER_STATUS[appt.status] if appt else "completed", "intent": "order",
                "priority": PRIORITY[urgency], "category": [concept(*IMAGING_CATEGORY)], "code": exam_code,
                "subject": patient, "requester": ref("Practitioner", main.referrer_id)}
    if appt and appt.requisition_id:
        sr["requisition"] = _identifier(REQUISITION_ID, appt.requisition_id)
    if protocol:
        sr["orderDetail"] = [concept(PROTOCOL, protocol)]
    if appt:
        sr["authoredOn"] = _instant(appt.booked_at)
    sr["occurrenceDateTime"] = _instant(appt.start if appt else study.performed_at)
    if study and study.indication:
        sr["reasonCode"] = [{"text": study.indication}]

    status = ENCOUNTER_STATUS[appt.status] if appt else "finished"
    start, end = (appt.start, appt.end) if appt else (study.performed_at, None)
    site = appt.site_id if appt else study.site_id
    enc: dict = {"resourceType": "Encounter", "id": encounter_id,
                 "identifier": [_identifier(APPOINTMENT_ID, appt.id)] if appt else [],
                 "status": status, "class": coding("http://terminology.hl7.org/CodeSystem/v3-ActCode", "AMB", "ambulatory"),
                 "serviceType": concept(LOCAL, "imaging", "Diagnostic imaging"), "subject": patient,
                 "basedOn": [ref("ServiceRequest", order_id)],
                 "period": {"start": _instant(start), **({"end": _instant(end)} if end else {})}}
    if study:
        enc["identifier"] += [_identifier(STUDY_ID, study.id), _identifier(DICOM_UID, f"urn:oid:{study.study_uid}")]
    if site:
        enc["location"] = [{"location": ref("Location", site)}]
        if loc_status := {"planned": "planned", "finished": "completed"}.get(status):
            enc["location"][0]["status"] = loc_status
    extensions = []
    if appt:
        extensions.append(_pack("imaging-appointment", appt, APPOINTMENT_EXTRA))
    if study:
        extensions.append(_pack("imaging-study", study, STUDY_EXTRA))
    enc["extension"] = extensions
    if not enc["identifier"]:
        del enc["identifier"]

    dr = None
    if report:
        dr = {"resourceType": "DiagnosticReport", "id": f"img-rpt-{report.id}",
              "identifier": [_identifier(REPORT_ID, report.id)], "status": REPORT_STATUS[report.status],
              "category": [concept(*RADIOLOGY)], "code": exam_code, "subject": ref("Patient", report.patient_id),
              "encounter": ref("Encounter", encounter_id), "basedOn": [ref("ServiceRequest", order_id)]}
        if study:
            dr["effectiveDateTime"] = _instant(study.performed_at)
        if report.signed_at:
            dr["issued"] = _instant(report.signed_at)
        if report.signed_by or report.signed_by_id:
            interpreter = {}
            if report.signed_by_id:
                interpreter["reference"] = f"Practitioner/{report.signed_by_id}"
            if report.signed_by:
                interpreter["display"] = report.signed_by
            dr["resultsInterpreter"] = [interpreter]
        impression = next((s.final_text for s in report.sections if s.key == "impression" and s.status != "deleted"), "")
        if impression:
            dr["conclusion"] = impression
        if text := _report_text(report):
            dr["presentedForm"] = [{"contentType": "text/plain", "language": "en", "title": "Imaging report",
                                    "data": base64.b64encode(text.encode()).decode()}]
        dr["extension"] = [_pack("imaging-report", report, REPORT_EXTRA)]
    return ExamResources(sr, enc, dr)


# ---------- FHIR -> exam ----------


def exam_from_fhir(service_request: dict, encounter: dict, diagnostic_report: dict | None = None) -> ImagingExam:
    """The inverse of exam_to_fhir."""
    sr, enc, dr = service_request, encounter, diagnostic_report
    appointment = study = report = None

    extra = _unpack(enc, "imaging-appointment", Appointment, APPOINTMENT_EXTRA)
    if extra is not None:
        period = enc["period"]
        protocol = sr.get("orderDetail") or []
        appointment = Appointment(
            id=_find(sr.get("identifier"), APPOINTMENT_ID), patient_id=ref_id(sr["subject"]),
            referrer_id=ref_id(sr["requester"]), site_id=ref_id(enc["location"][0]["location"]),
            exam_code=sr["code"]["coding"][0]["code"], start=parse(period["start"]), end=parse(period["end"]),
            urgency=_reverse(PRIORITY)[sr["priority"]], booked_at=parse(sr["authoredOn"]),
            requisition_id=(sr.get("requisition") or {}).get("value"),
            protocol_id=protocol[0]["coding"][0]["code"] if protocol else None, **extra)

    extra = _unpack(enc, "imaging-study", ImagingStudy, STUDY_EXTRA)
    if extra is not None:
        uid = _find(enc.get("identifier"), DICOM_UID)
        study = ImagingStudy(id=_find(enc["identifier"], STUDY_ID), study_uid=uid.removeprefix("urn:oid:"),
                             indication=((sr.get("reasonCode") or [{}])[0]).get("text", ""), **extra)

    if dr is not None:
        extra = _unpack(dr, "imaging-report", Report, REPORT_EXTRA)
        interpreter = (dr.get("resultsInterpreter") or [{}])[0]
        report = Report(id=_find(dr["identifier"], REPORT_ID), patient_id=ref_id(dr["subject"]),
                        status=_reverse(REPORT_STATUS)[dr["status"]], signed_at=parse(dr.get("issued")),
                        signed_by=interpreter.get("display"), signed_by_id=ref_id(interpreter.get("reference")),
                        **extra)
    return ImagingExam(appointment, study, report)


# ---------- loading from the imaging tables ----------


def load_exam(store, *, appointment_id: str | None = None, study_id: str | None = None) -> ImagingExam:
    """An exam from the imaging tables, by appointment (with its study and report) or by study."""
    appointment = store.appointments.get(appointment_id) if appointment_id else None
    if study_id:
        study = store.studies.get(study_id)
    elif appointment:
        study = next((s for s in store.studies.values() if s.appointment_id == appointment.id), None)
    else:
        study = None
    if appointment is None and study is None:
        raise KeyError(appointment_id or study_id)
    if appointment is None and study.appointment_id:
        appointment = store.appointments.get(study.appointment_id)
    return ImagingExam(appointment, study, report_for_study(store, study.id) if study else None)


def exam_names(store) -> dict[str, str]:
    return {code: exam.name for code, exam in store.exams.items()}
