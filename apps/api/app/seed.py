"""Synthetic data generator. Everything here is fictional.

Default scale: 5 sites, 12 scanners, 2000 patients, 150 referrers and about
three months of appointment history (with cancellations and no-shows) plus two
weeks of upcoming bookings, and the demo-storyline patients.
"""

import math
import random
from datetime import date, datetime, timedelta

from app.core.models import (
    Allergy,
    Appointment,
    AppointmentStatus,
    Exam,
    ImagingStudy,
    LabResult,
    Modality,
    Patient,
    Referrer,
    Requisition,
    Role,
    Scanner,
    Site,
    StaffUser,
    WaitlistEntry,
)
from app.core.store import Store
from app.phantom import chest_phantom

HISTORY_DAYS = 90
FUTURE_DAYS = 14

SITES = [
    # id, name, x, y, open, close, days, occupancy
    ("LKS", "Lakeshore Imaging Centre", 0.0, 0.0, 7, 21, [0, 1, 2, 3, 4, 5], 0.93),
    ("NGT", "Northgate Imaging Centre", 2.0, 9.0, 8, 20, [0, 1, 2, 3, 4, 5], 0.80),
    ("WBK", "Westbrook Imaging Centre", -8.0, 3.0, 8, 18, [0, 1, 2, 3, 4], 0.45),
    ("EVW", "Eastview Imaging Centre", 9.0, 2.0, 8, 20, [0, 1, 2, 3, 4, 5], 0.62),
    ("MTN", "Midtown Imaging Centre", 1.0, 4.0, 8, 18, [0, 1, 2, 3, 4], 0.70),
]

SCANNERS = [
    ("LKS", Modality.MRI), ("LKS", Modality.CT), ("LKS", Modality.US), ("LKS", Modality.XR),
    ("NGT", Modality.MRI), ("NGT", Modality.CT), ("NGT", Modality.US),
    ("WBK", Modality.US), ("WBK", Modality.XR),
    ("EVW", Modality.CT), ("EVW", Modality.MRI),
    ("MTN", Modality.XR),
]

SLOT_MINUTES = {Modality.MRI: 45, Modality.CT: 30, Modality.US: 30, Modality.XR: 20}

EXAMS = [
    Exam(code="MR_BRAIN", name="MRI Brain", modality=Modality.MRI, minutes=45, value=5, prep_hours=2),
    Exam(code="MR_LSPINE", name="MRI Lumbar Spine", modality=Modality.MRI, minutes=45, value=5, prep_hours=2),
    Exam(code="MR_KNEE", name="MRI Knee", modality=Modality.MRI, minutes=45, value=4, prep_hours=2),
    Exam(code="CT_CHEST_C", name="CT Chest with Contrast", modality=Modality.CT, minutes=30, value=4, prep_hours=4, contrast=True),
    Exam(code="CT_ABD_PEL", name="CT Abdomen/Pelvis", modality=Modality.CT, minutes=30, value=4, prep_hours=4, contrast=True),
    Exam(code="CT_HEAD", name="CT Head", modality=Modality.CT, minutes=30, value=3, prep_hours=0),
    Exam(code="CT_CHEST", name="CT Chest without Contrast", modality=Modality.CT, minutes=30, value=3, prep_hours=0),
    Exam(code="US_ABD", name="Ultrasound Abdomen", modality=Modality.US, minutes=30, value=2, prep_hours=8),
    Exam(code="US_PELVIS", name="Ultrasound Pelvis", modality=Modality.US, minutes=30, value=2, prep_hours=2),
    Exam(code="US_THYROID", name="Ultrasound Thyroid", modality=Modality.US, minutes=30, value=2, prep_hours=0),
    Exam(code="US_VENOUS", name="Ultrasound Venous Doppler", modality=Modality.US, minutes=30, value=2, prep_hours=0),
    Exam(code="XR_CHEST", name="X-ray Chest", modality=Modality.XR, minutes=20, value=1, prep_hours=0),
    Exam(code="XR_KNEE", name="X-ray Knee", modality=Modality.XR, minutes=20, value=1, prep_hours=0),
    Exam(code="XR_LSPINE", name="X-ray Lumbar Spine", modality=Modality.XR, minutes=20, value=1, prep_hours=0),
]

