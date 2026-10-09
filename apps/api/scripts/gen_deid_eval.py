"""Generate the free-text de-identification eval set (spec 6.3): evals/deid/cases.jsonl.

200 synthetic clinical notes (progress notes, nursing notes, discharge notes,
handoffs, call logs; English with Chinese passages) with planted PHI and its
gold spans: the patient's names in several spellings (English, pinyin, Chinese
characters, title + surname, given name alone), relatives and outside clinicians
the record does not know, staff from the roster, dates in mixed formats (ISO,
slashes both ways, dots, month names, Chinese), phone numbers with extensions,
health card numbers with version codes, MRNs, street addresses and postal codes,
organisations, emails. Notes also carry the usual traps for a de-identifier:
eponyms (Parkinson's, Foley, Glasgow), drug names, scores and fractions (pain
5/10, GCS 15/15, BP 132/84), times, unit and bed codes, years alone, surnames
that are also words (Long, Young, Price, Hope), titles without a name (Nurse
Practitioner). About a third of the notes add one harder variant that no
pattern targets on purpose: a relative named before the relation, a nickname,
visitors named without any cue, a lower-case "dr.", a date in words, a year
written '26, a phone number grouped 3-7.

Each case has the `known` identifiers a FHIR Patient and the staff roster would
give the de-identifier, so dictionary hits and pattern hits are both measured
(`known` on each gold span says which). Everything is invented; numbers use the
fictional 555-01xx phone range. Deterministic: same seed, same file.

    uv run python scripts/gen_deid_eval.py [--seed 6300] [--n 200]
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evals" / "deid" / "cases.jsonl"

EN_GIVEN = {"female": ["Margaret", "Linda", "Susan", "Helen", "Dorothy", "Emily", "Sarah", "Olivia", "Grace", "Ruth",
                       "Joan", "Claire", "Megan", "Natalie", "Rachel", "Beatrice", "Iris", "Phyllis"],
            "male": ["Robert", "William", "James", "Thomas", "Richard", "George", "Daniel", "Andrew", "Peter", "Brian",
                     "Kevin", "Edward", "Frank", "Henry", "Steven", "Leonard", "Walter", "Gerald"]}
EN_FAMILY = ["Anderson", "Bennett", "Campbell", "Douglas", "Ellis", "Fraser", "Gordon", "Harper", "Irving", "Jensen",
             "Keller", "Lawson", "MacLeod", "Nolan", "Osborne", "Prescott", "Quinlan", "Sutherland", "Tremblay",
             "Underwood", "Whitfield", "Abbott", "Brennan", "O'Connell", "Fitzpatrick", "Delaney", "Kowalski",
             "Moretti", "Haddad", "Okafor", "Young", "Long", "Price", "Hope"]  # the last four are also words
ZH = [("Wang", "王"), ("Li", "李"), ("Zhang", "张"), ("Liu", "刘"), ("Chen", "陈"), ("Yang", "杨"), ("Huang", "黄"),
      ("Zhao", "赵"), ("Wu", "吴"), ("Zhou", "周"), ("Xu", "徐"), ("Sun", "孙"), ("Ma", "马"), ("Zhu", "朱"),
      ("Lin", "林"), ("He", "何"), ("Guo", "郭"), ("Hu", "胡")]
ZH_GIVEN = {"female": [("Fang", "芳"), ("Min", "敏"), ("Jing", "静"), ("Yan", "燕"), ("Ling", "玲"), ("Hui", "慧"),
                       ("Xiuying", "秀英"), ("Meilan", "美兰"), ("Shufen", "淑芬")],
            "male": [("Wei", "伟"), ("Lei", "磊"), ("Jun", "军"), ("Qiang", "强"), ("Yong", "勇"), ("Jianguo", "建国"),
                     ("Haoran", "浩然"), ("Zhiqiang", "志强"), ("Ming", "明")]}
STAFF_GIVEN = ["Alicia", "Bernard", "Chloe", "Dmitri", "Esther", "Farid", "Gemma", "Hiro", "Isla", "Jonah", "Keira",
               "Luis", "Maya", "Nikhil", "Orla", "Pavel", "Rosa", "Stefan", "Tara", "Umar", "Vera", "Yara", "Eun-ji"]
STAFF_FAMILY = ["Abernathy", "Bastien", "Corrigan", "Delacroix", "Eriksen", "Fairweather", "Galloway", "Kincaid",
                "Lindqvist", "Moreau", "Nakamura", "Pemberton", "Quintero", "Rasmussen", "Salazar", "Thornton",
                "Valdez", "Whitlock", "Yilmaz", "Zielinski"]
OUTSIDE_DOCTORS = ["Okonjo", "Lindgren", "Castellanos", "Yamada", "Petrov", "Fitzgerald", "Ramaswamy", "Kowalczyk",
                   "Achebe", "Henderson", "Mbeki", "Sorensen", "Varga", "Albright"]
RELATIVE_GIVEN = ["Emily", "Jason", "Maria", "Peter", "Hannah", "Victor", "Lucy", "Owen", "Nadia", "Marcus", "Sophie",
                  "Derek", "Alana", "Tobias"]
RELATIVE_FAMILY = ["Santos", "Novak", "Chen", "Patel", "Nguyen", "Lee", "Martin", "Roy", "Singh", "Kim", "Lopez",
                   "Garcia", "Walsh", "Murphy"]
ZH_RELATIVE = ["李娜", "王磊", "张伟", "刘洋", "陈静", "杨帆", "赵敏", "周杰", "吴婷", "徐亮"]
ORGS = ["Lakeview Long-Term Care", "Riverside Family Health Team", "Northgate Pharmacy", "Maplewood Retirement Residence",
        "St. Brendan's Hospital", "Harbourfront Dialysis Centre", "Cedarbrook Community Health Centre",
        "Willowdale Medical Clinic", "Bayview Physiotherapy", "Kingsway Home Care", "Elmwood Manor",
        "Trillium Ridge Hospice", "Glenhaven Nursing Home", "Parkdale Medical Centre"]
ZH_ORGS = ["多伦多华人护理院", "万锦家庭诊所", "士嘉堡康复中心", "北约克华人社区中心", "列治文山药房"]
STREETS = ["Sample Ave", "Example St", "Placeholder Rd", "Fictional Blvd", "Demo Cres", "Mock Lane", "Synthetic Dr",
           "Kingsdale Avenue", "Birchmount Road", "Maple Grove Drive", "Willow Court", "Harbour Street"]
CITIES = ["Toronto", "Markham", "Mississauga", "Richmond Hill", "Scarborough", "Brampton"]
ZH_STREETS = ["央街", "士巴丹拿路", "芬治大道", "登打士街", "皇后街"]
CONDITIONS = ["community-acquired pneumonia", "a COPD exacerbation", "decompensated heart failure", "cellulitis of the "
              "left leg", "a urinary tract infection", "a fall with a left hip fracture", "acute kidney injury",
              "an upper GI bleed", "diabetic ketoacidosis", "atrial fibrillation with rapid ventricular response"]
ZH_CONDITIONS = ["肺炎", "心力衰竭", "髋部骨折", "尿路感染", "慢阻肺急性加重"]
FILLERS = [
    "Started apixaban 5 mg PO BID; held warfarin.",
    "Parkinson's disease stable on carbidopa-levodopa.",
    "Foley catheter removed at 06:00; voiding well.",
    "Glasgow Coma Scale 15/15; pain 4/10 at rest.",
    "BP 132/84, HR 88, RR 18, SpO2 96% on 2 L nasal prongs.",
    "Hb 98, creatinine 142, eGFR 41; repeat CBC and BMP in AM.",
    "Braden score 14; NEWS2 3; CTAS 2 on arrival.",
    "Transferred to MEDA-05-A at 14:30.",
    "Day 3 post-op after Hartmann's procedure in 2019 (history).",
    "Tylenol 650 mg q6h PRN; Lasix 40 mg IV x1 given.",
    "Mobilising with physiotherapy, walker, 2-person assist.",
    "Wound clean and dry; staples intact.",
    "Plan: continue ceftriaxone, reassess in 48 hours.",
    "Morse fall score 55; bed alarm on.",
    "Ate 75% of breakfast; tolerating diabetic diet.",
    "Insulin sliding scale per protocol; glucose 8.4.",
    "Chest X-ray: improving right lower lobe consolidation.",
    "Seen on Medicine A rounds; resident aware.",
    "Allergies: penicillin (rash), sulfa.",
    "Code status: full code, discussed at the 10:00 family meeting.",
    "Power 4/5 in the left leg; sensation intact.",
    "Whipple procedure discussed as an outpatient option in 2024.",
    "Apgar not applicable; adult patient.",
    "MRSA screen negative; contact precautions lifted.",
    # traps: words a de-identifier may take for identifiers
    "Long-term care application in progress.",
    "Seen by the Nurse Practitioner this morning.",
    "Discussed at Patient Care Rounds.",
    "Started on 1/2 dose metoprolol; titrate as tolerated.",
    "Young adult daughter at the bedside overnight.",
    "Price of the walker covered by the assistive devices program.",
    "Hope to mobilise tomorrow if pain allows.",
    "Ext. 4521 is the unit's charge-nurse phone.",
]
NICKNAMES = {"Margaret": "Peggy", "Robert": "Bob", "William": "Bill", "James": "Jim", "Thomas": "Tom",
             "Richard": "Rick", "Edward": "Ted", "Susan": "Sue", "Dorothy": "Dot", "Daniel": "Dan",
             "Andrew": "Andy", "Peter": "Pete", "Steven": "Steve", "Henry": "Hank", "Walter": "Walt",
             "Gerald": "Gerry", "Helen": "Nell", "Beatrice": "Bea", "Phyllis": "Phyl", "Leonard": "Len"}
WORD_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]


class Note:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.spans: list[dict] = []
        self.length = 0

    def t(self, text: str) -> Note:
        self.parts.append(text)
        self.length += len(text)
        return self

    def phi(self, text: str, kind: str, *, known: bool = False, days: int | None = None) -> Note:
        span = {"start": self.length, "end": self.length + len(text), "kind": kind, "text": text, "known": known}
        if days is not None:
            span["days"] = days
        self.spans.append(span)
        return self.t(text)

    @property
    def text(self) -> str:
        return "".join(self.parts)


class Case:
    def __init__(self, rng: random.Random, i: int) -> None:
        self.rng = rng
        self.id = f"deid-{i:03d}"
        self.ref = date(2026, 10, 8) + timedelta(days=rng.randint(-20, 20))
        self.sex = rng.choice(["female", "male"])
        self.chinese = rng.random() < 0.35
        if self.chinese:
            fam, fam_zh = rng.choice(ZH)
            giv, giv_zh = rng.choice(ZH_GIVEN[self.sex])
            self.given, self.family, self.zh = giv, fam, fam_zh + giv_zh
            self.zh_family = fam_zh
        else:
            self.given, self.family, self.zh = rng.choice(EN_GIVEN[self.sex]), rng.choice(EN_FAMILY), None
        self.full = f"{self.given} {self.family}"
        self.mrn = f"{rng.randint(10_000_000, 99_999_999)}"
        self.hcn = "".join(str(rng.randint(0, 9)) for _ in range(10))
        self.vc = rng.choice(["AB", "KT", "XY", "MN", "QR"])
        self.phone = f"{rng.choice(['416', '647', '905', '437'])}-555-01{rng.randint(10, 99)}"
        self.street = f"{rng.randint(5, 3999)} {rng.choice(STREETS)}"
        self.city = rng.choice(CITIES)
        letters = "ABCEGHJKLMNPRSTVXY"
        self.postal = f"{rng.choice('MLK')}{rng.randint(1, 9)}{rng.choice(letters)} {rng.randint(1, 9)}" \
                      f"{rng.choice(letters)}{rng.randint(1, 9)}"
        self.dob = date(rng.randint(1932, 2004), rng.randint(1, 12), rng.randint(1, 28))
        self.staff = [f"{rng.choice(STAFF_GIVEN)} {rng.choice(STAFF_FAMILY)}" for _ in range(4)]
        self.staff = list(dict.fromkeys(self.staff))

    # ----- formatting helpers -----

    def date_near(self, lo: int = -30, hi: int = 10) -> date:
        return self.ref + timedelta(days=self.rng.randint(lo, hi))

    def date_text(self, d: date) -> str:
        r = self.rng.random()
        month, mon = d.strftime("%B"), d.strftime("%b")
        forms = [d.isoformat(), f"{d.month:02d}/{d.day:02d}/{d.year}", f"{mon} {d.day}, {d.year}", f"{month} {d.day}",
                 f"{d.day} {mon} {d.year}", f"{d.year}.{d.month:02d}.{d.day:02d}", f"{month} {d.day}{_th(d.day)}",
                 f"{mon}. {d.day}", f"{d.day}/{d.month}/{d.year}" if d.day > 12 else f"{d.month}/{d.day}/{d.year}"]
        return forms[int(r * len(forms))]

    def phone_text(self, number: str | None = None) -> str:
        n = number or f"{self.rng.choice(['416', '647', '905'])}-555-01{self.rng.randint(10, 99)}"
        a, b, c = n.split("-")
        ext = str(self.rng.randint(10, 9999))
        forms = [f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"{a}.{b}.{c}", f"+1 {a} {b} {c}", f"{a}-{b}-{c} ext. {ext}",
                 f"({a}) {b}-{c} x{ext}", f"{a} {b} {c}", f"{a}-{b}-{c}, extension {ext}"]
        return self.rng.choice(forms)

    def hcn_text(self) -> str:
        h, v = self.hcn, self.vc
        return self.rng.choice([f"{h[:4]}-{h[4:7]}-{h[7:]}-{v}", f"{h[:4]} {h[4:7]} {h[7:]} {v}", f"{h}{v}", h,
                                f"{h[:4]}-{h[4:7]}-{h[7:]}"])

    def known(self) -> dict:
        names = [self.full, f"{self.family} {self.given}"] + ([self.zh] if self.zh else [])
        return {"patient": {"names": names, "mrn": self.mrn, "health_card": self.hcn, "phone": self.phone,
                            "address": self.street, "postal_code": self.postal},
                "staff": [("Dr. " if k < 2 else "") + s for k, s in enumerate(self.staff)]}


def _th(day: int) -> str:
    return "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


# ----- sentences: each writes into the note (text and gold spans) -----


def s_intro(c: Case, n: Note) -> None:
    d = c.date_near(-12, -1)
    age = c.ref.year - c.dob.year
    form = c.rng.random()
    if form < 0.4:
        n.phi(c.full, "PERSON", known=True)
    elif form < 0.6 and c.zh:
        n.phi(c.zh, "PERSON", known=True).t(" (").phi(f"{c.family} {c.given}", "PERSON", known=True).t(")")
    else:
        n.t({"female": "Mrs. ", "male": "Mr. "}[c.sex]).phi(c.family, "PERSON", known=True)
    n.t(f" is a {age}-year-old {c.sex} admitted on ").phi(c.date_text(d), "DATE", days=(d - c.ref).days)
    n.t(f" with {c.rng.choice(CONDITIONS)}. ")


def s_ids(c: Case, n: Note) -> None:
    mrn_form = c.rng.choice(["MRN {}", "MRN: {}", "MRN#{}", "chart #{}", "Medical record number {}"])
    pre, post = mrn_form.split("{}")
    n.t(pre).phi(c.mrn, "MRN", known=True).t(post + ". ")
    if c.rng.random() < 0.8:
        cue = c.rng.choice(["HCN ", "Health card ", "OHIP # ", "Health card number: "])
        n.t(cue).phi(c.hcn_text(), "HEALTH_CARD", known=True).t(". ")
    if c.rng.random() < 0.5:
        n.t("DOB ").phi(c.date_text(c.dob), "DATE", days=(c.dob - c.ref).days).t(". ")


def s_seen(c: Case, n: Note) -> None:
    d = c.date_near(-5, 0)
    who = c.rng.choice(c.staff[:2])
    surname_only = c.rng.random() < 0.4
    n.t("Seen by Dr. ").phi(who.split()[-1] if surname_only else who, "STAFF", known=True)
    n.t(" on ").phi(c.date_text(d), "DATE", days=(d - c.ref).days).t("; ")
    n.t(c.rng.choice(["plan reviewed with the team. ", "agrees with the plan. ", "medications reconciled. "]))


def s_relative_phone(c: Case, n: Note) -> None:
    rel = c.rng.choice(["Daughter", "Son", "Wife", "Husband", "Niece", "SDM", "Emergency contact"])
    name = f"{c.rng.choice(RELATIVE_GIVEN)}" + (f" {c.rng.choice(RELATIVE_FAMILY)}" if c.rng.random() < 0.6 else "")
    sep = c.rng.choice([" ", ": ", ", "])
    n.t(rel + sep).phi(name, "PERSON").t(c.rng.choice([" can be reached at ", ", phone ", " (cell) "]))
    n.phi(c.phone_text(), "PHONE").t(". ")


def s_patient_phone(c: Case, n: Note) -> None:
    a, b, cc = c.phone.split("-")
    form = c.rng.choice([f"({a}) {b}-{cc}", c.phone, f"{a}{b}{cc}", f"{a}.{b}.{cc}"])
    n.t(c.rng.choice(["Patient's own phone: ", "Home phone ", "Call back number "]))
    n.phi(form, "PHONE", known=True).t(". ")


def s_address(c: Case, n: Note) -> None:
    r = c.rng.random()
    n.t(c.rng.choice(["Lives at ", "Home address: ", "Discharge address "]))
    if r < 0.4:
        n.phi(f"{c.street}, {c.city}, ON {c.postal}", "ADDRESS", known=True)
    elif r < 0.7:
        n.phi(c.street, "ADDRESS", known=True).t(" with her daughter" if c.sex == "female" else " with his wife")
    else:
        unit = c.rng.randint(101, 2405)
        n.phi(f"{c.rng.randint(5, 999)} {c.rng.choice(STREETS)}, Unit {unit}, {c.city}", "ADDRESS")
    n.t(". ")


def s_postal(c: Case, n: Note) -> None:
    n.t("Postal code ").phi(c.postal, "ADDRESS", known=True).t(" is outside the home-care catchment. ")


def s_discharge_org(c: Case, n: Note) -> None:
    d = c.date_near(0, 7)
    n.t("Expected discharge ").phi(c.date_text(d), "DATE", days=(d - c.ref).days).t(" to ")
    n.phi(c.rng.choice(ORGS), "ORG").t(". ")


def s_outside_followup(c: Case, n: Note) -> None:
    d = c.date_near(3, 30)
    n.t("Follow-up with Dr. ").phi(c.rng.choice(OUTSIDE_DOCTORS), "STAFF").t(" at ")
    n.phi(c.rng.choice(ORGS), "ORG").t(" on ").phi(c.date_text(d), "DATE", days=(d - c.ref).days).t(". ")


def s_pharmacy(c: Case, n: Note) -> None:
    n.t("Community pharmacy: ").phi(c.rng.choice(["Northgate Pharmacy", "Kingsway Drug Mart", "Bayview Pharmacy"]),
                                    "ORG")
    n.t(" (").phi(c.phone_text(), "PHONE").t("). ")


def s_email(c: Case, n: Note) -> None:
    who = c.rng.choice(RELATIVE_GIVEN)
    n.t("Family prefers email updates: ").phi(f"{who.lower()}.{c.rng.choice(RELATIVE_FAMILY).lower()}"
                                              f"{c.rng.randint(1, 99)}@example.com", "EMAIL").t(". ")


def s_nurse(c: Case, n: Note) -> None:
    nurse = c.staff[2] if len(c.staff) > 2 else c.staff[0]
    r = c.rng.random()
    if r < 0.35:
        n.t("Covering nurse ").phi(nurse, "STAFF", known=True).t(", RN. ")
    elif r < 0.65:
        n.t("Handed over to RN ").phi(nurse, "STAFF", known=True).t(" at 19:00. ")
    else:
        g, f = nurse.split(" ", 1)
        n.t("Signed: ").phi(f"{g[0]}. {f}", "STAFF", known=True).t(c.rng.choice([", RN", ", RPN", " RN"])).t(". ")


def s_given_name(c: Case, n: Note) -> None:
    n.phi(c.given, "PERSON", known=True).t(c.rng.choice([" reports feeling better today. ",
                                                         " asked about going home. ", " slept well overnight. "]))


def s_family_name(c: Case, n: Note) -> None:
    title = {"female": c.rng.choice(["Mrs.", "Ms."]), "male": "Mr."}[c.sex]
    n.t(f"{title} ").phi(c.family, "PERSON", known=True).t(c.rng.choice(["'s pain is controlled. ",
                                                                         " tolerated the walk well. "]))


def s_short_date(c: Case, n: Note) -> None:
    d = c.date_near(1, 20)
    n.t(c.rng.choice(["Next appointment ", "Clinic appointment ", "Follow-up "]))
    n.phi(f"{d.month}/{d.day}", "DATE", days=(d - c.ref).days).t(" at 09:30. ")


def s_month_year(c: Case, n: Note) -> None:
    d = c.date_near(-400, -60)
    n.t("Previous admission ").phi(f"{d.strftime('%B')} {d.year}", "DATE").t(" at ")
    n.phi(c.rng.choice(ORGS), "ORG").t(". ")


def s_zh_intro(c: Case, n: Note) -> None:
    d = c.date_near(-10, -1)
    n.t("患者").phi(c.zh, "PERSON", known=True).t("，")
    n.phi(f"{d.year}年{d.month}月{d.day}日", "DATE", days=(d - c.ref).days)
    n.t(f"因{c.rng.choice(ZH_CONDITIONS)}入院。")


def s_zh_family(c: Case, n: Note) -> None:
    rel = c.rng.choice(["女儿", "儿子", "妻子", "丈夫"])
    n.t("家属").phi(c.rng.choice(ZH_RELATIVE), "PERSON").t(f"（{rel}），电话 ")
    a, b, cc = f"{c.rng.choice(['416', '647', '905'])}-555-01{c.rng.randint(10, 99)}".split("-")
    n.phi(f"{a}-{b}-{cc} 分机 {c.rng.randint(10, 999)}" if c.rng.random() < 0.6 else f"{a}-{b}-{cc}", "PHONE")
    n.t("。")


def s_zh_honorific(c: Case, n: Note) -> None:
    n.phi(c.zh_family, "PERSON", known=False).t({"female": "女士", "male": "先生"}[c.sex])
    d = c.date_near(-3, 0)
    n.t("今日精神好转，").phi(f"{d.month}月{d.day}日", "DATE", days=(d - c.ref).days).t("复查血常规。")


def s_zh_address(c: Case, n: Note) -> None:
    n.t("住址：").phi(f"多伦多市{c.rng.choice(ZH_STREETS)}{c.rng.randint(10, 9999)}号", "ADDRESS").t("。")


def s_zh_org(c: Case, n: Note) -> None:
    n.t("出院后转至").phi(c.rng.choice(ZH_ORGS), "ORG").t("。")


def s_zh_mrn(c: Case, n: Note) -> None:
    n.t("病历号 ").phi(c.mrn, "MRN", known=True).t("。")


def s_relation_en_zh(c: Case, n: Note) -> None:
    rel = c.rng.choice(["daughter", "son", "husband", "wife"])
    n.t(f"Interpreter not needed; {rel} ").phi(c.rng.choice(ZH_RELATIVE), "PERSON").t(" translated at the bedside. ")


def s_spoke_with(c: Case, n: Note) -> None:
    rel = c.rng.choice(["son", "daughter", "partner", "brother", "sister"])
    name = f"{c.rng.choice(RELATIVE_GIVEN)} {c.rng.choice(RELATIVE_FAMILY)}"
    d = c.date_near(-2, 0)
    n.t(f"Spoke with {rel}, ").phi(name, "PERSON").t(", by phone on ")
    n.phi(c.date_text(d), "DATE", days=(d - c.ref).days).t(". ")


def s_iso_time(c: Case, n: Note) -> None:
    d = c.date_near(-1, 0)
    n.t("Last dose given ").phi(f"{d.isoformat()}T{c.rng.randint(0, 23):02d}:{c.rng.choice(['00', '30'])}", "DATE",
                                days=(d - c.ref).days).t(". ")


def s_dotted_or_dmy(c: Case, n: Note) -> None:
    d = c.date_near(-30, -1)
    form = c.rng.choice([f"{d.day:02d}.{d.month:02d}.{d.year}", f"{d.day} {d.strftime('%B')} {d.year}",
                         f"{d.day}{_th(d.day)} of {d.strftime('%B')}"])
    n.t("Echo done ").phi(form, "DATE", days=(d - c.ref).days).t(": EF 45%. ")


# ----- harder variants (realistic, not written for the rules) -----


def h_relative_after_name(c: Case, n: Note) -> None:
    n.phi(c.rng.choice(RELATIVE_GIVEN), "PERSON").t(f" ({c.rng.choice(['daughter', 'son', 'niece', 'neighbour'])}) "
                                                    "brought clean clothes. ")


def h_nickname(c: Case, n: Note) -> None:
    nick = NICKNAMES.get(c.given)
    if nick is None:
        return s_given_name(c, n)
    n.t("Prefers to be called ").phi(nick, "PERSON").t(". ")


def h_cueless_visitors(c: Case, n: Note) -> None:
    a, b = c.rng.sample(RELATIVE_GIVEN, 2)
    n.phi(a, "PERSON").t(" and ").phi(b, "PERSON").t(" visited in the evening. ")


def h_lowercase_doctor(c: Case, n: Note) -> None:
    n.t("Discussed with dr. ").phi(c.rng.choice(OUTSIDE_DOCTORS).lower(), "STAFF").t(" by phone. ")


def h_word_date(c: Case, n: Note) -> None:
    d = c.date_near(1, 9)
    n.t("Surgery booked for the ").phi(f"{WORD_ORDINALS[d.day - 1]} of {d.strftime('%B')}"
                                       if d.day <= 10 else f"{d.day}th of {d.strftime('%B')}", "DATE",
                                       days=(d - c.ref).days).t(". ")


def h_short_year(c: Case, n: Note) -> None:
    d = c.date_near(-60, -10)
    n.t("Last colonoscopy ").phi(f"{d.strftime('%b')} {d.day}{_th(d.day)} '{d.year % 100:02d}", "DATE",
                                 days=(d - c.ref).days).t(", normal. ")


def h_odd_phone(c: Case, n: Note) -> None:
    n.t("Neighbour's landline ").phi(f"{c.rng.choice(['416', '905'])} 55501{c.rng.randint(10, 99)}", "PHONE").t(". ")


def h_full_name_no_cue(c: Case, n: Note) -> None:
    name = f"{c.rng.choice(RELATIVE_GIVEN)} {c.rng.choice(RELATIVE_FAMILY)}"
    n.phi(name, "PERSON").t(" will drive the patient home. ")


HARD_SENTENCES = [h_relative_after_name, h_nickname, h_cueless_visitors, h_lowercase_doctor, h_word_date,
                  h_short_year, h_odd_phone, h_full_name_no_cue]


EN_SENTENCES = [s_seen, s_relative_phone, s_patient_phone, s_address, s_postal, s_discharge_org, s_outside_followup,
                s_pharmacy, s_email, s_nurse, s_given_name, s_family_name, s_short_date, s_month_year, s_spoke_with,
                s_iso_time, s_dotted_or_dmy]
ZH_SENTENCES = [s_zh_intro, s_zh_family, s_zh_honorific, s_zh_address, s_zh_org, s_zh_mrn, s_relation_en_zh]
KINDS = ["Progress note", "Nursing note", "Discharge note", "Handoff", "Call log", "Consult note"]


def build(rng: random.Random, i: int) -> dict:
    c = Case(rng, i)
    n = Note()
    n.t(f"{rng.choice(KINDS)}. ")
    s_intro(c, n)
    s_ids(c, n)
    picks = rng.sample(EN_SENTENCES, k=rng.randint(3, 6))
    if rng.random() < 0.3:
        picks.append(rng.choice(HARD_SENTENCES))
    if c.zh:
        picks += rng.sample(ZH_SENTENCES, k=rng.randint(1, 3))
        rng.shuffle(picks)
    for fill in rng.sample(FILLERS, k=rng.randint(2, 4)):
        picks.insert(rng.randint(0, len(picks)), fill)
    for item in picks:
        if isinstance(item, str):
            n.t(item + " ")
        else:
            item(c, n)
    text = n.text.rstrip()
    spans = [s for s in n.spans if s["end"] <= len(text)]
    for s in spans:
        assert text[s["start"]:s["end"]] == s["text"], (c.id, s)
    return {"id": c.id, "reference_date": c.ref.isoformat(), "known": c.known(), "text": text, "phi": spans}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=6300)
    parser.add_argument("--n", type=int, default=200)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    cases = [build(rng, i + 1) for i in range(args.n)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    kinds: dict[str, int] = {}
    for case in cases:
        for s in case["phi"]:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print(f"{len(cases)} notes, {sum(kinds.values())} PHI spans -> {OUT}")
    print(", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))


if __name__ == "__main__":
    main()
