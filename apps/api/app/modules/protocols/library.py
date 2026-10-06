"""Synthetic protocol library and keyword retrieval.

Each protocol says which indications it fits, whether it uses contrast and how
long it takes. Retrieval narrows the library to a handful of candidates; the
LLM then chooses among them (retrieval-augmented, so it can only pick real
protocols)."""

import re

from pydantic import BaseModel

from app.core.models import Modality


class Protocol(BaseModel):
    id: str
    name: str
    modality: Modality
    exam_code: str  # booking exam this protocol is scheduled as
    contrast: bool
    minutes: int
    indications: list[str]


PROTOCOLS = [
    Protocol(id="MR-BR-ROUTINE", name="MRI brain, routine (no contrast)", modality=Modality.MRI, exam_code="MR_BRAIN", contrast=False, minutes=30,
             indications=["headache", "dizziness", "vertigo", "memory", "cognitive decline", "dementia"]),
    Protocol(id="MR-BR-TUMOUR", name="MRI brain with and without contrast, tumour", modality=Modality.MRI, exam_code="MR_BRAIN", contrast=True, minutes=45,
             indications=["tumour", "tumor", "metastasis", "metastases", "mass", "cancer", "lesion"]),
    Protocol(id="MR-BR-EPILEPSY", name="MRI brain, epilepsy protocol", modality=Modality.MRI, exam_code="MR_BRAIN", contrast=False, minutes=45,
             indications=["seizure", "epilepsy", "convulsion"]),
    Protocol(id="MR-BR-MS", name="MRI brain, demyelination (MS) with contrast", modality=Modality.MRI, exam_code="MR_BRAIN", contrast=True, minutes=45,
             indications=["multiple sclerosis", "demyelination", "optic neuritis", "numbness", "ms"]),
    Protocol(id="MR-LS-ROUTINE", name="MRI lumbar spine, routine", modality=Modality.MRI, exam_code="MR_LSPINE", contrast=False, minutes=30,
             indications=["back pain", "sciatica", "radiculopathy", "stenosis", "cauda equina", "saddle"]),
    Protocol(id="MR-LS-POSTOP", name="MRI lumbar spine, post-operative with contrast", modality=Modality.MRI, exam_code="MR_LSPINE", contrast=True, minutes=45,
             indications=["post-operative", "discectomy", "laminectomy", "previous surgery", "recurrent"]),
    Protocol(id="MR-KN-ROUTINE", name="MRI knee, routine", modality=Modality.MRI, exam_code="MR_KNEE", contrast=False, minutes=30,
             indications=["meniscus", "meniscal", "ligament", "acl", "locking", "twisting"]),
    Protocol(id="CT-CH-ROUTINE", name="CT chest without contrast", modality=Modality.CT, exam_code="CT_CHEST", contrast=False, minutes=15,
             indications=["interstitial", "emphysema", "chronic cough", "fibrosis"]),
    Protocol(id="CT-CH-CONTRAST", name="CT chest with contrast", modality=Modality.CT, exam_code="CT_CHEST_C", contrast=True, minutes=20,
             indications=["new nodule", "mass", "staging", "lymphadenopathy", "malignancy", "hemoptysis", "nodule"]),
    Protocol(id="CT-CH-LDCT", name="Low-dose CT chest, nodule follow-up", modality=Modality.CT, exam_code="CT_CHEST", contrast=False, minutes=15,
             indications=["follow-up", "known nodule", "screening", "surveillance"]),
    Protocol(id="CT-AP-CONTRAST", name="CT abdomen and pelvis with contrast", modality=Modality.CT, exam_code="CT_ABD_PEL", contrast=True, minutes=20,
             indications=["weight loss", "abdominal pain", "anemia", "anaemia", "diverticulitis", "abdominal mass"]),
    Protocol(id="CT-AP-RENAL", name="CT renal stone (KUB, no contrast)", modality=Modality.CT, exam_code="CT_ABD_PEL", contrast=False, minutes=15,
             indications=["renal colic", "kidney stone", "flank pain", "hematuria", "haematuria"]),
    Protocol(id="CT-HD-ROUTINE", name="CT head without contrast", modality=Modality.CT, exam_code="CT_HEAD", contrast=False, minutes=15,
             indications=["head injury", "fall", "headache"]),
    Protocol(id="US-ABD-COMPLETE", name="Ultrasound abdomen, complete", modality=Modality.US, exam_code="US_ABD", contrast=False, minutes=30,
             indications=["gallstones", "gallbladder", "ruq", "right upper quadrant", "liver enzymes", "fatty liver"]),
    Protocol(id="US-PEL-TV", name="Ultrasound pelvis, transabdominal and transvaginal", modality=Modality.US, exam_code="US_PELVIS", contrast=False, minutes=30,
             indications=["pelvic pain", "bleeding", "postmenopausal", "fibroids", "ovarian"]),
    Protocol(id="US-THY", name="Ultrasound thyroid", modality=Modality.US, exam_code="US_THYROID", contrast=False, minutes=30,
             indications=["thyroid", "goitre", "goiter", "neck lump"]),
    Protocol(id="US-VEN-LE", name="Ultrasound lower limb venous Doppler", modality=Modality.US, exam_code="US_VENOUS", contrast=False, minutes=30,
             indications=["dvt", "deep vein", "calf", "leg swelling"]),
    Protocol(id="XR-CH-PALAT", name="Chest X-ray, PA and lateral", modality=Modality.XR, exam_code="XR_CHEST", contrast=False, minutes=10,
             indications=["cough", "pneumonia", "fever", "shortness of breath"]),
    Protocol(id="XR-KN-3V", name="Knee X-ray, 3 views", modality=Modality.XR, exam_code="XR_KNEE", contrast=False, minutes=10,
             indications=["osteoarthritis", "knee pain", "knee"]),
    Protocol(id="XR-LS-2V", name="Lumbar spine X-ray, 2 views", modality=Modality.XR, exam_code="XR_LSPINE", contrast=False, minutes=10,
             indications=["mechanical back pain", "lifting", "back pain"]),
]