GIVEN = {
    "en": ["James", "Olivia", "Liam", "Emma", "Noah", "Ava", "Lucas", "Sophia", "Ethan", "Mia", "Owen", "Grace",
           "Jack", "Chloe", "Ryan", "Hannah", "Nathan", "Ella", "Daniel", "Zoe", "Michael", "Sarah", "David", "Laura"],
    "fr": ["Julien", "Camille", "Mathieu", "Chloé", "Antoine", "Léa", "Étienne", "Juliette", "Gabriel", "Manon"],
    "zh": ["Wei", "Mei", "Jun", "Lin", "Hao", "Xin", "Yu", "Ling", "Jie", "Fang", "Ming", "Yan"],
    "pa": ["Harpreet", "Gurpreet", "Manpreet", "Jaspreet", "Amrit", "Simran", "Navdeep", "Rajveer", "Kiran", "Arjun"],
}
FAMILY = {
    "en": ["Smith", "Brown", "Wilson", "Taylor", "Campbell", "Anderson", "Clark", "Wright", "Mitchell", "Thompson",
           "Walker", "Young", "Scott", "Green", "Baker", "Hill", "Reid", "Stewart", "Murray", "Ross"],
    "fr": ["Tremblay", "Gagnon", "Roy", "Côté", "Bouchard", "Gauthier", "Morin", "Lavoie", "Fortin", "Pelletier"],
    "zh": ["Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Huang", "Zhao", "Wu", "Zhou"],
    "pa": ["Singh", "Kaur", "Gill", "Sandhu", "Dhillon", "Grewal", "Brar", "Sidhu", "Sekhon", "Bains"],
}
LANG_WEIGHTS = [("en", 0.70), ("fr", 0.08), ("zh", 0.12), ("pa", 0.10)]
STREETS = ["Example St", "Sample Ave", "Placeholder Rd", "Fictional Blvd", "Demo Cres", "Mock Lane"]
SPECIALTIES = [("Family Medicine", 0.55), ("Orthopedics", 0.1), ("Neurology", 0.08), ("Oncology", 0.08),
               ("Internal Medicine", 0.1), ("Obstetrics/Gynecology", 0.05), ("Respirology", 0.04)]



def _weighted(rng: random.Random, pairs):
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


def _phone(rng: random.Random) -> str:
    # 555-01xx numbers are reserved for fiction.
    return f"+1-416-555-01{rng.randint(0, 99):02d}"


def _health_card(rng: random.Random) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(10))


def _no_show_probability(lead_days: float, prior_no_shows: int, weekday: int, hour: int,
                         reminder_confirmed: bool, age: int) -> float:
    """Ground truth used to generate history. The model has to learn it back."""
    z = -3.1
    z += 0.035 * min(lead_days, 60)
    z += 0.85 * min(prior_no_shows, 3)
    z += 0.35 if weekday == 0 else 0.0
    z += 0.45 if hour < 9 or hour >= 18 else 0.0
    z -= 1.3 if reminder_confirmed else 0.0
    z += 0.4 if age < 30 else 0.0
    return 1 / (1 + math.exp(-z))


