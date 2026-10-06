"""Synthetic requisition generator with ground-truth labels.

Used for demo seed data and for the evaluation sets (with a different seed), so
every generated requisition knows its correct exam, protocol, priority, contrast
need, prior imaging, allergies and implants."""

import random
from dataclasses import asdict, dataclass, field
from datetime import date

from app.core.models import Patient, Referrer
from app.modules.protocols.library import BY_ID


@dataclass
class Case:
    exam_texts: list[str]
    indications: list[str]
    protocol_id: str
    priority: str
    red_flags: list[str] = field(default_factory=list)


CASES = [
    Case(["CT chest with contrast", "CT thorax with IV contrast"],
         ["New 1.5 cm right upper lobe nodule on chest X-ray. Former smoker, 20 pack-years.",
          "Spiculated lung nodule seen on recent chest X-ray, rule out malignancy."],
         "CT-CH-CONTRAST", "P2", ["nodule"]),
    Case(["CT chest low dose", "Low-dose CT chest"],
         ["Known 6 mm left lower lobe nodule, 12-month follow-up.", "Surveillance of known small pulmonary nodule, follow-up as per guidelines."],
         "CT-CH-LDCT", "P4"),
    Case(["CT chest without contrast", "CT chest non-contrast"],
         ["Chronic cough and crackles, query interstitial lung disease.", "Progressive dyspnea, query fibrosis."],
         "CT-CH-ROUTINE", "P3"),
    Case(["CT abdomen and pelvis with contrast", "CT A/P with IV contrast"],
         ["Unintentional weight loss of 8 kg over 3 months with iron deficiency anemia.",
          "Weight loss and change in bowel habit, rule out malignancy."],
         "CT-AP-CONTRAST", "P2", ["weight loss"]),
    Case(["CT KUB", "CT abdomen for renal stone"],
         ["Left flank pain radiating to groin with microscopic hematuria, query kidney stone.",
          "Recurrent renal colic, known kidney stone history."],
         "CT-AP-RENAL", "P3"),
    Case(["CT head", "CT head without contrast"],
         ["Chronic tension-type headaches, normal neurological exam, patient anxious.",
          "Fall two weeks ago with persistent mild headache, on no anticoagulants."],
         "CT-HD-ROUTINE", "P4"),
    Case(["MRI brain with and without contrast", "MRI head with gadolinium"],
         ["Known breast cancer, new morning headaches, rule out brain metastases.",
          "History of melanoma, new focal weakness, query metastasis."],
         "MR-BR-TUMOUR", "P2", ["metastases"]),
    Case(["MRI brain", "MRI head"],
         ["First unprovoked seizure last week, normal CT in emergency.", "New-onset seizure, query epilepsy."],
         "MR-BR-EPILEPSY", "P2", ["seizure"]),
    Case(["MRI brain with contrast", "MRI brain and orbits"],
         ["Optic neuritis last month and intermittent leg numbness, query multiple sclerosis.",
          "Episodes of numbness and blurred vision, query demyelination."],
         "MR-BR-MS", "P3"),
    Case(["MRI brain", "MRI head without contrast"],
         ["Gradual memory decline over 12 months, query dementia.", "Chronic dizziness and vertigo, normal exam."],
         "MR-BR-ROUTINE", "P4"),
    Case(["MRI lumbar spine", "MRI L-spine"],
         ["Chronic low back pain with right sciatica for 6 months, no red flags.", "Lumbar radiculopathy L5, failed physiotherapy."],
         "MR-LS-ROUTINE", "P4"),
    Case(["MRI lumbar spine urgent", "MRI L-spine"],
         ["New urinary retention and saddle numbness, query cauda equina.", "Bilateral leg weakness with saddle anesthesia."],
         "MR-LS-ROUTINE", "P1", ["cauda equina"]),
    Case(["MRI lumbar spine with contrast", "MRI L-spine with gadolinium"],
         ["Recurrent left leg pain after L4-5 discectomy in 2023.", "Previous laminectomy, recurrent symptoms, query recurrent disc."],
         "MR-LS-POSTOP", "P3"),
    Case(["MRI right knee", "MRI left knee"],
         ["Locking and medial joint line pain after twisting injury, query meniscal tear.", "Knee instability after sports injury, query ACL tear."],
         "MR-KN-ROUTINE", "P3"),
    Case(["Ultrasound abdomen", "US abdomen"],
         ["Right upper quadrant pain after fatty meals, query gallstones.", "Elevated liver enzymes, query fatty liver."],
         "US-ABD-COMPLETE", "P3"),
    Case(["Ultrasound pelvis", "Pelvic ultrasound"],
         ["Postmenopausal bleeding for 3 weeks.", "Postmenopausal bleeding, endometrial thickness assessment."],
         "US-PEL-TV", "P2", ["postmenopausal bleeding"]),
    Case(["Ultrasound thyroid", "US neck/thyroid"],
         ["Palpable 2 cm thyroid nodule.", "Enlarging goitre, query nodules."],
         "US-THY", "P3"),
    Case(["Venous Doppler left leg", "Ultrasound venous Doppler lower limb"],
         ["Swollen painful left calf for 2 days, query DVT.", "Unilateral leg swelling after long flight, rule out deep vein thrombosis."],
         "US-VEN-LE", "P1", ["dvt"]),
    Case(["Chest X-ray", "CXR PA and lateral"],
         ["Cough for 3 weeks with fever.", "Shortness of breath and productive cough, query pneumonia."],
         "XR-CH-PALAT", "P3"),
    Case(["X-ray knee", "Knee X-ray"],
         ["Chronic knee pain, query osteoarthritis.", "Bilateral knee pain worse on stairs."],
         "XR-KN-3V", "P4"),
    Case(["X-ray lumbar spine", "Lumbar spine X-ray"],
         ["Mechanical back pain after lifting a box.", "Low back pain for 4 weeks after lifting."],
         "XR-LS-2V", "P4"),
]

