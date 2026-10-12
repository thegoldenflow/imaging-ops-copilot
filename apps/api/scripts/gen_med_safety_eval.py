"""Write the medication-safety eval sets (spec 7.2): 60 labelled order-review cases and 20 reconciliation cases.

    cd apps/api && uv run python scripts/gen_med_safety_eval.py

Writes evals/order_review/cases.jsonl and evals/medrec/cases.jsonl (deterministic; the files are committed).
Every case is a small synthetic patient: age, weight, kidney function, INR, potassium, allergies, diagnoses (the
stay's diagnosis first), home medications, other active orders and the order under review. The labels are what a
pharmacist reviewer would want flagged (concern types), whether the order has an indication on the diagnosis list,
and for the reconciliations the discrepancy type of every row. They were written by the same author as the mock
model (app/modules/med_safety/mocks.py), so a mock run scores optimistically; `--live` runs the configured model.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Condition codes (app/ehr/codes.py): AFib 49436004, HTN 59621000, diabetes 44054006, CHF 42343007, COPD 13645005,
# CKD3 433144002, CKD4 431857002, hyperlipidemia 55822004, depression 35489007, OA 396275006, RA 69896004,
# Alzheimer's 26929004, gout 90560007, hypothyroid 40930008, hip fracture 5913000, fracture 125605004,
# pneumonia 233604007, MI 22298006, sepsis 91302008, UTI 68566005, appendicitis 74400008, cellulitis 396230008,
# AECOPD 195951007, backache 161891005, fall 1912002.


def med(rxcui: str, dose: float | None = None, per_day: float | None = None, reason: str | None = None,
        prn: bool = False) -> dict:
    out: dict = {"rxcui": rxcui}
    if dose is not None:
        out["dose"] = dose
    if per_day is not None:
        out["per_day"] = per_day
    if reason:
        out["reason"] = reason
    if prn:
        out["prn"] = True
    return out


def case(n: int, title: str, *, age: int, dx: str, order: dict, expect: list[str], indicated: bool | None = None,
         conditions: tuple[str, ...] = (), home: tuple[dict, ...] = (), orders: tuple[dict, ...] = (),
         allergies: tuple[str, ...] = (), weight: float = 72, egfr: float = 80, inr: float | None = None,
         potassium: float | None = None, sex: str = "female", services: tuple[dict, ...] = (), note: str = "") -> dict:
    out = {"id": f"or-{n:03d}", "title": title, "source": "authored",
           "patient": {"age": age, "sex": sex, "weight": weight, "egfr": egfr, "inr": inr, "potassium": potassium},
           "encounter_dx": dx, "conditions": list(conditions), "allergies": list(allergies),
           "home": list(home), "orders": list(orders), "services": list(services), "order": order,
           "expected": {"concerns": sorted(set(expect))}}
    if indicated is not None:
        out["expected"]["indicated"] = indicated
    if note:
        out["note"] = note
    return out


def lab(code: str, hours_before: float, resulted_after: float | None = 2.0, category: str = "lab") -> dict:
    return {"code": code, "hours_before": hours_before, "resulted_after": resulted_after, "category": category}


ORDER_REVIEW = [
    case(1, "Warfarin at home, naproxen added after a hip fracture, INR 3.8", age=78, dx="5913000",
         conditions=("49436004",), home=(med("11289"),), orders=(med("11289"),), inr=3.8, order=med("7258"),
         expect=["interaction", "monitoring", "age_related"], indicated=True),
    case(2, "Warfarin and a new ibuprofen for back pain", age=60, dx="161891005", conditions=("49436004",),
         home=(med("11289"),), orders=(med("11289"),), inr=2.5, order=med("5640"), expect=["interaction"],
         indicated=True),
    case(3, "Apixaban and intravenous ketorolac after appendicitis", age=55, dx="74400008", conditions=("49436004",),
         home=(med("1364430"),), order=med("35827"), expect=["interaction"], indicated=True),
    case(4, "Enoxaparin prophylaxis and celecoxib for osteoarthritis at 70", age=70, dx="125605004",
         conditions=("396275006",), orders=(med("67108", reason="125605004"),), order=med("140587"),
         expect=["interaction", "age_related"], indicated=True),
    case(5, "Penicillin allergy, piperacillin for sepsis", age=66, dx="91302008", allergies=("764146007",),
         order=med("8339", reason="91302008"), expect=["allergy"], indicated=True),
    case(6, "Aspirin allergy (NSAID class), ibuprofen for a fracture", age=45, dx="125605004", allergies=("1191",),
         order=med("5640"), expect=["allergy"], indicated=True),
    case(7, "Ceftriaxone allergy (cephalosporin), cefazolin for cellulitis", age=52, dx="396230008",
         allergies=("2193",), order=med("2180", reason="396230008"), expect=["allergy"], indicated=True),
    case(8, "Morphine allergy (opioid), hydromorphone after a hip fracture", age=58, dx="5913000",
         allergies=("7052",), order=med("3423", prn=True), expect=["allergy"], indicated=True),
    case(9, "Metformin continued with an eGFR of 22", age=74, dx="233604007", conditions=("44054006", "431857002"),
         home=(med("6809"),), egfr=22, order=med("6809"), expect=["renal"], indicated=True),
    case(10, "Gabapentin started with an eGFR of 25 and no neuropathic diagnosis", age=69, dx="1912002",
         conditions=("431857002",), egfr=25, order=med("25480"), expect=["renal", "indication"], indicated=False),
    case(11, "Nitrofurantoin for a urinary infection with an eGFR of 18", age=81, dx="68566005",
         conditions=("431857002",), egfr=18, order=med("7454", reason="68566005"), expect=["renal"], indicated=True),
    case(12, "Enoxaparin 40 mg after a hip fracture with an eGFR of 28", age=86, dx="5913000",
         conditions=("431857002",), egfr=28, order=med("67108"), expect=["renal"], indicated=True),
    case(13, "Metformin continued with an eGFR of 45", age=63, dx="21522001", conditions=("44054006", "433144002"),
         home=(med("6809"),), egfr=45, order=med("6809"), expect=[], indicated=True,
         note="Above the rule threshold; a reviewer would not flag it."),
    case(14, "Tramadol 100 mg four times a day at 80", age=80, dx="161891005", order=med("10689", 100, 4),
         expect=["dose"], indicated=True),
    case(15, "Acetaminophen 1 g four times a day at 44 kg", age=91, dx="5913000", weight=44,
         order=med("161", 1000, 4), expect=["dose"], indicated=True),
    case(16, "Ketorolac 30 mg every 6 hours at 72", age=72, dx="125605004", order=med("35827", 30, 4),
         expect=["dose", "age_related"], indicated=True),
    case(17, "Methotrexate 15 mg ordered daily for rheumatoid arthritis", age=64, dx="1912002",
         conditions=("69896004",), home=(med("6851"),), order=med("6851", 15, 1), expect=["dose"], indicated=True),
    case(18, "Vancomycin 1 g twice a day at 110 kg", age=57, dx="91302008", weight=110,
         order=med("11124", 1000, 2, reason="91302008"), expect=["dose", "monitoring"], indicated=True),
    case(19, "Lorazepam started for sleep at 84", age=84, dx="1912002", order=med("6470", 1, 1),
         expect=["age_related", "indication"], indicated=False),
    case(20, "Quetiapine started at 88 with Alzheimer's disease", age=88, dx="68566005", conditions=("26929004",),
         order=med("51272"), expect=["age_related"], indicated=False,
         note="Antipsychotics for behaviour in dementia are not a diagnosis-backed indication."),
    case(21, "Tramadol for back pain on sertraline", age=48, dx="161891005", conditions=("35489007",),
         home=(med("36437"),), orders=(med("36437"),), order=med("10689"), expect=["interaction"], indicated=True),
    case(22, "Lorazepam added to morphine at 70", age=70, dx="125605004", orders=(med("7052", prn=True),),
         order=med("6470"), expect=["interaction", "age_related", "indication"], indicated=False),
    case(23, "Spironolactone added to lisinopril and potassium in heart failure, K 5.4", age=76, dx="42343007",
         conditions=("42343007",), home=(med("29046"),), orders=(med("29046"), med("8591")), potassium=5.4,
         order=med("9997"), expect=["interaction", "monitoring"], indicated=True),
    case(24, "Potassium chloride added to spironolactone, K 4.0", age=71, dx="42343007", conditions=("42343007",),
         home=(med("9997"),), potassium=4.0, order=med("8591"), expect=["interaction", "indication"],
         indicated=False),
    case(25, "Clarithromycin for pneumonia on simvastatin", age=67, dx="233604007", conditions=("55822004",),
         home=(med("36567"),), order=med("21212", reason="233604007"), expect=["interaction"], indicated=True),
    case(26, "Amiodarone started on digoxin", age=79, dx="29857009", conditions=("49436004",),
         home=(med("3407"),), orders=(med("3407"),), order=med("703"), expect=["interaction", "monitoring"],
         indicated=True),
    case(27, "Ibuprofen for back pain on lithium", age=44, dx="161891005", home=(med("6448"),), order=med("5640"),
         expect=["interaction"], indicated=True),
    case(28, "Sulfamethoxazole for a urinary infection on methotrexate", age=62, dx="68566005",
         conditions=("69896004",), home=(med("6851", 15, 1 / 7),), order=med("10180", reason="68566005"),
         expect=["interaction"], indicated=True),
    case(29, "Omeprazole added to clopidogrel", age=66, dx="29857009", conditions=("22298006",),
         home=(med("32968"),), orders=(med("32968"),), order=med("7646"), expect=["interaction"], indicated=False,
         note="Gastroprotection is a reason, not a diagnosis on the list."),
    case(30, "Pantoprazole started with nothing to protect against", age=50, dx="21522001", order=med("40790"),
         expect=["indication"], indicated=False),
    case(31, "Ceftriaxone for pneumonia", age=59, dx="233604007", order=med("2193", reason="233604007"),
         expect=[], indicated=True),
    case(32, "Acetaminophen 1 g four times a day at 70 kg after a fracture", age=61, dx="125605004", weight=70,
         order=med("161", 1000, 4), expect=[], indicated=True),
    case(33, "Atorvastatin after a myocardial infarction", age=66, dx="22298006", order=med("83367"), expect=[],
         indicated=True),
    case(34, "Amlodipine continued for hypertension", age=58, dx="21522001", conditions=("59621000",),
         home=(med("17767"),), order=med("17767"), expect=[], indicated=True),
    case(35, "Metoprolol continued for atrial fibrillation", age=73, dx="230690007", conditions=("49436004",),
         home=(med("6918"),), order=med("6918"), expect=[], indicated=True),
    case(36, "Insulin glargine continued for diabetes", age=55, dx="68566005", conditions=("44054006",),
         home=(med("274783"),), order=med("274783"), expect=[], indicated=True),
    case(37, "Enoxaparin 40 mg after a hip fracture, normal kidneys", age=74, dx="5913000", order=med("67108"),
         expect=[], indicated=True),
    case(38, "Hydromorphone as needed after a fracture at 50", age=50, dx="125605004", order=med("3423", prn=True),
         expect=[], indicated=True),
    case(39, "Ondansetron as needed after an appendectomy", age=34, dx="74400008", order=med("26225", prn=True),
         expect=[], indicated=True),
    case(40, "Salbutamol in a COPD exacerbation", age=68, dx="195951007", conditions=("13645005",),
         order=med("435"), expect=[], indicated=True),
    case(41, "Simvastatin added to atorvastatin", age=62, dx="29857009", conditions=("55822004",),
         home=(med("83367"),), orders=(med("83367"),), order=med("36567"), expect=["duplicate_therapy"],
         indicated=True),
    case(42, "Hydromorphone added to morphine after a fracture", age=40, dx="125605004",
         orders=(med("7052", prn=True),), order=med("3423", prn=True), expect=["duplicate_therapy"], indicated=True),
    case(43, "Pantoprazole added to omeprazole", age=57, dx="21522001", home=(med("7646"),), orders=(med("7646"),),
         order=med("40790"), expect=["duplicate_therapy", "indication"], indicated=False),
    case(44, "Enoxaparin bridging on warfarin, INR 1.6", age=69, dx="230690007", conditions=("49436004",),
         home=(med("11289"),), orders=(med("11289"),), inr=1.6, order=med("67108"), expect=["duplicate_therapy"],
         indicated=True),
    case(45, "Clopidogrel added to aspirin after a myocardial infarction", age=61, dx="22298006",
         orders=(med("1191"),), order=med("32968"), expect=[], indicated=True,
         note="Dual antiplatelet therapy after a myocardial infarction is intended."),
    case(46, "Warfarin continued, INR 2.4", age=75, dx="233604007", conditions=("49436004",),
         home=(med("11289"),), inr=2.4, order=med("11289"), expect=[], indicated=True),
    case(47, "Warfarin continued with no INR on record", age=68, dx="1912002", conditions=("49436004",),
         home=(med("11289"),), order=med("11289"), expect=["monitoring"], indicated=True),
    case(48, "Ciprofloxacin for a urinary infection on warfarin, no recent INR", age=77, dx="68566005",
         conditions=("49436004",), home=(med("11289"),), orders=(med("11289"),), order=med("2551", reason="68566005"),
         expect=["interaction", "monitoring"], indicated=True),
    case(49, "Lisinopril started with potassium 5.6", age=66, dx="21522001", conditions=("59621000", "433144002"),
         egfr=40, potassium=5.6, order=med("29046"), expect=["monitoring"], indicated=True),
    case(50, "Vancomycin 1.5 g at 80 kg in sepsis", age=60, dx="91302008", weight=80,
         order=med("11124", 1500, 2, reason="91302008"), expect=["monitoring"], indicated=True),
    case(51, "Digoxin 0.125 mg continued at 75", age=75, dx="42343007", conditions=("49436004",),
         home=(med("3407"),), order=med("3407", 0.125, 1), expect=["monitoring"], indicated=True),
    case(52, "Glyburide continued at 79", age=79, dx="68566005", conditions=("44054006",), home=(med("4815"),),
         order=med("4815"), expect=["age_related"], indicated=True),
    case(53, "Naproxen for back pain at 40", age=40, dx="161891005", order=med("7258"), expect=[], indicated=True),
    case(54, "Ibuprofen for osteoarthritis at 82 with an eGFR of 55", age=82, dx="1912002",
         conditions=("396275006", "433144002"), egfr=55, order=med("5640"), expect=["age_related", "renal"],
         indicated=True),
    case(55, "Blood count repeated a day after the last one", age=63, dx="233604007",
         services=(lab("718-7", 24),), order={"service": "718-7", "category": "lab"}, expect=["duplicate_test"]),
    case(56, "Serial troponin three hours after the first", age=58, dx="29857009",
         services=(lab("10839-9", 3, 1),), order={"service": "10839-9", "category": "lab"}, expect=[],
         note="Serial troponins are planned repeats."),
    case(57, "Creatinine repeated after 80 hours", age=70, dx="233604007",
         services=(lab("2160-0", 80),), order={"service": "2160-0", "category": "lab"}, expect=[]),
    case(58, "Chest X-ray repeated after 30 hours", age=74, dx="233604007",
         services=(lab("XR_CHEST", 30, 4, "imaging"),), order={"service": "XR_CHEST", "category": "imaging"},
         expect=["duplicate_test"]),
    case(59, "Potassium repeated while the first is still pending", age=66, dx="42343007",
         services=(lab("2823-3", 2, None),), order={"service": "2823-3", "category": "lab"}, expect=[]),
    case(60, "Hemoglobin repeated after 50 hours in a stable patient", age=52, dx="21522001",
         services=(lab("718-7", 50),), order={"service": "718-7", "category": "lab"}, expect=["duplicate_test"]),
]


def rec(n: int, title: str, kind: str, *, dx: str, expect: dict[str, str], conditions: tuple[str, ...] = (),
        home: tuple[dict, ...] = (), orders: tuple[dict, ...] = (), discharge: tuple[dict, ...] = (),
        age: int = 70) -> dict:
    return {"id": f"mr-{n:03d}", "title": title, "source": "authored", "kind": kind,
            "patient": {"age": age, "sex": "male" if n % 2 else "female", "weight": 75, "egfr": 75, "inr": None,
                        "potassium": None},
            "encounter_dx": dx, "conditions": list(conditions), "home": list(home), "orders": list(orders),
            "discharge": list(discharge), "expected": {"items": expect}}


MEDREC = [
    rec(1, "Hip fracture on warfarin: an antihypertensive missed, a dose change, naproxen and enoxaparin", "admission",
        dx="5913000", conditions=("49436004", "59621000"),
        home=(med("11289"), med("17767"), med("6918")),
        orders=(med("11289"), med("6918", 25), med("67108", reason="5913000"), med("7258")),
        expect={"ok-11289": "match", "om-17767": "omission", "dc-6918": "dose_change", "new-67108": "new",
                "new-7258": "new", "dup-anticoagulant": "duplicate_therapy"}),
    rec(2, "Omeprazole at home, pantoprazole in hospital", "admission", dx="21522001", conditions=("59621000",),
        home=(med("7646"), med("29046")), orders=(med("40790"), med("29046")),
        expect={"om-7646": "omission", "ok-29046": "match", "new-40790": "new_without_indication"}),
    rec(3, "Metformin doubled, insulin started", "admission", dx="68566005", conditions=("44054006",),
        home=(med("6809"),), orders=(med("6809", 1000), med("274783")),
        expect={"dc-6809": "dose_change", "new-274783": "new"}),
    rec(4, "Simvastatin added to atorvastatin", "admission", dx="29857009", conditions=("55822004",),
        home=(med("83367"),), orders=(med("83367"), med("36567")),
        expect={"ok-83367": "match", "new-36567": "new", "dup-statin": "duplicate_therapy"}),
    rec(5, "Sertraline left off, tramadol for back pain", "admission", dx="161891005", conditions=("35489007",),
        home=(med("36437"),), orders=(med("10689"),),
        expect={"om-36437": "omission", "new-10689": "new"}),
    rec(6, "Levothyroxine twice a day, allopurinol missed, colchicine for gout", "admission", dx="1912002",
        conditions=("40930008", "90560007"), home=(med("10582"), med("519")),
        orders=(med("10582", per_day=2), med("2683")),
        expect={"dc-10582": "dose_change", "om-519": "omission", "new-2683": "new"}),
    rec(7, "Quetiapine continued in Alzheimer's disease", "admission", dx="68566005", conditions=("26929004",),
        home=(med("51272"),), orders=(med("51272"),), expect={"ok-51272": "match"}),
    rec(8, "Lorazepam started with no diagnosis for it", "admission", dx="1912002", orders=(med("6470"),),
        expect={"new-6470": "new_without_indication"}),
    rec(9, "Heart failure: furosemide doubled, spironolactone held, potassium started", "admission", dx="42343007",
        conditions=("42343007",), home=(med("4603"), med("9997")), orders=(med("4603", 80), med("8591")),
        expect={"dc-4603": "dose_change", "om-9997": "omission", "new-8591": "new_without_indication"}),
    rec(10, "COPD exacerbation: tiotropium held, prednisone started", "admission", dx="195951007",
        conditions=("13645005",), home=(med("435"), med("69120")), orders=(med("435"), med("8640")),
        expect={"ok-435": "match", "om-69120": "omission", "new-8640": "new"}),
    rec(11, "Pneumonia: home naproxen stopped, IV antibiotic to amoxicillin", "discharge", dx="233604007",
        conditions=("59621000",), home=(med("17767"), med("7258")),
        orders=(med("17767"), med("2193", reason="233604007"), med("3423", prn=True)),
        discharge=(med("17767"), med("723")),
        expect={"ok-17767": "match", "om-7258": "omission", "om-2193": "omission", "om-3423": "omission",
                "new-723": "new"}),
    rec(12, "Heart failure: furosemide increased at discharge", "discharge", dx="42343007",
        conditions=("42343007", "49436004"), home=(med("4603"), med("6918")), orders=(med("4603"), med("6918")),
        discharge=(med("4603", 80), med("6918")), expect={"dc-4603": "dose_change", "ok-6918": "match"}),
    rec(13, "Hip fracture on warfarin: enoxaparin continued with warfarin", "discharge", dx="5913000",
        conditions=("49436004",), home=(med("11289"),),
        orders=(med("11289"), med("67108", reason="5913000")),
        discharge=(med("11289"), med("67108", reason="5913000")),
        expect={"ok-11289": "match", "new-67108": "new", "dup-anticoagulant": "duplicate_therapy"}),
    rec(14, "Omeprazole at home, pantoprazole and omeprazole at discharge", "discharge", dx="21522001",
        home=(med("7646"),), orders=(med("40790"),), discharge=(med("40790"), med("7646")),
        expect={"ok-7646": "match", "new-40790": "new_without_indication",
                "dup-proton-pump-inhibitor": "duplicate_therapy"}),
    rec(15, "Diabetes: insulin added to metformin at discharge", "discharge", dx="68566005", conditions=("44054006",),
        home=(med("6809"),), orders=(med("274783"),), discharge=(med("6809"), med("274783")),
        expect={"ok-6809": "match", "new-274783": "new"}),
    rec(16, "After a myocardial infarction: statin switched, dual antiplatelets", "discharge", dx="22298006",
        home=(med("36567"),), orders=(med("83367", reason="22298006"),),
        discharge=(med("83367", reason="22298006"), med("1191"), med("32968")),
        expect={"om-36567": "omission", "new-83367": "new", "new-1191": "new", "new-32968": "new",
                "dup-antiplatelet": "duplicate_therapy"}),
    rec(17, "Lisinopril doubled at discharge", "discharge", dx="21522001", conditions=("59621000",),
        home=(med("29046"),), orders=(med("29046"),), discharge=(med("29046", 20),),
        expect={"dc-29046": "dose_change"}),
    rec(18, "Lorazepam started in hospital and prescribed at discharge", "discharge", dx="1912002",
        conditions=("35489007",), home=(med("36437"),), orders=(med("36437"), med("6470", prn=True)),
        discharge=(med("36437"), med("6470", prn=True)),
        expect={"ok-36437": "match", "new-6470": "new_without_indication"}),
    rec(19, "Fracture: as-needed morphine and ondansetron stop, tramadol at discharge", "discharge", dx="125605004",
        home=(med("161"),), orders=(med("161"), med("7052", prn=True), med("26225", prn=True)),
        discharge=(med("161"), med("10689")),
        expect={"ok-161": "match", "om-7052": "omission", "om-26225": "omission", "new-10689": "new"}),
    rec(20, "Sepsis: two IV antibiotics stop, amoxicillin at discharge", "discharge", dx="91302008",
        orders=(med("11124", reason="91302008"), med("8339", reason="91302008")), discharge=(med("723"),),
        expect={"om-11124": "omission", "om-8339": "omission", "new-723": "new"}),
]


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    assert len(ORDER_REVIEW) == 60 and len({c["id"] for c in ORDER_REVIEW}) == 60
    assert len(MEDREC) == 20
    write(ROOT / "evals" / "order_review" / "cases.jsonl", ORDER_REVIEW)
    write(ROOT / "evals" / "medrec" / "cases.jsonl", MEDREC)
    print(f"wrote {len(ORDER_REVIEW)} order-review cases and {len(MEDREC)} reconciliation cases")