def build_store(seed: int | None = None) -> Store:
    from app.core.config import settings

    rng = random.Random(settings.seed if seed is None else seed)
    s = Store()
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    today = now.date()

    for exam in EXAMS:
        s.exams[exam.code] = exam

    occupancy = {}
    for sid, name, x, y, open_h, close_h, days, occ in SITES:
        s.sites[sid] = Site(
            id=sid, name=name, address=f"{rng.randint(10, 990)} {rng.choice(STREETS)}, Demo City, ON",
            phone=_phone(rng), open_hour=open_h, close_hour=close_h, open_days=days, modalities=[],
            x_km=x, y_km=y, parking="Free underground parking; entrance on the east side.",
        )
        occupancy[sid] = occ
    for i, (sid, modality) in enumerate(SCANNERS):
        scanner_id = f"{sid}-{modality.value}1"
        s.scanners[scanner_id] = Scanner(id=scanner_id, site_id=sid, modality=modality,
                                         name=f"{modality.value} 1", slot_minutes=SLOT_MINUTES[modality])
        if modality not in s.sites[sid].modalities:
            s.sites[sid].modalities.append(modality)

    # Referrers
    for i in range(150):
        lang = _weighted(rng, LANG_WEIGHTS)
        rid = f"R-{i + 1:04d}"
        s.referrers[rid] = Referrer(
            id=rid, name=f"Dr. {rng.choice(GIVEN[lang])} {rng.choice(FAMILY[lang])}",
            specialty=_weighted(rng, SPECIALTIES), clinic=f"{rng.choice(FAMILY['en'])} Medical Clinic",
            phone=_phone(rng), fax=_phone(rng), is_key=rng.random() < 0.15,
        )
    referrer_ids = list(s.referrers)

    # Patients
    for i in range(2000):
        lang = _weighted(rng, LANG_WEIGHTS)
        given, family = rng.choice(GIVEN[lang]), rng.choice(FAMILY[lang])
        pid = f"PT-{i + 1:05d}"
        s.patients[pid] = Patient(
            id=pid, given_name=given, family_name=family,
            dob=date(rng.randint(1940, 2008), rng.randint(1, 12), rng.randint(1, 28)),
            sex=rng.choice(["F", "M"]), phone=_phone(rng),
            email=f"{given.lower()}.{family.lower()}{i}@example.com".replace(" ", ""),
            address=f"{rng.randint(1, 999)} {rng.choice(STREETS)}, Demo City, ON",
            health_card=_health_card(rng), health_card_version=rng.choice(["AB", "CD", "EF", "GH"]),
            preferred_language=lang,
        )
    patient_ids = list(s.patients)

    # Appointments: fill each scanner's day with slots, chronologically.
    exams_by_modality: dict[Modality, list[Exam]] = {}
    for exam in EXAMS:
        exams_by_modality.setdefault(exam.modality, []).append(exam)

    slots = []
    for scanner in s.scanners.values():
        site = s.sites[scanner.site_id]
        for offset in range(-HISTORY_DAYS, FUTURE_DAYS + 1):
            day = today + timedelta(days=offset)
            if day.weekday() not in site.open_days:
                continue
            fill = occupancy[site.id]
            if offset > 0:
                # Busy sites stay busy further ahead; quiet sites thin out quickly.
                fill = fill * max(0.4, 1 - (1 - fill) * offset * 0.08)
            start = datetime.combine(day, datetime.min.time()) + timedelta(hours=site.open_hour)
            close = datetime.combine(day, datetime.min.time()) + timedelta(hours=site.close_hour)
            while start + timedelta(minutes=scanner.slot_minutes) <= close:
                if rng.random() < fill:
                    slots.append((start, scanner))
                start += timedelta(minutes=scanner.slot_minutes)
    slots.sort(key=lambda item: item[0])

    prior_no_shows: dict[str, int] = {}
    for start, scanner in slots:
        pid = rng.choice(patient_ids)
        patient = s.patients[pid]
        exam = rng.choice(exams_by_modality[scanner.modality])
        lead_days = rng.choice([1, 2, 3, 5, 7, 10, 14, 21, 30, 45])
        if start > now:
            lead_days = min(lead_days, max(1, (start - now).days + rng.randint(0, 10)))
        booked_at = start - timedelta(days=lead_days)
        confirmed = rng.random() < 0.55
        age = (start.date() - patient.dob).days // 365
        if start < now:
            if rng.random() < 0.07:
                status = AppointmentStatus.CANCELLED
            else:
                p = _no_show_probability(lead_days, prior_no_shows.get(pid, 0), start.weekday(),
                                         start.hour, confirmed, age)
                status = AppointmentStatus.NO_SHOW if rng.random() < p else AppointmentStatus.COMPLETED
                if status == AppointmentStatus.NO_SHOW:
                    prior_no_shows[pid] = prior_no_shows.get(pid, 0) + 1
        else:
            status = AppointmentStatus.CONFIRMED if confirmed else AppointmentStatus.BOOKED
        aid = s.next_id("AP")
        s.appointments[aid] = Appointment(
            id=aid, patient_id=pid, referrer_id=rng.choice(referrer_ids), site_id=scanner.site_id,
            scanner_id=scanner.id, exam_code=exam.code, start=start,
            end=start + timedelta(minutes=scanner.slot_minutes), status=status,
            urgency=rng.choices(["P1", "P2", "P3", "P4"], weights=[0.05, 0.2, 0.45, 0.3])[0],
            booked_at=booked_at, reminder_confirmed=confirmed,
            cancel_reason="Patient request" if status == AppointmentStatus.CANCELLED else None,
        )

    # Waitlist
    for _ in range(60):
        exam = rng.choice(EXAMS)
        sites_with = [x.id for x in s.sites.values() if exam.modality in x.modalities]
        urgency = rng.choices(["P1", "P2", "P3", "P4"], weights=[0.08, 0.27, 0.4, 0.25])[0]
        if exam.modality == Modality.CT:
            urgency = rng.choice(["P3", "P4"])  # keeps the demo storyline ranking stable
        wid = s.next_id("WL")
        s.waitlist[wid] = WaitlistEntry(
            id=wid, patient_id=rng.choice(patient_ids), referrer_id=rng.choice(referrer_ids),
            exam_code=exam.code, urgency=urgency,
            acceptable_site_ids=rng.sample(sites_with, k=min(len(sites_with), rng.randint(1, 2))),
            earliest_date=today + timedelta(days=rng.choice([0, 0, 0, 1, 3, 7])),
            added_at=now - timedelta(days=rng.randint(1, 40), hours=rng.randint(0, 12)),
        )

    # Phase 4 (system 16): a few referrers lose volume; before studies copy referrer ids.
    from app.modules.referrals.service import seed_declines

    seed_declines(s, now, (settings.seed if seed is None else seed) + 16)  # own stream: main rng untouched
    _add_staff(s)
    _add_demo_storyline(s, rng, now)
    _add_reading_worklist(s, rng, now)
    _add_clinical_records(s, rng, now)
    _add_requisitions(s, rng, now)
    _add_radiology_ops(s, rng, now)
    _add_business_compliance(s, rng, now)
    return s


