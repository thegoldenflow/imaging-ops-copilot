"""System 14 · CT Dose Monitoring.

Each completed CT gets a dose record shaped like a DICOM Radiation Dose
Structured Report (TID 10011 "CT Radiation Dose"): one entry per irradiation
event (scout, helical acquisition) with CTDIvol, DLP and technique, plus the
accumulated DLP. In the demo the "scanner" sends synthetic values. Each record
is compared with the reference level for its protocol; exceedances go to a
review list and trends are shown by scanner and protocol."""

import random
import statistics
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import Appointment, AppointmentStatus, ImagingStudy, Modality
from app.core.store import Store
from app.modules.protocols.library import BY_ID as PROTOCOLS
from app.modules.scheduling import service as scheduling

DEFAULT_PROTOCOL = {"CT_CHEST": "CT-CH-ROUTINE", "CT_CHEST_C": "CT-CH-CONTRAST", "CT_ABD_PEL": "CT-AP-CONTRAST",
                    "CT_HEAD": "CT-HD-ROUTINE"}
# Demo placeholder reference levels (CTDIvol mGy, DLP mGy·cm) per protocol.
DEFAULT_REFERENCES = {
    "CT-HD-ROUTINE": (60, 1000), "CT-CH-ROUTINE": (10, 400), "CT-CH-CONTRAST": (12, 450),
    "CT-CH-LDCT": (3, 120), "CT-AP-CONTRAST": (15, 750), "CT-AP-RENAL": (10, 450),
}
SCAN_LENGTH_CM = {"CT-HD-ROUTINE": 17, "CT-CH-ROUTINE": 34, "CT-CH-CONTRAST": 34, "CT-CH-LDCT": 34,
                  "CT-AP-CONTRAST": 48, "CT-AP-RENAL": 44}
HISTORY_DAYS = 90
DRIFT_SCANNER, DRIFT_DAYS = "EVW-CT1", 21  # seeded tube/protocol drift for the trend demo
REVIEW_OUTCOMES = {"justified": "Justified (patient size or clinical need)", "technique": "Technique issue, staff informed",
                   "protocol": "Protocol to be reviewed", "repeat": "Repeat acquisition"}


class ReferenceLevel(BaseModel):
    ctdivol_mgy: float = Field(gt=0)
    dlp_mgycm: float = Field(gt=0)


class IrradiationEvent(BaseModel):
    sequence: int
    protocol_step: str
    acquisition_type: str  # Stationary Acquisition, Spiral Acquisition
    kvp: int
    exposure_mas: int
    scanning_length_mm: int
    ctdivol_mgy: float
    dlp_mgycm: float
    phantom: str  # IEC Head Dosimetry Phantom (16 cm) / IEC Body Dosimetry Phantom (32 cm)


class DoseRecord(BaseModel):
    id: str
    appointment_id: str
    study_id: str | None
    patient_id: str
    scanner_id: str
    site_id: str
    exam_code: str
    protocol_id: str
    performed_at: datetime
    sr_template: str = "TID 10011 CT Radiation Dose"
    source: str = "RDSR from scanner (mock)"
    events: list[IrradiationEvent]
    ctdivol_mgy: float  # highest CTDIvol of the diagnostic acquisitions
    dlp_total_mgycm: float  # accumulated DLP
    review: dict | None = None  # {outcome, note, by, at}


def references(store: Store) -> dict[str, ReferenceLevel]:
    return store.module("dose_references", lambda: {
        pid: ReferenceLevel(ctdivol_mgy=c, dlp_mgycm=d) for pid, (c, d) in DEFAULT_REFERENCES.items()})


def records(store: Store) -> dict[str, DoseRecord]:
    return store.module("dose_records", dict)


def _by_appointment(store: Store) -> dict[str, str]:
    return store.module("dose_by_appointment", dict)


def protocol_for(appt: Appointment) -> str:
    if appt.protocol_id in DEFAULT_REFERENCES:
        return appt.protocol_id
    return DEFAULT_PROTOCOL[appt.exam_code]


def synthesize(rng: random.Random, protocol_id: str, scanner_id: str, performed_at: datetime, now: datetime) -> list[IrradiationEvent]:
    """Synthetic RDSR content: a scout plus one helical acquisition."""
    ref_c, _ = DEFAULT_REFERENCES[protocol_id]
    factor = rng.lognormvariate(-0.35, 0.17)
    days_ago = (now - performed_at).total_seconds() / 86400
    if scanner_id == DRIFT_SCANNER and days_ago < DRIFT_DAYS:
        factor *= 1 + 0.42 * (DRIFT_DAYS - days_ago) / DRIFT_DAYS  # creeping upwards
    if rng.random() < 0.02:
        factor *= 1.5  # large patient / repeat
    head = protocol_id.startswith("CT-HD")
    phantom = "IEC Head Dosimetry Phantom (16 cm)" if head else "IEC Body Dosimetry Phantom (32 cm)"
    length_cm = SCAN_LENGTH_CM[protocol_id] * rng.uniform(0.9, 1.12)
    ctdi = round(ref_c * factor, 2)
    low_dose = protocol_id == "CT-CH-LDCT"
    return [
        IrradiationEvent(sequence=1, protocol_step="Scout", acquisition_type="Stationary Acquisition", kvp=120,
                         exposure_mas=rng.randint(20, 40), scanning_length_mm=round(length_cm * 12),
                         ctdivol_mgy=round(rng.uniform(0.05, 0.2), 2), dlp_mgycm=round(rng.uniform(2, 6), 1), phantom=phantom),
        IrradiationEvent(sequence=2, protocol_step=PROTOCOLS[protocol_id].name, acquisition_type="Spiral Acquisition",
                         kvp=100 if low_dose else 120, exposure_mas=round(ctdi * (9 if head else 14)),
                         scanning_length_mm=round(length_cm * 10), ctdivol_mgy=ctdi, dlp_mgycm=round(ctdi * length_cm, 1),
                         phantom=phantom),
    ]


