"""Hospital master data shared by the generator, the simulator and the flow models.

All of it is fictional: unit sizes follow the spec (6.2 step 4), the ED roster
and expected lengths of stay are plausible round numbers for a demo, not
benchmarks.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Unit:
    id: str  # also the Location id; rooms are "<id>-NN", beds "<id>-NN-X"
    name: str
    beds: int
    beds_per_room: int
    kind: str  # ed, ward, icu


UNITS = [
    Unit("ED", "Emergency Department", 30, 1, "ed"),
    Unit("MEDA", "Medicine A", 32, 2, "ward"),
    Unit("MEDB", "Medicine B", 32, 2, "ward"),
    Unit("SURG", "Surgery", 32, 2, "ward"),
    Unit("ORTH", "Orthopedics", 24, 2, "ward"),
    Unit("ICU", "Intensive Care Unit", 12, 1, "icu"),
]
UNIT_BY_ID = {u.id: u for u in UNITS}
INPATIENT_UNITS = [u.id for u in UNITS if u.kind != "ed"]
OR_ROOMS = ["OR-01", "OR-02", "OR-03", "OR-04"]  # OR-04 takes emergency add-on cases after hours
EMERGENCY_OR = "OR-04"
OR_DAY = (8, 16)  # elective block hours
TURNOVER_MIN = 30

# Overflow order when a unit is full.
OVERFLOW = {"MEDA": ["MEDB"], "MEDB": ["MEDA"], "SURG": ["MEDB", "MEDA"], "ORTH": ["SURG"], "ICU": []}


def ed_physicians_on_duty(at: datetime) -> int:
    """The ED physician roster (demo): fewer overnight and at weekends."""
    hour, weekend = at.hour, at.weekday() >= 5
    if hour < 7:
        n = 2
    elif hour < 11:
        n = 4
    elif hour < 21:
        n = 5
    else:
        n = 3
    return max(2, n - (1 if weekend else 0))


# Admission diagnosis per ED complaint: (SNOMED code, weight, unit, ICU share, surgery code or None).
# ICU shares: myocardial infarction 0.5, sepsis 0.6, stroke 0.2, pneumonia and heart failure 0.15, COPD 0.12, plus
# elective CABG; the 12-bed ICU then holds about 8 patients at 07:00 on a weekday (docs/data-model.md).
ADMIT_DX: dict[str, list[tuple[str, float, str, float, str | None]]] = {
    "chest pain": [("22298006", 0.35, "MEDA", 0.5, None), ("29857009", 0.65, "MEDB", 0.0, None)],
    "shortness of breath": [("233604007", 0.4, "MEDA", 0.15, None), ("42343007", 0.3, "MEDB", 0.15, None),
                            ("195951007", 0.3, "MEDA", 0.12, None)],
    "abdominal pain": [("74400008", 0.3, "SURG", 0.0, "80146002"), ("65275009", 0.3, "SURG", 0.0, "45595009"),
                       ("21522001", 0.4, "MEDB", 0.0, None)],
    "fall": [("5913000", 0.55, "ORTH", 0.0, "52734007"), ("125605004", 0.25, "ORTH", 0.0, None),
             ("1912002", 0.2, "MEDB", 0.0, None)],
    "fever": [("91302008", 0.3, "MEDA", 0.6, None), ("68566005", 0.4, "MEDB", 0.0, None),
              ("233604007", 0.3, "MEDA", 0.15, None)],
    "headache": [("230690007", 0.5, "MEDA", 0.2, None), ("25064002", 0.5, "MEDB", 0.0, None)],
    "back pain": [("161891005", 1.0, "MEDB", 0.0, None)],
    "injury": [("125605004", 1.0, "ORTH", 0.0, None)],
    "confusion": [("68566005", 0.4, "MEDB", 0.0, None), ("230690007", 0.3, "MEDA", 0.2, None),
                  ("91302008", 0.3, "MEDA", 0.6, None)],
    "rash": [("396230008", 1.0, "MEDB", 0.0, None)],
}

# Expected length of stay in days by admission diagnosis (used as a feature and for discharge planning).
EXPECTED_LOS: dict[str, float] = {
    "22298006": 4, "29857009": 2, "233604007": 5, "42343007": 6, "195951007": 5, "74400008": 2,
    "65275009": 3, "21522001": 2, "5913000": 7, "125605004": 4, "1912002": 4, "91302008": 8,
    "68566005": 4, "230690007": 7, "25064002": 2, "161891005": 3, "396230008": 4,
    # elective surgical admissions use the procedure's typical days (codes.OR_PROCEDURES)
}

# Medications started in hospital for an admission diagnosis (RxNorm), beyond continued home medications.
INPATIENT_MEDS: dict[str, list[str]] = {
    "22298006": ["1191", "32968", "83367", "5224"],
    "29857009": ["1191"],
    "233604007": ["2193", "21212"],
    "42343007": ["4603"],
    "195951007": ["8640", "435"],
    "74400008": ["2193", "6922", "3423"],
    "65275009": ["8339", "3423"],
    "21522001": ["26225", "161"],
    "5913000": ["67108", "161", "3423"],
    "125605004": ["161", "7052"],
    "1912002": ["161"],
    "91302008": ["8339", "11124"],
    "68566005": ["2193"],
    "230690007": ["1191", "83367"],
    "25064002": ["161"],
    "161891005": ["161", "35827"],
    "396230008": ["2180"],
}
POSTOP_MEDS = ["161", "3423", "67108", "26225"]

# Lab and imaging orders by admission diagnosis: (kind, code system, code, display)
ORDER_SETS: dict[str, list[tuple[str, str]]] = {
    "default": [("lab", "6690-2"), ("lab", "718-7"), ("lab", "2160-0"), ("lab", "2823-3"), ("lab", "2951-2")],
    "22298006": [("lab", "10839-9"), ("imaging", "XR_CHEST")],
    "233604007": [("lab", "1988-5"), ("imaging", "XR_CHEST")],
    "42343007": [("lab", "33762-6"), ("imaging", "XR_CHEST")],
    "91302008": [("lab", "2524-7"), ("lab", "1988-5")],
    "5913000": [("lab", "6301-6"), ("imaging", "XR_HIP"), ("imaging", "CT_HIP")],
    "230690007": [("imaging", "CT_HEAD")],
    "74400008": [("imaging", "CT_ABD_PEL")],
    "65275009": [("imaging", "US_ABD")],
}
IMAGING_ORDERS = {
    "XR_CHEST": "X-ray Chest", "XR_HIP": "X-ray Hip", "CT_HIP": "CT Hip", "CT_HEAD": "CT Head",
    "CT_ABD_PEL": "CT Abdomen/Pelvis", "US_ABD": "Ultrasound Abdomen",
}
CONSULTS = {"physio": "Physiotherapy consult", "ot": "Occupational therapy consult", "sw": "Social work consult",
            "pharm": "Pharmacy consult"}


@dataclass(frozen=True)
class Surgeon:
    id: str  # Practitioner id
    name: str
    specialty: str  # ORTH, SURG, CARD
    speed: float  # multiplies the procedure's base duration (the effect the duration model learns)
    procedures: tuple[str, ...]


SURGEONS = [
    Surgeon("prac-surg-01", "Dr. Elena Marsh", "ORTH", 0.85, ("52734007", "609588000")),
    Surgeon("prac-surg-02", "Dr. Victor Osei", "ORTH", 1.22, ("52734007", "609588000", "55705006")),
    Surgeon("prac-surg-03", "Dr. Hannah Kowalski", "ORTH", 1.0, ("609588000", "55705006", "52734007")),
    Surgeon("prac-surg-04", "Dr. Samuel Achebe", "SURG", 0.9, ("45595009", "44558001", "80146002")),
    Surgeon("prac-surg-05", "Dr. Ingrid Novak", "SURG", 1.25, ("23968004", "45595009", "236886002")),
    Surgeon("prac-surg-06", "Dr. Rafael Duarte", "SURG", 1.05, ("44558001", "80146002", "236886002", "23968004")),
    Surgeon("prac-surg-07", "Dr. Margaret Ho", "CARD", 1.1, ("232717009",)),
]
SURGEON_BY_ID = {s.id: s for s in SURGEONS}
# Weekly elective block: weekday -> OR room -> surgeon id
OR_BLOCKS: dict[int, dict[str, str]] = {
    0: {"OR-01": "prac-surg-01", "OR-02": "prac-surg-04", "OR-03": "prac-surg-07", "OR-04": "prac-surg-06"},
    1: {"OR-01": "prac-surg-02", "OR-02": "prac-surg-05", "OR-03": "prac-surg-03"},
    2: {"OR-01": "prac-surg-01", "OR-02": "prac-surg-06", "OR-03": "prac-surg-07", "OR-04": "prac-surg-05"},
    3: {"OR-01": "prac-surg-03", "OR-02": "prac-surg-04", "OR-03": "prac-surg-02"},
    4: {"OR-01": "prac-surg-01", "OR-02": "prac-surg-05", "OR-03": "prac-surg-07", "OR-04": "prac-surg-03"},
}
ASA_FACTOR = {1: 0.9, 2: 1.0, 3: 1.12, 4: 1.3}
DAY_SURGERY = {"45595009", "44558001"}  # same-day discharge when uncomplicated
