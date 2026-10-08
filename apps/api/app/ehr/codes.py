"""Code systems and the coded vocabulary the hospital generator and modules share.

SNOMED CT for conditions and procedures, LOINC for observations, RxNorm
(ingredient level) for medications, as Synthea writes them. Local concepts that
have no standard code in this demo (CTAS, NEWS2, bed requests, pre-op checks)
use the `urn:demo-hospital:*` systems.
"""

SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
BED_STATUS = "http://terminology.hl7.org/CodeSystem/v2-0116"
LOCATION_TYPE = "http://terminology.hl7.org/CodeSystem/location-physical-type"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
CONDITION_CATEGORY = "http://terminology.hl7.org/CodeSystem/condition-category"
CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
ADMIT_SOURCE = "http://terminology.hl7.org/CodeSystem/admit-source"
DISCHARGE_DISPOSITION = "http://terminology.hl7.org/CodeSystem/discharge-disposition"
PRACTITIONER_ROLE = "http://terminology.hl7.org/CodeSystem/practitioner-role"
CONSENT_SCOPE = "http://terminology.hl7.org/CodeSystem/consentscope"

MRN_SYSTEM = "urn:demo-hospital:mrn"
HCN_SYSTEM = "urn:demo-hospital:hcn"
LOCAL = "urn:demo-hospital:code"  # local concepts without a standard code here
CTAS_SYSTEM = "urn:demo-hospital:ctas"
TASK_CODE = "urn:demo-hospital:task"
FLAG_CODE = "urn:demo-hospital:flag"
DOC_TYPE_LOCAL = "urn:demo-hospital:doc-type"
CONSENT_CATEGORY = "urn:demo-hospital:consent"
EXT = "urn:demo-hospital:ext:"  # prefix for extension URLs

# ---------- Conditions (SNOMED CT) ----------

# code: (display, chronic?)
CONDITIONS: dict[str, tuple[str, bool]] = {
    "59621000": ("Essential hypertension", True),
    "44054006": ("Diabetes mellitus type 2", True),
    "49436004": ("Atrial fibrillation", True),
    "42343007": ("Congestive heart failure", True),
    "13645005": ("Chronic obstructive lung disease", True),
    "433144002": ("Chronic kidney disease stage 3", True),
    "431857002": ("Chronic kidney disease stage 4", True),
    "195967001": ("Asthma", True),
    "55822004": ("Hyperlipidemia", True),
    "35489007": ("Depressive disorder", True),
    "64859006": ("Osteoporosis", True),
    "396275006": ("Osteoarthritis", True),
    "69896004": ("Rheumatoid arthritis", True),
    "26929004": ("Alzheimer's disease", True),
    "40930008": ("Hypothyroidism", True),
    "90560007": ("Gout", True),
    "233604007": ("Pneumonia", False),
    "22298006": ("Myocardial infarction", False),
    "230690007": ("Cerebrovascular accident", False),
    "5913000": ("Fracture of neck of femur", False),
    "68566005": ("Urinary tract infectious disease", False),
    "91302008": ("Sepsis", False),
    "195951007": ("Acute exacerbation of chronic obstructive airways disease", False),
    "74400008": ("Appendicitis", False),
    "65275009": ("Acute cholecystitis", False),
    "128053003": ("Deep venous thrombosis", False),
    "59282003": ("Pulmonary embolism", False),
    "29857009": ("Chest pain", False),
    "21522001": ("Abdominal pain", False),
    "267036007": ("Dyspnea", False),
    "161891005": ("Backache", False),
    "25064002": ("Headache", False),
    "125605004": ("Fracture of bone", False),
    "10509002": ("Acute bronchitis", False),
    "444814009": ("Viral sinusitis", False),
    "62315008": ("Diarrhea", False),
    "386661006": ("Fever", False),
    "1912002": ("Fall", False),
    "271737000": ("Anemia", False),
    "396230008": ("Cellulitis", False),
    "44558001": ("Inguinal hernia", False),  # used as the indication for hernia repair
    "396331005": ("Coeliac disease", True),
}

# ---------- Procedures (SNOMED CT) ----------

# code: (display, base minutes, service unit, typical inpatient days after)
OR_PROCEDURES: dict[str, tuple[str, int, str, int]] = {
    "52734007": ("Total replacement of hip", 105, "ORTH", 4),
    "609588000": ("Total knee replacement", 110, "ORTH", 3),
    "55705006": ("Spinal fusion", 190, "ORTH", 5),
    "80146002": ("Excision of appendix", 60, "SURG", 2),
    "45595009": ("Laparoscopic cholecystectomy", 75, "SURG", 1),
    "44558001": ("Repair of inguinal hernia", 55, "SURG", 1),
    "23968004": ("Colectomy", 180, "SURG", 6),
    "232717009": ("Coronary artery bypass grafting", 240, "ICU", 7),
    "236886002": ("Hysterectomy", 120, "SURG", 2),
}
OTHER_PROCEDURES: dict[str, str] = {
    "73761001": "Colonoscopy",
    "18286008": "Catheter ablation of tissue of heart",
    "415070008": "Percutaneous coronary intervention",
    "312681000": "Bone density scan",
    "385763009": "Hospice care",
}