def make_record(store: Store, appt: Appointment, study_id: str | None, performed_at: datetime,
                rng: random.Random, now: datetime) -> DoseRecord:
    protocol_id = protocol_for(appt)
    events = synthesize(rng, protocol_id, appt.scanner_id, performed_at, now)
    rec = DoseRecord(
        id=store.next_id("DOSE"), appointment_id=appt.id, study_id=study_id, patient_id=appt.patient_id,
        scanner_id=appt.scanner_id, site_id=appt.site_id, exam_code=appt.exam_code, protocol_id=protocol_id,
        performed_at=performed_at, events=events,
        ctdivol_mgy=max(e.ctdivol_mgy for e in events if e.acquisition_type != "Stationary Acquisition"),
        dlp_total_mgycm=round(sum(e.dlp_mgycm for e in events), 1),
    )
    records(store)[rec.id] = rec
    _by_appointment(store)[appt.id] = rec.id
    return rec


def on_completed(store: Store, appt: Appointment, study: ImagingStudy) -> None:
    if store.exams[appt.exam_code].modality == Modality.CT:
        make_record(store, appt, study.id, study.performed_at, random.Random(), datetime.now())


scheduling.COMPLETION_HOOKS.append(on_completed)


def exceedance(store: Store, rec: DoseRecord) -> dict | None:
    """Computed on read, so changing a reference level re-evaluates every record."""
    ref = references(store)[rec.protocol_id]
    over = []
    if rec.ctdivol_mgy > ref.ctdivol_mgy:
        over.append("CTDIvol")
    if rec.dlp_total_mgycm > ref.dlp_mgycm:
        over.append("DLP")
    if not over:
        return None
    return {"metrics": over, "ctdivol_ratio": round(rec.ctdivol_mgy / ref.ctdivol_mgy, 2),
            "dlp_ratio": round(rec.dlp_total_mgycm / ref.dlp_mgycm, 2)}


def coverage(store: Store, since: datetime) -> dict:
    """Completed CT exams in the window and how many have a dose record."""
    ct = [a for a in store.appointments.values() if a.status == AppointmentStatus.COMPLETED
          and store.exams[a.exam_code].modality == Modality.CT and a.start >= since]
    with_record = sum(a.id in _by_appointment(store) for a in ct)
    return {"ct_exams": len(ct), "with_record": with_record, "missing": len(ct) - with_record}


def weekly_trend(store: Store, key, now: datetime, weeks: int = 13) -> dict:
    """Median CTDIvol as % of the protocol reference, per week, grouped by `key(record)`."""
    start = (now - timedelta(weeks=weeks - 1)).date()
    start -= timedelta(days=start.weekday())
    labels = [(start + timedelta(weeks=i)) for i in range(weeks)]
    buckets: dict[str, dict[int, list[float]]] = {}
    refs = references(store)
    for rec in records(store).values():
        week = (rec.performed_at.date() - start).days // 7
        if 0 <= week < weeks:
            ratio = rec.ctdivol_mgy / refs[rec.protocol_id].ctdivol_mgy * 100
            buckets.setdefault(key(rec), {}).setdefault(week, []).append(ratio)
    series = [{"name": name, "values": [round(statistics.median(w[i]), 1) if i in w else None for i in range(weeks)]}
              for name, w in sorted(buckets.items())]
    return {"labels": [d.strftime("%b %d") for d in labels], "series": series}


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Dose records for every completed CT exam in the last 90 days."""
    study_by_appt = {st.appointment_id: st.id for st in s.studies.values() if st.appointment_id}
    since = now - timedelta(days=HISTORY_DAYS)
    for appt in sorted(s.appointments.values(), key=lambda a: a.start):
        if appt.status == AppointmentStatus.COMPLETED and appt.start >= since and s.exams[appt.exam_code].modality == Modality.CT:
            make_record(s, appt, study_by_appt.get(appt.id), min(appt.end, now), rng, now)
    # Older exceedances have been reviewed; recent ones are waiting.
    for rec in records(s).values():
        if exceedance(s, rec) and rec.performed_at < now - timedelta(days=7) and rng.random() < 0.85:
            rec.review = {"outcome": rng.choice(["justified", "justified", "technique"]),
                          "note": "Reviewed at the monthly dose meeting", "by": "Sam Rivera",
                          "at": (rec.performed_at + timedelta(days=rng.randint(2, 8))).isoformat(timespec="minutes")}