def _add_staff(s: Store) -> None:
    users = [
        StaffUser(id="U-FD", name="Alex Morgan", role=Role.FRONT_DESK, site_ids=["LKS"]),
        StaffUser(id="U-TECH", name="Sam Rivera", role=Role.TECHNOLOGIST, site_ids=["LKS"]),
        StaffUser(id="U-RAD", name="Dr. Priya Raman", role=Role.RADIOLOGIST, site_ids=[],
                  reading_modalities=[Modality.XR, Modality.CT, Modality.US, Modality.MRI]),
        StaffUser(id="U-OPS", name="Jordan Lee", role=Role.OPERATIONS_MANAGER, site_ids=[]),
        StaffUser(id="U-MD", name="Dr. Daniel Okafor", role=Role.MEDICAL_DIRECTOR, site_ids=[]),
        StaffUser(id="U-ADMIN", name="Casey Brooks", role=Role.ADMIN, site_ids=[]),
        StaffUser(id="U-REF", name="Dr. Helen Park", role=Role.REFERRER, site_ids=[], referrer_id="R-DEMO"),
    ]
    # The rest of the reading group: not on the demo login page, but they read
    # studies, get reassigned work (system 11) and review each other (system 13).
    users += [
        StaffUser(id="U-RAD2", name="Dr. Marcus Webb", role=Role.RADIOLOGIST, site_ids=[], demo_login=False,
                  reading_modalities=[Modality.MRI, Modality.CT]),
        StaffUser(id="U-RAD3", name="Dr. Aisha Nwosu", role=Role.RADIOLOGIST, site_ids=[], demo_login=False,
                  reading_modalities=[Modality.CT, Modality.US, Modality.XR]),
        StaffUser(id="U-RAD4", name="Dr. Tomas Ferreira", role=Role.RADIOLOGIST, site_ids=[], demo_login=False,
                  reading_modalities=[Modality.MRI, Modality.XR]),
        StaffUser(id="U-RAD5", name="Dr. Grace Liu", role=Role.RADIOLOGIST, site_ids=[], demo_login=False,
                  reading_modalities=[Modality.US, Modality.XR, Modality.CT]),
    ]
    for user in users:
        s.staff[user.id] = user


