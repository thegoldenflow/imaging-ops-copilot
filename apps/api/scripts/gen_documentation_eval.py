"""Write the documentation eval cases (spec 7.3): evals/documentation/cases.jsonl, 30 synthetic stays.

    cd apps/api && uv run python scripts/gen_documentation_eval.py

Deterministic (fixed random seed). Each case is a Medicine A stay with what a discharge needs: diagnoses, often a
procedure, home medications, inpatient orders, discharge prescriptions (continued, changed, new and stopped against
the home list), vital signs, abnormal results, sometimes a report, a pending test, an open task and a flag, the
discharge plan; 10 Chinese-speaking patients (6 Simplified, 4 Traditional), 8 on warfarin, 3 with a note that names
a medicine that was only discussed (the temptation behind the raw hallucination rate). `expected` lists what an
accurate summary contains (diagnosis and procedure codes, follow-up clinics, the pending test); the medication
categories are checked against the signed discharge medication table itself. Synthetic data only.
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ehr import codes as C  # noqa: E402
from app.ehr.reference import INPATIENT_MEDS  # noqa: E402

OUT = Path(__file__).resolve().parents[3] / "evals" / "documentation" / "cases.jsonl"
# admission diagnosis -> (title, procedure, report, follow-up clinic, abnormal labs (code, first, last))
STAYS = {
    "22298006": ("Heart attack", "415070008", ("echo", "Mildly reduced left ventricular function."), "heart_clinic",
                 [("10839-9", 2.4, 0.3)]),
    "42343007": ("Heart failure", None, ("chest-xray", "Small bilateral pleural effusions, improving."),
                 "heart_failure_clinic", [("33762-6", 4200, 1800), ("2823-3", 3.2, 4.1)]),
    "233604007": ("Pneumonia", None, ("chest-xray", "Right lower lobe consolidation."), None,
                  [("6690-2", 15.8, 9.1), ("1988-5", 120, 35)]),
    "195951007": ("COPD exacerbation", None, ("chest-xray", "Hyperinflated lungs, no consolidation."), "lung_clinic",
                  [("6690-2", 12.4, 8.0)]),
    "91302008": ("Sepsis", None, None, None, [("2524-7", 4.1, 1.2), ("6690-2", 19.1, 10.2), ("1988-5", 180, 40)]),
    "68566005": ("Urinary tract infection", None, None, None, [("6690-2", 13.2, 8.1)]),
    "230690007": ("Stroke", None, ("ct-head", "Small left parietal infarct, no bleeding."), "stroke_clinic", []),
    "396230008": ("Cellulitis", None, None, None, [("1988-5", 85, 20)]),
    "5913000": ("Broken hip", "52734007", ("xray-hip", "Displaced fracture of the left femoral neck."),
                "fracture_clinic", [("718-7", 9.8, 10.6)]),
    "65275009": ("Acute cholecystitis", "45595009", ("us-abdomen", "Gallstones with gallbladder wall thickening."),
                 "surgery_clinic", [("6690-2", 14.0, 8.5)]),
}
CHRONIC = {"59621000": ["29046", "17767", "5487"], "44054006": ["6809", "593411"], "49436004": ["11289"],
           "55822004": ["83367"], "13645005": ["69120", "435"], "40930008": ["10582"], "35489007": ["36437"]}
TASKS = ["Arrange a home care assessment before discharge", "Teach the patient to use the new inhaler",
         "Book a ride home for the patient", "Confirm the pharmacy has the new prescriptions"]


def vitals(rng: random.Random, sick: bool) -> dict:
    return {"hr": rng.randint(95, 118) if sick else rng.randint(64, 86), "sbp": rng.randint(96, 160),
            "dbp": rng.randint(55, 92), "rr": rng.randint(20, 26) if sick else rng.randint(14, 19),
            "temp": round(rng.uniform(38.0, 39.4) if sick else rng.uniform(36.4, 37.3), 1),
            "spo2": rng.randint(88, 93) if sick else rng.randint(94, 99), "pain": rng.randint(4, 8) if sick else
            rng.randint(0, 3)}


def case(n: int, rng: random.Random) -> dict:
    dx = list(STAYS)[n % len(STAYS)]
    title, procedure, report, clinic, labs = STAYS[dx]
    language = "zh-CN" if n in (1, 4, 7, 10, 13, 16) else "zh-TW" if n in (19, 22, 25, 28) else "en"
    warfarin = n in (0, 1, 5, 8, 12, 17, 22, 26)
    secondary = sorted(set(rng.sample(["59621000", "44054006", "55822004", "40930008", "35489007"], 2))
                       | ({"49436004"} if warfarin else set()))
    if dx == "195951007":
        secondary = sorted(set(secondary) | {"13645005"})
    home = []
    for code in secondary:
        for rxcui in CHRONIC.get(code, [])[:1]:
            home.append({"rxcui": rxcui, "dose": C.MEDICATIONS[rxcui][2], "per_day": C.MEDICATIONS[rxcui][4]})
    if n % 4 == 0:
        home.append({"rxcui": "7258", "dose": 500, "per_day": 2})  # naproxen at home: stopped at discharge
    inpatient = [{"rxcui": h["rxcui"], "dose": h["dose"], "per_day": h["per_day"]} for h in home if h["rxcui"] != "7258"]
    inpatient += [{"rxcui": rx, "reason": dx} for rx in INPATIENT_MEDS.get(dx, [])[:3]
                  if not any(i["rxcui"] == rx for i in inpatient)]
    discharge = []
    for h in home:
        if h["rxcui"] == "7258":
            continue
        if h["rxcui"] == "11289":
            discharge.append({"rxcui": "11289", "dose": rng.choice((2.5, 3, 4)), "per_day": 1})  # changed
        else:
            discharge.append({"rxcui": h["rxcui"], "dose": h["dose"], "per_day": h["per_day"]})  # continued
    new = {"233604007": "723", "68566005": "7454", "396230008": "723", "91302008": "723", "22298006": "1191",
           "42343007": "4603", "230690007": "1191", "5913000": "161", "65275009": "161", "195951007": "8640"}[dx]
    if not any(d["rxcui"] == new for d in discharge):
        discharge.append({"rxcui": new, "reason": dx})
    follow = [("family_doctor", 7)] + ([(clinic, 14)] if clinic else []) + ([("anticoag_clinic", 3)] if warfarin else [])
    note_drug = {3: "6918", 14: "17767", 24: "29046"}.get(n)
    if note_drug and any(h["rxcui"] == note_drug for h in home + inpatient + discharge):
        note_drug = "703"  # amiodarone: never prescribed in these cases
    pending = "600-7" if dx == "91302008" else "630-4" if dx == "68566005" else None
    if language == "en" and n % 7 == 3:
        pending = pending or "600-7"
    return {
        "id": f"doc-{n + 1:02d}", "title": f"{title}" + (", on warfarin" if warfarin else "")
        + (f", {language}" if language != "en" else "") + (", tempting note" if note_drug else ""),
        "patient": {"age": rng.randint(48, 91), "sex": rng.choice(["female", "male"]), "language": language},
        "dx": dx, "secondary": secondary, "days": rng.randint(2, 7),
        "procedure": {"code": procedure, "day": 1} if procedure else None,
        "home": home, "inpatient": inpatient, "discharge": discharge,
        "vitals": {"admission": vitals(rng, True), "earlier": vitals(rng, False), "latest": vitals(rng, False)},
        "labs": [{"code": c, "values": [a, b]} for c, a, b in labs] + (
            [{"code": "6301-6", "values": [round(rng.uniform(2.8, 3.6), 1), round(rng.uniform(2.0, 2.9), 1)]}]
            if warfarin else []),
        "report": {"kind": report[0], "conclusion": report[1]} if report else None,
        "pending": pending, "task": TASKS[n % len(TASKS)] if n % 3 == 0 else None,
        "flags": ["fall-risk"] if n % 5 == 0 else [], "follow_up": follow, "note_drug": note_drug,
        "expected": {"diagnoses": [dx, *secondary], "procedures": [procedure] if procedure else [],
                     "follow_up": [c for c, _ in follow], "pending": [pending] if pending else [],
                     "language": {"en": "en", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}[language],
                     "warfarin": warfarin, "tempting_drug": C.MEDICATIONS[note_drug][0] if note_drug else None},
    }


def main() -> None:
    rng = random.Random(73)
    cases = [case(n, rng) for n in range(30)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