HISTORY = [
    (None, False), ("Hypertension", False), ("Asthma", False),
    ("Type 2 diabetes on metformin", True), ("Chronic kidney disease stage 3", True),
]
ALLERGIES = [
    (None, False, None), (None, False, None), ("Penicillin (rash)", False, None),
    ("Iodinated contrast (hives)", True, "moderate"), ("Iodinated contrast (anaphylaxis)", True, "severe"),
    ("Gadolinium (nausea)", True, "mild"),
]
IMPLANTS = [
    (None, None), (None, None), (None, None),
    ("Pacemaker inserted 2019", "cardiac pacemaker"), ("Cochlear implant, right ear", "cochlear implant"),
    ("Cerebral aneurysm clip (2010)", "aneurysm clip"), ("Right total hip replacement", "joint replacement"),
]
FACILITIES = ["Northview General Hospital", "Riverside Health Centre", "Lakeview Diagnostics"]
PRIOR_EXAMS = {"MRI": "MRI", "CT": "CT", "US": "ultrasound", "XR": "X-ray"}
BODY_PART = {
    "MR_BRAIN": "brain", "MR_LSPINE": "lumbar spine", "MR_KNEE": "knee", "CT_CHEST": "chest", "CT_CHEST_C": "chest",
    "CT_ABD_PEL": "abdomen", "CT_HEAD": "head", "US_ABD": "abdomen", "US_PELVIS": "pelvis", "US_THYROID": "thyroid",
    "US_VENOUS": "leg veins", "XR_CHEST": "chest", "XR_KNEE": "knee", "XR_LSPINE": "lumbar spine",
}


@dataclass
class Labels:
    exam_code: str
    protocol_id: str
    priority: str
    red_flags: list[str]
    contrast: bool
    modality: str
    renal_risk: bool
    contrast_allergy: str | None  # severity, if any
    implant: str | None
    prior_facility: str | None
    stated_urgency: str  # urgent, routine, unspecified
    style: str  # form, letter

    def to_dict(self) -> dict:
        return asdict(self)


def _fmt_dob(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def generate(rng: random.Random, patient: Patient, referrer: Referrer, case: Case | None = None,
             style: str | None = None) -> tuple[str, Labels]:
    case = case or rng.choice(CASES)
    protocol = BY_ID[case.protocol_id]
    exam_text = rng.choice(case.exam_texts)
    indication = rng.choice(case.indications)
    history, renal = rng.choice(HISTORY)
    allergy, contrast_allergy, severity = rng.choice(ALLERGIES)
    implant_text, implant = rng.choice(IMPLANTS) if protocol.modality == "MRI" or rng.random() < 0.3 else (None, None)
    prior = None
    if rng.random() < 0.35:
        facility = rng.choice(FACILITIES)
        prior = (f"{PRIOR_EXAMS[protocol.modality]} {BODY_PART[protocol.exam_code]} at {facility}, "
                 f"{rng.choice(['March 2025', 'June 2024', 'January 2026', '2023'])}")
        prior_facility = facility
    else:
        prior_facility = None
    if case.priority in ("P1", "P2"):
        urgency = rng.choices(["urgent", "unspecified"], weights=[0.7, 0.3])[0]
    else:
        urgency = rng.choices(["routine", "unspecified"], weights=[0.6, 0.4])[0]
    history_items = [h for h in (history, implant_text) if h]
    style = style or rng.choices(["form", "letter"], weights=[0.7, 0.3])[0]
    pronoun = "She" if patient.sex == "F" else "He"

    if style == "form":
        lines = [
            "REQUISITION FOR DIAGNOSTIC IMAGING",
            f"Patient: {patient.full_name}    DOB: {_fmt_dob(patient.dob)}    Health card: {patient.health_card} {patient.health_card_version}",
            f"Phone: {patient.phone}",
            f"Exam requested: {exam_text}",
            f"Clinical information: {indication}",
            f"Relevant history: {'; '.join(history_items) if history_items else 'None'}",
            f"Allergies: {allergy or 'NKDA'}",
            f"Previous imaging: {prior or 'None known'}",
        ]
        if urgency == "urgent":
            lines.append("Priority: URGENT - please expedite")
        elif urgency == "routine":
            lines.append("Priority: Routine")
        lines.append(f"Referring physician: {referrer.name}, {referrer.clinic}. Fax {referrer.fax}")
        text = "\n".join(lines)
    else:
        parts = [f"Dear colleague, please arrange a {exam_text} for {patient.full_name} (DOB {_fmt_dob(patient.dob)}, "
                 f"HCN {patient.health_card}). {indication}"]
        if history_items:
            parts.append(f"PMH: {', '.join(history_items)}.")
        if allergy:
            parts.append(f"{pronoun} is allergic to {allergy.split(' (')[0].lower()} ({allergy.split('(')[1]}.")
        if prior:
            parts.append(f"{pronoun} had a {prior}.")
        if urgency == "urgent":
            parts.append("This is urgent, please book as soon as possible.")
        elif urgency == "routine":
            parts.append("Routine booking is fine.")
        parts.append(f"Thanks, {referrer.name}, {referrer.clinic}")
        text = " ".join(parts)

    labels = Labels(
        exam_code=protocol.exam_code, protocol_id=protocol.id, priority=case.priority, red_flags=case.red_flags,
        contrast=protocol.contrast, modality=str(protocol.modality), renal_risk=renal,
        contrast_allergy=severity if contrast_allergy else None, implant=implant, prior_facility=prior_facility,
        stated_urgency=urgency, style=style,
    )
    return text, labels