def _free_slot(s: Store, scanner_id: str, start: datetime, minutes: int) -> None:
    """Remove any active booking overlapping the given window."""
    end = start + timedelta(minutes=minutes)
    for appt in list(s.appointments.values()):
        if appt.scanner_id == scanner_id and appt.start < end and appt.end > start:
            del s.appointments[appt.id]


def _add_demo_storyline(s: Store, rng: random.Random, now: datetime) -> None:
    today = now.date()
    s.referrers["R-DEMO"] = Referrer(
        id="R-DEMO", name="Dr. Helen Park", specialty="Family Medicine", clinic="Park Family Practice",
        phone="+1-416-555-0142", fax="+1-416-555-0143", is_key=True,
    )
    # The storyline patient: preferred language Chinese.
    s.patients["PT-DEMO1"] = Patient(
        id="PT-DEMO1", given_name="Mei", family_name="Chen", dob=date(1961, 9, 3), sex="F",
        phone="+1-416-555-0168", email="mei.chen@example.com", address="88 Example St, Demo City, ON",
        health_card="4827103956", health_card_version="MC", preferred_language="zh",
    )
    # The caller who will cancel.
    s.patients["PT-DEMO2"] = Patient(
        id="PT-DEMO2", given_name="Robert", family_name="Taylor", dob=date(1968, 4, 12), sex="M",
        phone="+1-416-555-0177", email="robert.taylor@example.com", address="15 Sample Ave, Demo City, ON",
        health_card="7310594826", health_card_version="RT", preferred_language="en",
    )
    # Robert's CT tomorrow at 10:00 at Lakeshore.
    tomorrow = today + timedelta(days=1)
    while tomorrow.weekday() not in s.sites["LKS"].open_days:
        tomorrow += timedelta(days=1)
    start = datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=10)
    _free_slot(s, "LKS-CT1", start, 30)
    s.appointments["AP-DEMO2"] = Appointment(
        id="AP-DEMO2", patient_id="PT-DEMO2", referrer_id=rng.choice(list(s.referrers)), site_id="LKS",
        scanner_id="LKS-CT1", exam_code="CT_CHEST_C", start=start, end=start + timedelta(minutes=30),
        status=AppointmentStatus.CONFIRMED, urgency="P3", booked_at=now - timedelta(days=12),
        reminder_confirmed=True,
    )
    # Mei waits for a contrast CT ordered after her chest X-ray.
    s.waitlist["WL-DEMO1"] = WaitlistEntry(
        id="WL-DEMO1", patient_id="PT-DEMO1", referrer_id="R-DEMO", exam_code="CT_CHEST_C", urgency="P2",
        acceptable_site_ids=["LKS", "NGT"], earliest_date=today, added_at=now - timedelta(days=6),
        notes="Follow-up of possible right upper lobe nodule on chest X-ray.",
    )
    # A competing P2 request that cannot use Lakeshore, and one that is not ready yet.
    s.waitlist["WL-DEMO2"] = WaitlistEntry(
        id="WL-DEMO2", patient_id="PT-00011", referrer_id="R-0003", exam_code="CT_CHEST_C", urgency="P2",
        acceptable_site_ids=["EVW"], earliest_date=today, added_at=now - timedelta(days=9),
    )
    s.waitlist["WL-DEMO3"] = WaitlistEntry(
        id="WL-DEMO3", patient_id="PT-00012", referrer_id="R-0004", exam_code="CT_CHEST_C", urgency="P2",
        acceptable_site_ids=["LKS"], earliest_date=today + timedelta(days=5), added_at=now - timedelta(days=4),
    )
    s.waitlist["WL-DEMO4"] = WaitlistEntry(
        id="WL-DEMO4", patient_id="PT-00013", referrer_id="R-0005", exam_code="CT_CHEST_C", urgency="P3",
        acceptable_site_ids=["LKS", "MTN"], earliest_date=today, added_at=now - timedelta(days=31),
    )
    # Mei's completed chest X-ray from yesterday, waiting for a report.
    s.images["IMG-DEMO1"] = (chest_phantom("nodule", seed=1), "image/png")
    performed = now - timedelta(days=1, hours=2)
    s.studies["ST-DEMO1"] = ImagingStudy(
        id="ST-DEMO1", appointment_id=None, patient_id="PT-DEMO1", referrer_id="R-DEMO", exam_code="XR_CHEST",
        performed_at=performed, image_key="IMG-DEMO1", study_uid="2.25.100000000000000000000000000000000001",
        indication="Persistent cough for 6 weeks, former smoker",
    )