# ---------- Observations (LOINC) ----------

# code: (display, unit)
VITALS: dict[str, tuple[str, str]] = {
    "8867-4": ("Heart rate", "/min"),
    "8480-6": ("Systolic blood pressure", "mm[Hg]"),
    "8462-4": ("Diastolic blood pressure", "mm[Hg]"),
    "9279-1": ("Respiratory rate", "/min"),
    "8310-5": ("Body temperature", "Cel"),
    "59408-5": ("Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
    "72514-3": ("Pain severity - 0-10 verbal numeric rating [Score] - Reported", "{score}"),
    "9269-2": ("Glasgow coma score total", "{score}"),
}
VITAL_PANEL = ("85353-1", "Vital signs, weight, height, head circumference, oxygen saturation and BMI panel")
WEIGHT = ("29463-7", "Body weight", "kg")
HEIGHT = ("8302-2", "Body height", "cm")

LABS: dict[str, tuple[str, str, tuple[float, float]]] = {
    # code: (display, unit, normal range)
    "33914-3": ("Glomerular filtration rate/1.73 sq M.predicted", "mL/min/{1.73_m2}", (60, 120)),
    "2160-0": ("Creatinine [Mass/volume] in Serum or Plasma", "mg/dL", (0.6, 1.2)),
    "6301-6": ("INR in Platelet poor plasma by Coagulation assay", "{INR}", (0.9, 1.2)),
    "2823-3": ("Potassium [Moles/volume] in Serum or Plasma", "mmol/L", (3.5, 5.1)),
    "2951-2": ("Sodium [Moles/volume] in Serum or Plasma", "mmol/L", (135, 145)),
    "718-7": ("Hemoglobin [Mass/volume] in Blood", "g/dL", (12, 16)),
    "6690-2": ("Leukocytes [#/volume] in Blood by Automated count", "10*3/uL", (4, 11)),
    "777-3": ("Platelets [#/volume] in Blood by Automated count", "10*3/uL", (150, 400)),
    "2345-7": ("Glucose [Mass/volume] in Serum or Plasma", "mg/dL", (70, 140)),
    "1988-5": ("C reactive protein [Mass/volume] in Serum or Plasma", "mg/L", (0, 10)),
    "10839-9": ("Troponin I.cardiac [Mass/volume] in Serum or Plasma", "ng/mL", (0, 0.04)),
    "33762-6": ("Natriuretic peptide.B prohormone N-Terminal [Mass/volume] in Serum or Plasma", "pg/mL", (0, 300)),
    "2524-7": ("Lactate [Moles/volume] in Serum or Plasma", "mmol/L", (0.5, 2.0)),
}
BLOOD_GROUP = ("883-9", "ABO group [Type] in Blood")
CTAS = ("11283-9", "Acuity assessment at First encounter")  # value: CTAS level 1-5
NEWS2 = ("news2", "NEWS2 early warning score")  # local, system LOCAL

# ---------- Medications (RxNorm ingredients) ----------

# rxcui: (name, therapeutic class, typical dose, unit, frequency per day, route)
MEDICATIONS: dict[str, tuple[str, str, float, str, int, str]] = {
    "11289": ("warfarin", "anticoagulant", 5, "mg", 1, "oral"),
    "1364430": ("apixaban", "anticoagulant", 5, "mg", 2, "oral"),
    "1114195": ("rivaroxaban", "anticoagulant", 20, "mg", 1, "oral"),
    "1037042": ("dabigatran", "anticoagulant", 150, "mg", 2, "oral"),
    "67108": ("enoxaparin", "anticoagulant", 40, "mg", 1, "subcutaneous"),
    "5224": ("heparin", "anticoagulant", 5000, "[iU]", 2, "subcutaneous"),
    "6809": ("metformin", "biguanide", 500, "mg", 2, "oral"),
    "593411": ("sitagliptin", "dpp4 inhibitor", 100, "mg", 1, "oral"),
    "4815": ("glyburide", "sulfonylurea", 5, "mg", 1, "oral"),
    "274783": ("insulin glargine", "insulin", 20, "[iU]", 1, "subcutaneous"),
    "29046": ("lisinopril", "ace inhibitor", 10, "mg", 1, "oral"),
    "17767": ("amlodipine", "calcium channel blocker", 5, "mg", 1, "oral"),
    "5487": ("hydrochlorothiazide", "thiazide diuretic", 25, "mg", 1, "oral"),
    "6918": ("metoprolol", "beta blocker", 50, "mg", 2, "oral"),
    "4603": ("furosemide", "loop diuretic", 40, "mg", 1, "oral"),
    "9997": ("spironolactone", "potassium-sparing diuretic", 25, "mg", 1, "oral"),
    "83367": ("atorvastatin", "statin", 20, "mg", 1, "oral"),
    "36567": ("simvastatin", "statin", 20, "mg", 1, "oral"),
    "3407": ("digoxin", "cardiac glycoside", 0.125, "mg", 1, "oral"),
    "703": ("amiodarone", "antiarrhythmic", 200, "mg", 1, "oral"),
    "1191": ("aspirin", "antiplatelet", 81, "mg", 1, "oral"),
    "32968": ("clopidogrel", "antiplatelet", 75, "mg", 1, "oral"),
    "5640": ("ibuprofen", "nsaid", 400, "mg", 3, "oral"),
    "7258": ("naproxen", "nsaid", 500, "mg", 2, "oral"),
    "35827": ("ketorolac", "nsaid", 15, "mg", 4, "intravenous"),
    "140587": ("celecoxib", "nsaid", 200, "mg", 1, "oral"),
    "161": ("acetaminophen", "analgesic", 1000, "mg", 4, "oral"),
    "7052": ("morphine", "opioid", 5, "mg", 4, "oral"),
    "3423": ("hydromorphone", "opioid", 2, "mg", 4, "oral"),
    "10689": ("tramadol", "opioid", 50, "mg", 3, "oral"),
    "25480": ("gabapentin", "gabapentinoid", 300, "mg", 3, "oral"),
    "187832": ("pregabalin", "gabapentinoid", 75, "mg", 2, "oral"),
    "519": ("allopurinol", "xanthine oxidase inhibitor", 300, "mg", 1, "oral"),
    "2683": ("colchicine", "anti-gout", 0.6, "mg", 2, "oral"),
    "6448": ("lithium", "mood stabilizer", 300, "mg", 2, "oral"),
    "36437": ("sertraline", "ssri", 50, "mg", 1, "oral"),
    "4493": ("fluoxetine", "ssri", 20, "mg", 1, "oral"),
    "2556": ("citalopram", "ssri", 20, "mg", 1, "oral"),
    "51272": ("quetiapine", "antipsychotic", 25, "mg", 1, "oral"),
    "6470": ("lorazepam", "benzodiazepine", 1, "mg", 1, "oral"),
    "10582": ("levothyroxine", "thyroid hormone", 0.1, "mg", 1, "oral"),
    "8640": ("prednisone", "corticosteroid", 40, "mg", 1, "oral"),
    "435": ("salbutamol", "beta agonist", 0.1, "mg", 4, "inhaled"),
    "69120": ("tiotropium", "anticholinergic", 0.018, "mg", 1, "inhaled"),
    "40790": ("pantoprazole", "proton pump inhibitor", 40, "mg", 1, "oral"),
    "7646": ("omeprazole", "proton pump inhibitor", 20, "mg", 1, "oral"),
    "26225": ("ondansetron", "antiemetic", 4, "mg", 3, "oral"),
    "8591": ("potassium chloride", "electrolyte", 20, "meq", 1, "oral"),
    "2193": ("ceftriaxone", "cephalosporin", 1000, "mg", 1, "intravenous"),
    "2180": ("cefazolin", "cephalosporin", 2000, "mg", 3, "intravenous"),
    "723": ("amoxicillin", "penicillin", 500, "mg", 3, "oral"),
    "8339": ("piperacillin", "penicillin", 4000, "mg", 3, "intravenous"),
    "11124": ("vancomycin", "glycopeptide", 1000, "mg", 2, "intravenous"),
    "2551": ("ciprofloxacin", "fluoroquinolone", 500, "mg", 2, "oral"),
    "82122": ("levofloxacin", "fluoroquinolone", 750, "mg", 1, "oral"),
    "21212": ("clarithromycin", "macrolide", 500, "mg", 2, "oral"),
    "10180": ("sulfamethoxazole", "sulfonamide", 800, "mg", 2, "oral"),
    "7454": ("nitrofurantoin", "nitrofuran", 100, "mg", 2, "oral"),
    "4450": ("fluconazole", "azole antifungal", 150, "mg", 1, "oral"),
    "6922": ("metronidazole", "nitroimidazole", 500, "mg", 2, "oral"),
    "6851": ("methotrexate", "antimetabolite", 15, "mg", 1, "oral"),  # weekly in practice; see dosage text
}

# Long-term medications a chronic condition brings with it (home medication lists).
CHRONIC_MEDS: dict[str, list[str]] = {
    "59621000": ["29046", "17767", "5487"],
    "44054006": ["6809", "593411", "274783"],
    "49436004": ["11289", "1364430", "6918"],
    "42343007": ["4603", "6918", "29046", "9997"],
    "13645005": ["69120", "435"],
    "433144002": [],
    "431857002": [],
    "195967001": ["435"],
    "55822004": ["83367", "36567"],
    "35489007": ["36437", "4493", "2556"],
    "64859006": [],
    "396275006": ["161", "5640", "140587"],
    "69896004": ["6851", "8640"],
    "26929004": ["51272"],
    "40930008": ["10582"],
    "90560007": ["519", "2683"],
    "396331005": [],
}

# Substances for AllergyIntolerance: code (SNOMED substance or RxNorm), display, drug class matched by 7.2
ALLERGENS: list[tuple[str, str, str, str]] = [
    # (system, code, display, class it covers)
    (SNOMED, "764146007", "Penicillin", "penicillin"),
    (SNOMED, "387406002", "Sulfonamide", "sulfonamide"),
    (RXNORM, "1191", "aspirin", "nsaid"),
    (RXNORM, "7052", "morphine", "opioid"),
    (SNOMED, "256277009", "Grass pollen", ""),
    (SNOMED, "91935009", "Peanut", ""),
    (SNOMED, "111088007", "Latex", ""),
    (RXNORM, "2193", "ceftriaxone", "cephalosporin"),
]

# ---------- ED ----------

# Chief complaint categories with typical CTAS distribution and admission tendency.
# name: (SNOMED code of the presenting problem, CTAS weights for levels 1..5, admission log-odds offset)
COMPLAINTS: dict[str, tuple[str, list[float], float]] = {
    "chest pain": ("29857009", [0.05, 0.45, 0.35, 0.12, 0.03], 0.6),
    "shortness of breath": ("267036007", [0.06, 0.40, 0.38, 0.13, 0.03], 0.9),
    "abdominal pain": ("21522001", [0.01, 0.15, 0.50, 0.28, 0.06], 0.1),
    "fall": ("1912002", [0.02, 0.18, 0.45, 0.28, 0.07], 0.4),
    "fever": ("386661006", [0.02, 0.15, 0.45, 0.30, 0.08], 0.0),
    "headache": ("25064002", [0.01, 0.10, 0.40, 0.37, 0.12], -0.8),
    "back pain": ("161891005", [0.00, 0.05, 0.30, 0.45, 0.20], -1.4),
    "injury": ("125605004", [0.01, 0.10, 0.35, 0.40, 0.14], -0.6),
    "confusion": ("26929004", [0.05, 0.35, 0.40, 0.17, 0.03], 1.1),
    "rash": ("396230008", [0.00, 0.02, 0.20, 0.48, 0.30], -1.6),
}
ARRIVAL_MODES = [("ambulance", 0.32), ("walk-in", 0.62), ("police", 0.02), ("transfer", 0.04)]

# ---------- Documents and tasks ----------

DOC_TYPES = {
    "discharge_summary": (LOINC, "18842-5", "Discharge summary"),
    "patient_instructions": (LOINC, "69730-0", "Instructions"),
    "medrec": (DOC_TYPE_LOCAL, "medrec", "Medication reconciliation record"),
    "handoff": (DOC_TYPE_LOCAL, "sbar-handoff", "Nursing handoff (SBAR)"),
    "discharge_med_list": (DOC_TYPE_LOCAL, "discharge-med-list", "Discharge medication list"),
    "progress_note": (LOINC, "11506-3", "Progress note"),
}

PREOP_CHECKS = {
    "preop-consent": "Surgical consent signed",
    "preop-npo": "Fasting (NPO) confirmed",
    "preop-labs": "Pre-operative labs resulted",
    "preop-blood-type": "Blood group and screen on file",
}


def coding(system: str, code: str, display: str | None = None) -> dict:
    out = {"system": system, "code": code}
    if display:
        out["display"] = display
    return out


def concept(system: str, code: str, display: str | None = None, text: str | None = None) -> dict:
    out: dict = {"coding": [coding(system, code, display)]}
    if text or display:
        out["text"] = text or display
    return out


def snomed(code: str) -> dict:
    display = CONDITIONS.get(code, (None,))[0] or OR_PROCEDURES.get(code, (None,))[0] or OTHER_PROCEDURES.get(code)
    return concept(SNOMED, code, display)


def rxnorm(rxcui: str) -> dict:
    return concept(RXNORM, rxcui, MEDICATIONS[rxcui][0])


def loinc(code: str) -> dict:
    display = VITALS.get(code, (None,))[0] or LABS.get(code, (None,))[0]
    return concept(LOINC, code, display)