BY_ID = {p.id: p for p in PROTOCOLS}

MODALITY_WORDS = {
    Modality.MRI: r"\bmri\b|\bmr\b|magnetic",
    Modality.CT: r"\bct\b|computed tomography|cat scan",
    Modality.US: r"ultrasound|\bus\b|doppler|sonogra",
    Modality.XR: r"x-ray|xray|radiograph|\bxr\b",
}


def detect_modality(requested_exam: str) -> Modality | None:
    text = requested_exam.lower()
    for modality, pattern in MODALITY_WORDS.items():
        if re.search(pattern, text):
            return modality
    return None


def retrieve(requested_exam: str, clinical_text: str, k: int = 5) -> list[tuple[Protocol, float]]:
    """Rank protocols by modality and indication keyword overlap."""
    modality = detect_modality(requested_exam)
    exam = requested_exam.lower()
    text = f"{requested_exam} {clinical_text}".lower()
    scored = []
    for p in PROTOCOLS:
        if modality and p.modality != modality:
            continue
        score = sum(1.0 for kw in p.indications if re.search(rf"\b{re.escape(kw)}\b", text))
        body = p.name.lower().split(",")[0].replace("mri ", "").replace("ct ", "").replace("ultrasound ", "")
        if any(word in exam for word in body.split() if len(word) > 3):
            score += 0.5  # body part matches the requested exam
        wants_contrast = bool(re.search(r"with contrast|contrast[- ]enhanced|\bc\+", exam))
        no_contrast = bool(re.search(r"without contrast|non[- ]contrast|no contrast", exam))
        if wants_contrast and p.contrast or no_contrast and not p.contrast:
            score += 0.75
        scored.append((p, score))
    scored.sort(key=lambda item: -item[1])
    return scored[:k]