def _add_reading_worklist(s: Store, rng: random.Random, now: datetime) -> None:
    variants = ["normal", "effusion", "normal", "cardiomegaly", "normal"]
    patient_ids = [p for p in s.patients if not p.startswith("PT-DEMO")]
    for i, variant in enumerate(variants):
        key = f"IMG-{i + 1:03d}"
        s.images[key] = (chest_phantom(variant, seed=10 + i), "image/png")
        sid = s.next_id("ST")
        s.studies[sid] = ImagingStudy(
            id=sid, appointment_id=None, patient_id=rng.choice(patient_ids),
            referrer_id=rng.choice(list(s.referrers)), exam_code="XR_CHEST",
            performed_at=now - timedelta(hours=rng.randint(2, 40)), image_key=key,
            study_uid=f"2.25.{rng.getrandbits(100)}",
            indication=rng.choice(["Cough and fever", "Shortness of breath", "Pre-operative assessment", "Chest pain"]),
        )
    s.modules["phantom_variants"] = {f"IMG-{i + 1:03d}": v for i, v in enumerate(variants)} | {"IMG-DEMO1": "nodule"}


def _add_clinical_records(s: Store, rng: random.Random, now: datetime) -> None:
    """eGFR results and allergies used by the contrast checker."""
    for pid, patient in s.patients.items():
        age = (now.date() - patient.dob).days // 365
        if age >= 45 and rng.random() < 0.55:
            lid = s.next_id("LAB")
            s.labs[lid] = LabResult(id=lid, patient_id=pid, code="egfr", unit="mL/min/1.73m2",
                                    value=round(min(120, max(15, rng.gauss(78, 20))), 0),
                                    taken_at=now - timedelta(days=rng.randint(3, 220)))
        roll = rng.random()
        if roll < 0.03:
            severity = rng.choice(["mild", "moderate", "severe"])
            aid = s.next_id("ALG")
            s.allergies[aid] = Allergy(id=aid, patient_id=pid, substance="Iodinated contrast", contrast=True,
                                       reaction={"mild": "nausea", "moderate": "hives", "severe": "anaphylaxis"}[severity],
                                       severity=severity)
        elif roll < 0.11:
            aid = s.next_id("ALG")
            s.allergies[aid] = Allergy(id=aid, patient_id=pid, substance="Penicillin", reaction="rash",
                                       severity="mild", contrast=False)
    # Storyline patient: recent normal kidney function.
    for lid in [lid for lid, lab in s.labs.items() if lab.patient_id == "PT-DEMO1"]:
        del s.labs[lid]
    s.labs["LAB-DEMO1"] = LabResult(id="LAB-DEMO1", patient_id="PT-DEMO1", code="egfr", value=72, unit="mL/min/1.73m2",
                                    taken_at=now - timedelta(days=20))


