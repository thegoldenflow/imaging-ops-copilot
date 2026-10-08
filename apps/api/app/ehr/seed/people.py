"""Organization, Location tree, practitioners and patients with their baseline records."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.ehr import codes as C
from app.ehr.reference import OR_ROOMS, SURGEONS, UNITS
from app.ehr.seed.builder import Builder, ext
from app.fhir.dt import fhir_date, ref

N_PATIENTS = 1000
HOSPITAL_NAME = "Lakeside General Hospital (demo)"

CITIES = [("Toronto", "M"), ("Toronto", "M"), ("Toronto", "M"), ("Mississauga", "L"), ("Markham", "L"),
          ("Richmond Hill", "L"), ("Brampton", "L"), ("Vaughan", "L"), ("Oakville", "L"), ("Scarborough", "M")]
STREETS = ["Example St", "Sample Ave", "Placeholder Rd", "Fictional Blvd", "Demo Cres", "Mock Lane",
           "Synthetic Dr", "Testing Way", "Imaginary Ct", "Notional Pkwy"]
POSTAL_LETTERS = "ABCEGHJKLMNPRSTVXY"
AREA_CODES = ["416", "647", "905", "437"]

EN_GIVEN = {"female": ["Margaret", "Linda", "Susan", "Karen", "Helen", "Dorothy", "Emily", "Sarah", "Olivia",
                       "Grace", "Jennifer", "Patricia", "Ruth", "Joan", "Claire", "Megan", "Natalie", "Rachel"],
            "male": ["Robert", "William", "James", "Thomas", "Richard", "George", "Michael", "Daniel", "Andrew",
                     "Peter", "Brian", "Kevin", "Paul", "Edward", "Frank", "Henry", "Mark", "Steven"]}
EN_FAMILY = ["Anderson", "Bennett", "Campbell", "Douglas", "Ellis", "Fraser", "Gordon", "Harper", "Irving",
             "Jensen", "Keller", "Lawson", "MacLeod", "Nolan", "Osborne", "Prescott", "Quinlan", "Rowe",
             "Sutherland", "Tremblay", "Underwood", "Vaughan", "Whitfield", "Young", "Abbott", "Brennan"]
ZH_CN_FAMILY = [("Wang", "王"), ("Li", "李"), ("Zhang", "张"), ("Liu", "刘"), ("Chen", "陈"), ("Yang", "杨"),
                ("Huang", "黄"), ("Zhao", "赵"), ("Wu", "吴"), ("Zhou", "周"), ("Xu", "徐"), ("Sun", "孙"),
                ("Ma", "马"), ("Zhu", "朱"), ("Hu", "胡"), ("Guo", "郭"), ("He", "何"), ("Lin", "林")]
ZH_CN_GIVEN = {"female": [("Fang", "芳"), ("Min", "敏"), ("Jing", "静"), ("Yan", "燕"), ("Ling", "玲"),
                          ("Hui", "慧"), ("Xiuying", "秀英"), ("Guiying", "桂英"), ("Shufen", "淑芬"), ("Meilan", "美兰")],
               "male": [("Wei", "伟"), ("Lei", "磊"), ("Jun", "军"), ("Qiang", "强"), ("Yong", "勇"),
                        ("Jianguo", "建国"), ("Haoran", "浩然"), ("Zhiqiang", "志强"), ("Ming", "明"), ("Bo", "波")]}
ZH_TW_FAMILY = [("Chen", "陳"), ("Lin", "林"), ("Huang", "黃"), ("Chang", "張"), ("Lee", "李"), ("Wang", "王"),
                ("Wu", "吳"), ("Liu", "劉"), ("Tsai", "蔡"), ("Yang", "楊")]
ZH_TW_GIVEN = {"female": [("Mei-Ling", "美玲"), ("Shu-Fen", "淑芬"), ("Ya-Ting", "雅婷"), ("Chia-Hui", "佳慧"),
                          ("Hsiu-Ying", "秀英"), ("Li-Hua", "麗華")],
               "male": [("Chih-Hao", "志豪"), ("Chun-Hung", "俊宏"), ("Wen-Chieh", "文傑"), ("Kuo-Hua", "國華"),
                        ("Chien-Hung", "建宏"), ("Ming-Te", "明德")]}

STAFF_GIVEN = ["Alicia", "Bernard", "Chloe", "Dmitri", "Esther", "Farid", "Gemma", "Hiro", "Isla", "Jonah",
               "Keira", "Luis", "Maya", "Nikhil", "Orla", "Pavel", "Quinn", "Rosa", "Stefan", "Tara", "Umar",
               "Vera", "Wes", "Yara", "Zane", "Amara", "Bjorn", "Celine", "Dario", "Eun-ji", "Felix", "Gita"]
STAFF_FAMILY = ["Abernathy", "Bastien", "Corrigan", "Delacroix", "Eriksen", "Fairweather", "Galloway", "Haddad",
                "Ivanova", "Jovanovic", "Kincaid", "Lindqvist", "Moreau", "Nakamura", "Okonkwo", "Pemberton",
                "Quintero", "Rasmussen", "Salazar", "Thornton", "Uchida", "Valdez", "Whitlock", "Xavier",
                "Yilmaz", "Zielinski"]

LANGUAGE_DISPLAY = {"en": "English", "zh-CN": "Chinese (China)", "zh-TW": "Chinese (Taiwan)"}

# Chronic condition prevalence: code -> (base probability, per-decade increase above 40, sex restriction)
CHRONIC = {
    "59621000": (0.08, 0.10, None), "44054006": (0.04, 0.04, None), "49436004": (0.0, 0.035, None),
    "42343007": (0.0, 0.025, None), "13645005": (0.01, 0.025, None), "433144002": (0.0, 0.035, None),
    "431857002": (0.0, 0.01, None), "195967001": (0.06, 0.0, None), "55822004": (0.05, 0.07, None),
    "35489007": (0.10, 0.0, None), "64859006": (0.0, 0.05, "female"), "396275006": (0.02, 0.07, None),
    "69896004": (0.02, 0.003, None), "26929004": (0.0, 0.0, None), "40930008": (0.04, 0.012, None),
    "90560007": (0.01, 0.01, "male"), "396331005": (0.01, 0.0, None),
}


@dataclass
class PatientInfo:
    id: str
    mrn: str
    age: int
    sex: str
    lang: str
    birth: date
    conditions: list[str] = field(default_factory=list)
    home_meds: list[str] = field(default_factory=list)
    allergy_classes: list[str] = field(default_factory=list)
    egfr: float = 90.0
    weight: float = 75.0
    prior_admissions: list[datetime] = field(default_factory=list)
    has_phone: bool = True
    clinic_encounter: str = ""  # the outpatient visit the baseline records are attached to

    @property
    def burden(self) -> float:
        return len(self.conditions) + max(0, self.age - 60) / 10


@dataclass
class Practitioner:
    id: str
    name: str
    role: str  # doctor, nurse, pharmacist, clerk, ops
    unit: str | None
    specialty: str


def build_locations(b: Builder) -> None:
    b.add({"resourceType": "Organization", "id": "org-demo-hospital", "active": True, "name": HOSPITAL_NAME,
           "type": [C.concept("http://terminology.hl7.org/CodeSystem/organization-type", "prov", "Healthcare Provider")]})
    managing = ref("Organization", "org-demo-hospital")
    for unit in UNITS:
        b.add({"resourceType": "Location", "id": unit.id, "status": "active", "name": unit.name, "mode": "instance",
               "physicalType": C.concept(C.LOCATION_TYPE, "wa", "Ward"), "managingOrganization": managing,
               "extension": [ext("bed-capacity", valueInteger=unit.beds), ext("unit-kind", valueCode=unit.kind)]})
        rooms = math.ceil(unit.beds / unit.beds_per_room)
        for r in range(1, rooms + 1):
            room_id = f"{unit.id}-{r:02d}"
            b.add({"resourceType": "Location", "id": room_id, "status": "active", "name": f"{unit.name} room {r}",
                   "mode": "instance", "physicalType": C.concept(C.LOCATION_TYPE, "ro", "Room"),
                   "partOf": ref("Location", unit.id), "managingOrganization": managing})
            for k in range(unit.beds_per_room):
                if (r - 1) * unit.beds_per_room + k >= unit.beds:
                    break
                bed_id = f"{room_id}-{'AB'[k]}"
                b.add({"resourceType": "Location", "id": bed_id, "status": "active",
                       "name": f"{unit.name} bed {r}{'AB'[k] if unit.beds_per_room > 1 else ''}", "mode": "instance",
                       "physicalType": C.concept(C.LOCATION_TYPE, "bd", "Bed"),
                       "operationalStatus": C.coding(C.BED_STATUS, "U", "Unoccupied"),
                       "partOf": ref("Location", room_id), "managingOrganization": managing})
    b.add({"resourceType": "Location", "id": "OR", "status": "active", "name": "Operating rooms", "mode": "instance",
           "physicalType": C.concept(C.LOCATION_TYPE, "wa", "Ward"), "managingOrganization": managing,
           "extension": [ext("unit-kind", valueCode="or")]})
    for room in OR_ROOMS:
        b.add({"resourceType": "Location", "id": room, "status": "active", "name": f"Operating room {room[-1]}",
               "mode": "instance", "physicalType": C.concept(C.LOCATION_TYPE, "ro", "Room"),
               "partOf": ref("Location", "OR"), "managingOrganization": managing})


def bed_ids(unit_id: str) -> list[str]:
    unit = next(u for u in UNITS if u.id == unit_id)
    out = []
    for i in range(unit.beds):
        r, k = divmod(i, unit.beds_per_room)
        out.append(f"{unit.id}-{r + 1:02d}-{'AB'[k]}")
    return out


def build_practitioners(b: Builder) -> list[Practitioner]:
    rng = b.rng("practitioners")
    staff: list[Practitioner] = []
    used: set[str] = set()

    def name() -> str:
        while True:
            n = f"{rng.choice(STAFF_GIVEN)} {rng.choice(STAFF_FAMILY)}"
            if n not in used:
                used.add(n)
                return n

    plan = [("ED", "doctor", "emergency-medicine", 8), ("MEDA", "doctor", "internal-medicine", 3),
            ("MEDB", "doctor", "internal-medicine", 3), ("SURG", "doctor", "general-surgery", 1),
            ("ORTH", "doctor", "orthopedics", 1), ("ICU", "doctor", "critical-care", 3), (None, "doctor", "anesthesiology", 3)]
    for unit in ["ED", "MEDA", "MEDB", "SURG", "ORTH", "ICU"]:
        plan.append((unit, "nurse", "nursing", 4))
    plan += [(None, "pharmacist", "pharmacy", 3), (None, "clerk", "registration", 2), (None, "ops", "patient-flow", 2)]
    counters: dict[str, int] = {}
    for unit, role, specialty, n in plan:
        for _ in range(n):
            counters[role] = counters.get(role, 0) + 1
            pid = f"prac-{role[:4]}-{counters[role]:02d}" if role != "doctor" else f"prac-doc-{counters[role]:02d}"
            full = ("Dr. " if role == "doctor" else "") + name()
            staff.append(Practitioner(pid, full, role, unit, specialty))
    for s in SURGEONS:
        unit = {"ORTH": "ORTH", "SURG": "SURG", "CARD": "ICU"}[s.specialty]
        staff.append(Practitioner(s.id, s.name, "doctor", unit,
                                  {"ORTH": "orthopedics", "SURG": "general-surgery", "CARD": "cardiac-surgery"}[s.specialty]))
    role_codes = {"doctor": ("doctor", C.PRACTITIONER_ROLE), "nurse": ("nurse", C.PRACTITIONER_ROLE),
                  "pharmacist": ("pharmacist", C.PRACTITIONER_ROLE), "clerk": ("clerk", C.LOCAL),
                  "ops": ("ops", C.LOCAL)}
    for p in staff:
        given, family = p.name.removeprefix("Dr. ").split(" ", 1)
        b.add({"resourceType": "Practitioner", "id": p.id, "active": True,
               "name": [{"family": family, "given": [given], "text": p.name,
                         **({"prefix": ["Dr."]} if p.name.startswith("Dr.") else {})}],
               "identifier": [{"system": "urn:demo-hospital:staff", "value": p.id}]})
        code, system = role_codes[p.role]
        role = {"resourceType": "PractitionerRole", "id": f"role-{p.id.removeprefix('prac-')}", "active": True,
                "practitioner": ref("Practitioner", p.id, p.name),
                "organization": ref("Organization", "org-demo-hospital"),
                "code": [C.concept(system, code, code)], "specialty": [C.concept(C.LOCAL, p.specialty, p.specialty)]}
        if p.unit:
            role["location"] = [ref("Location", p.unit)]
        b.add(role)
    return staff


def _age(rng) -> int:
    bucket = rng.choices([(18, 39), (40, 59), (60, 74), (75, 84), (85, 96)], weights=[0.17, 0.25, 0.28, 0.2, 0.1])[0]
    return rng.randint(*bucket)


def _postal(rng, first: str) -> str:
    L = POSTAL_LETTERS
    return f"{first}{rng.randint(1, 9)}{rng.choice(L)} {rng.randint(0, 9)}{rng.choice(L)}{rng.randint(0, 9)}"


def _phone(rng) -> str:
    return f"+1-{rng.choice(AREA_CODES)}-555-01{rng.randint(0, 99):02d}"  # 555-01xx: reserved for fiction


def build_patients(b: Builder) -> list[PatientInfo]:
    rng = b.rng("patients")
    today = b.now.date()
    out: list[PatientInfo] = []
    for i in range(1, N_PATIENTS + 1):
        pid = f"pat-{i:04d}"
        sex = rng.choice(["female", "male"])
        age = _age(rng)
        birth = today - timedelta(days=age * 365 + rng.randint(0, 364))
        lang = rng.choices(["en", "zh-CN", "zh-TW"], weights=[0.70, 0.20, 0.10])[0]
        mrn = f"4{i:07d}"
        if lang == "en":
            given, family = rng.choice(EN_GIVEN[sex]), rng.choice(EN_FAMILY)
            names = [{"use": "official", "family": family, "given": [given], "text": f"{given} {family}"}]
        else:
            fam_list, given_list = (ZH_CN_FAMILY, ZH_CN_GIVEN) if lang == "zh-CN" else (ZH_TW_FAMILY, ZH_TW_GIVEN)
            (family, family_zh), (given, given_zh) = rng.choice(fam_list), rng.choice(given_list[sex])
            names = [{"use": "official", "family": family, "given": [given], "text": f"{given} {family}"},
                     {"use": "usual", "family": family_zh, "given": [given_zh], "text": family_zh + given_zh,
                      "extension": [ext("name-language", valueCode=lang)]}]
        city, letter = rng.choice(CITIES)
        has_phone = rng.random() > 0.04
        hcn = "".join(str(rng.randint(0, 9)) for _ in range(10))
        resource = {
            "resourceType": "Patient", "id": pid, "active": True,
            "identifier": [
                {"use": "usual", "type": {"text": "Medical record number"}, "system": C.MRN_SYSTEM, "value": mrn},
                {"use": "official", "type": {"text": "Ontario health card (synthetic)"}, "system": C.HCN_SYSTEM,
                 "value": hcn, "extension": [ext("synthetic", valueBoolean=True),
                                             ext("hcn-version", valueString=rng.choice(["AB", "CD", "EF", "GH", "KL"]))]},
            ],
            "name": names, "gender": sex, "birthDate": fhir_date(birth),
            "address": [{"use": "home", "line": [f"{rng.randint(10, 999)} {rng.choice(STREETS)}"], "city": city,
                         "state": "ON", "postalCode": _postal(rng, letter), "country": "CA"}],
            "communication": [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": lang,
                                                        "display": LANGUAGE_DISPLAY[lang]}]}, "preferred": True}],
            "managingOrganization": ref("Organization", "org-demo-hospital"),
        }
        if has_phone:
            resource["telecom"] = [{"system": "phone", "value": _phone(rng), "use": "home"}]
        if age >= 70 and rng.random() < 0.5:
            rel_given = rng.choice(EN_GIVEN["female"] + EN_GIVEN["male"]) if lang == "en" else rng.choice(
                [g for g, _ in (ZH_CN_GIVEN if lang == "zh-CN" else ZH_TW_GIVEN)["female"]])
            resource["contact"] = [{"relationship": [{"text": rng.choice(["daughter", "son", "spouse"])}],
                                    "name": {"text": f"{rel_given} {names[0]['family']}"},
                                    "telecom": [{"system": "phone", "value": _phone(rng)}]}]
        b.add(resource)
        out.append(PatientInfo(pid, mrn, age, sex, lang, birth, has_phone=has_phone))
    return out


def build_baseline(b: Builder, patients: list[PatientInfo], window_start: datetime) -> None:
    """Problem lists, home medications, allergies, consents, a recent outpatient visit with labs,
    and earlier admissions (before the simulated window) for the 'admissions in 12 months' feature."""
    rng = b.rng("baseline")
    now = b.now
    for p in patients:
        # A recent outpatient visit: the encounter the problem list, home medications and allergies are recorded at
        visit = now - timedelta(days=rng.randint(20, 300), hours=rng.randint(0, 6))
        visit = visit.replace(hour=rng.randint(8, 16), minute=rng.choice([0, 15, 30, 45]), second=0, microsecond=0)
        enc = b.encounter(p.id, "AMB", visit, end=visit + timedelta(minutes=30), service="outpatient-clinic")["id"]
        p.clinic_encounter = enc
        decades = max(0, (p.age - 40) / 10)
        for code, (base, per_decade, sex) in CHRONIC.items():
            if sex and sex != p.sex:
                continue
            prob = base + per_decade * decades
            if code == "26929004":
                prob = 0.12 if p.age >= 85 else (0.03 if p.age >= 75 else 0.0)
            if rng.random() < prob:
                p.conditions.append(code)
        if "431857002" in p.conditions and "433144002" in p.conditions:
            p.conditions.remove("433144002")
        for code in p.conditions:
            onset = p.birth + timedelta(days=int(365 * rng.uniform(max(18, p.age - 25), max(19, p.age - 1))))
            b.condition(p.id, code, min(onset, now.date() - timedelta(days=30)), enc=enc)
        # Home medications (provincial drug history stand-in)
        meds: list[str] = []
        for code in p.conditions:
            options = C.CHRONIC_MEDS.get(code, [])
            if code == "49436004":
                meds.append(rng.choice(["11289", "11289", "1364430"]))
                meds.append("6918")
            elif options:
                meds += rng.sample(options, k=min(len(options), 1 + (rng.random() < 0.35)))
        if "42343007" in p.conditions and "11289" not in meds and rng.random() < 0.3:
            meds.append("11289")
        if p.age > 65 and rng.random() < 0.15:
            meds.append("40790")
        for rxcui in dict.fromkeys(meds):
            start = now.date() - timedelta(days=rng.randint(60, 2000))
            b.medication_statement(p.id, rxcui, start, enc=enc)
            p.home_meds.append(rxcui)
        # Allergies
        if rng.random() < 0.22:
            allergen = rng.choices(C.ALLERGENS, weights=[0.32, 0.18, 0.08, 0.1, 0.12, 0.06, 0.06, 0.08])[0]
            severe = rng.random() < 0.25
            b.allergy(p.id, allergen, now.date() - timedelta(days=rng.randint(200, 4000)),
                      criticality="high" if severe else "low",
                      reaction=rng.choice(["Hives", "Rash", "Anaphylaxis" if severe else "Itching", "Swelling"]),
                      severity="severe" if severe else "mild", enc=enc)
            if allergen[3]:
                p.allergy_classes.append(allergen[3])
        # Consents (missing for some patients, so modules show their fallback)
        consent_time = now - timedelta(days=rng.randint(30, 900))
        for category, permit_rate, missing_rate in (("ai_processing", 0.94, 0.03), ("followup_call", 0.88, 0.05),
                                                    ("sms", 0.75, 0.10)):
            if rng.random() < missing_rate:
                continue
            b.consent(p.id, category, consent_time, permit=rng.random() < permit_rate)
        # Kidney function and weight
        if "431857002" in p.conditions:
            p.egfr = rng.uniform(16, 29)
        elif "433144002" in p.conditions:
            p.egfr = rng.uniform(31, 58)
        else:
            p.egfr = max(45, min(118, 125 - 0.8 * p.age + rng.gauss(0, 10)))
        p.weight = round(max(42, rng.gauss(82 if p.sex == "male" else 68, 14)), 1)
        # The recent outpatient visit (labs below)
        b.observation(p.id, enc, (C.WEIGHT[0], C.WEIGHT[1]), visit, value=p.weight, unit="kg")
        b.observation(p.id, enc, ("33914-3", C.LABS["33914-3"][0]), visit, value=round(p.egfr), unit="mL/min/{1.73_m2}",
                      category="laboratory")
        creat = round(max(0.5, 88 / max(p.egfr, 8) * (1.0 if p.sex == "male" else 0.85)), 2)
        b.observation(p.id, enc, ("2160-0", C.LABS["2160-0"][0]), visit, value=creat, unit="mg/dL", category="laboratory")
        if "11289" in p.home_meds:
            b.observation(p.id, enc, ("6301-6", C.LABS["6301-6"][0]), visit, value=round(rng.uniform(1.9, 3.1), 1),
                          unit="{INR}", category="laboratory")
        # Earlier admissions (12 months before the simulated window)
        rate = min(0.35, 0.03 + 0.05 * p.burden)
        n_prior = sum(rng.random() < rate for _ in range(3))
        for _ in range(n_prior):
            start = window_start - timedelta(days=rng.randint(5, 360), hours=rng.randint(0, 23))
            los = timedelta(days=rng.randint(2, 9), hours=rng.randint(0, 12))
            reason = rng.choice(list(C.CONDITIONS))
            b.encounter(p.id, "IMP", start, end=start + los, service="inpatient",
                        reason=C.snomed(reason), admit_source="emd", disposition="home")
            p.prior_admissions.append(start)