def _form(patient, referrer, exam, info, history, allergies, prior, priority) -> str:
    lines = [
        "REQUISITION FOR DIAGNOSTIC IMAGING",
        f"Patient: {patient.full_name}    DOB: {patient.dob:%Y-%m-%d}    Health card: {patient.health_card} {patient.health_card_version}",
        f"Phone: {patient.phone}",
        f"Exam requested: {exam}",
        f"Clinical information: {info}",
        f"Relevant history: {history}",
        f"Allergies: {allergies}",
        f"Previous imaging: {prior}",
    ]
    if priority:
        lines.append(f"Priority: {priority}")
    lines.append(f"Referring physician: {referrer.name}, {referrer.clinic}. Fax {referrer.fax}")
    return "\n".join(lines)


# How long seeded requisitions have been waiting, by tier (some end up past target).
MAX_AGE_DAYS = {"P1": 2.5, "P2": 9, "P3": 34, "P4": 65}


def _add_requisitions(s: Store, rng: random.Random, now: datetime) -> None:
    from app.modules.priors.service import archive_index
    from app.modules.requisitions.generator import generate
    from app.modules.requisitions.service import seed_processed

    archive = archive_index(s)
    patient_ids = [p for p in s.patients if not p.startswith("PT-DEMO")]
    referrer_ids = [r for r in s.referrers if r != "R-DEMO"]
    reviewer = s.staff["U-RAD"].name
    for i in range(32):
        patient = s.patients[rng.choice(patient_ids)]
        referrer = s.referrers[rng.choice(referrer_ids)]
        text, labels = generate(rng, patient, referrer)
        rid = s.next_id("REQ")
        req = Requisition(id=rid, patient_id=patient.id, referrer_id=referrer.id,
                          received_at=now - timedelta(days=rng.uniform(0.2, MAX_AGE_DAYS[labels.priority])),
                          channel=rng.choice(["fax", "fax", "portal"]),
                          text=text)
        s.requisitions[rid] = req
        seed_processed(s, req, labels.to_dict(), reviewed=i < 24, reviewer=reviewer)
        if labels.prior_facility and rng.random() < 0.8:
            archive.setdefault(patient.id, []).append(
                {"facility": labels.prior_facility, "description": f"{labels.modality} {labels.exam_code}", "date": "2025-03-14"})

    # Storyline: Dr. Park's contrast CT for Mei Chen, already approved and on the waitlist.
    mei, park = s.patients["PT-DEMO1"], s.referrers["R-DEMO"]
    text = _form(mei, park, "CT chest with contrast",
                 "New 1.5 cm right upper lobe nodule on chest X-ray. Former smoker, 20 pack-years.",
                 "Type 2 diabetes on metformin", "NKDA", "CT chest at Northview General Hospital, March 2025",
                 "URGENT - please expedite")
    req = Requisition(id="REQ-DEMO1", patient_id=mei.id, referrer_id=park.id, received_at=now - timedelta(days=6),
                      channel="portal", text=text)
    s.requisitions[req.id] = req
    seed_processed(s, req, {"priority": "P2", "protocol_id": "CT-CH-CONTRAST"}, reviewed=True, reviewer=reviewer)
    req.status, req.waitlist_id = "waitlisted", "WL-DEMO1"
    wl = s.waitlist["WL-DEMO1"]
    wl.requisition_id, wl.protocol_id, wl.duration_minutes = req.id, "CT-CH-CONTRAST", 20
    archive.setdefault(mei.id, []).append({"facility": "Northview General Hospital", "description": "CT chest without contrast", "date": "2025-03-11"})

    # Three requisitions that arrive unprocessed; the intake worker runs them through the AI pipeline.
    punjabi = next(p for p in patient_ids if s.patients[p].preferred_language == "pa")
    english = next(p for p in patient_ids if s.patients[p].preferred_language == "en" and (now.date() - s.patients[p].dob).days > 365 * 55)
    third = rng.choice(patient_ids)
    ref = s.referrers[referrer_ids[0]]
    incoming = [
        (punjabi, _form(s.patients[punjabi], ref, "MRI brain with and without contrast",
                        "Known breast cancer, new morning headaches, rule out brain metastases.",
                        "Cochlear implant, right ear", "NKDA", "None known", "URGENT - please expedite")),
        (english, _form(s.patients[english], ref, "CT abdomen and pelvis with contrast",
                        "Unintentional weight loss of 8 kg over 3 months with iron deficiency anemia.",
                        "Type 2 diabetes on metformin; Hypertension", "Iodinated contrast (hives)",
                        "Ultrasound abdomen at Lakeview Diagnostics, January 2026", None)),
        (third, f"Dear colleague, please arrange a venous Doppler left leg for {s.patients[third].full_name} "
                f"(DOB {s.patients[third].dob:%Y-%m-%d}, HCN {s.patients[third].health_card}). Swollen painful left calf "
                f"for 2 days, query DVT. This is urgent, please book as soon as possible. Thanks, {ref.name}, {ref.clinic}"),
    ]
    for i, (pid, body) in enumerate(incoming, start=1):
        rid = f"REQ-NEW{i}"
        s.requisitions[rid] = Requisition(id=rid, patient_id=pid, referrer_id=ref.id,
                                          received_at=now - timedelta(minutes=12 - i * 3), channel="fax", text=body)
    # The diabetic patient's eGFR is too old to rely on.
    for lid in [lid for lid, lab in s.labs.items() if lab.patient_id == english]:
        del s.labs[lid]
    for aid in [aid for aid, a in s.allergies.items() if a.patient_id in (english, punjabi, third)]:
        del s.allergies[aid]
    s.labs["LAB-DEMO2"] = LabResult(id="LAB-DEMO2", patient_id=english, code="egfr", value=58, unit="mL/min/1.73m2",
                                    taken_at=now - timedelta(days=150))
    archive.setdefault(english, []).append({"facility": "Lakeview Diagnostics", "description": "Ultrasound abdomen", "date": "2026-01-20"})


def _add_radiology_ops(s: Store, rng: random.Random, now: datetime) -> None:
    """Phase 3: reading history and backlog, critical results, peer review, CT dose."""
    from app.modules.backlog import service as backlog
    from app.modules.critical import service as critical
    from app.modules.dose import service as dose
    from app.modules.peer_review import service as peer_review

    backlog.seed(s, rng, now)
    critical.seed(s, rng, now)
    peer_review.seed(s, rng, now)
    dose.seed(s, rng, now)


def _add_business_compliance(s: Store, rng: random.Random, now: datetime) -> None:
    """Phase 4: inventory, referrals, portal, billing, feedback, access monitoring, inspection."""
    from app.modules.billing import service as billing
    from app.modules.feedback import service as feedback
    from app.modules.inventory import service as inventory
    from app.modules.phipa import service as phipa
    from app.modules.portal import service as portal

    inventory.seed(s, rng, now)
    portal.seed(s, rng, now)
    billing.seed(s, rng, now)
    feedback.seed(s, rng, now)
    phipa.seed(s, rng, now)  # last: audit history covers records created above
