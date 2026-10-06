"""Builds the evaluation sets in evals/datasets (deterministic, synthetic).

    uv run python -m app.modules.evals.build
"""

import json
import random
from datetime import date

from app.core.models import Patient, Referrer
from app.modules.evals.paths import DATASETS
from app.modules.protocols.library import BY_ID
from app.modules.requisitions.generator import CASES, generate

SEED = 2026

# Patients' own words about implants, in four languages, with expected device categories.
IMPLANT_ITEMS = [
    ("en", "I have a pacemaker since 2019", ["cardiac_device"]),
    ("en", "Defibrillator in my chest (ICD)", ["cardiac_device"]),
    ("en", "Cochlear implant in my right ear", ["ear_implant"]),
    ("en", "They put a clip on an aneurysm in my brain in 2010", ["neuro_clip"]),
    ("en", "spinal cord stimulator for back pain", ["neurostimulator"]),
    ("en", "I wear an insulin pump", ["drug_pump"]),
    ("en", "I used to be a welder and got metal in my eye once", ["foreign_body"]),
    ("en", "Left hip replacement and a stent in my heart", ["orthopedic", "stent"]),
    ("en", "I had bypass surgery, they closed my chest with wires", ["surgical_hardware"]),
    ("en", "VP shunt since childhood", ["shunt"]),
    ("en", "Copper IUD", ["gynecologic"]),
    ("en", "Nothing metal, just fillings in my teeth", []),
    ("fr", "J'ai un stimulateur cardiaque", ["cardiac_device"]),
    ("fr", "Implant cochléaire à gauche", ["ear_implant"]),
    ("fr", "Prothèse de hanche droite", ["orthopedic"]),
    ("fr", "Un stérilet en cuivre", ["gynecologic"]),
    ("fr", "Aucun implant", []),
    ("zh", "我装了心脏起搏器", ["cardiac_device"]),
    ("zh", "右耳有人工耳蜗", ["ear_implant"]),
    ("zh", "十年前做过心脏搭桥，胸口有钢丝", ["surgical_hardware"]),
    ("zh", "膝关节置换和一个心脏支架", ["orthopedic", "stent"]),
    ("zh", "以前做焊工，眼睛里进过铁屑", ["foreign_body"]),
    ("zh", "身上用胰岛素泵", ["drug_pump"]),
    ("zh", "没有任何金属", []),
    ("pa", "ਮੇਰੇ ਪੇਸਮੇਕਰ ਲੱਗਿਆ ਹੋਇਆ ਹੈ", ["cardiac_device"]),
    ("pa", "ਕੋਕਲੀਅਰ ਇਮਪਲਾਂਟ ਸੱਜੇ ਕੰਨ ਵਿੱਚ", ["ear_implant"]),
    ("pa", "ਕੁੱਲ੍ਹੇ ਦੀ ਬਦਲੀ ਹੋਈ ਹੈ", ["orthopedic"]),
    ("pa", "ਅੱਖ ਵਿੱਚ ਧਾਤ ਦੇ ਟੁਕੜੇ ਗਏ ਸਨ", ["foreign_body"]),
    ("pa", "ਦਿਮਾਗ ਵਿੱਚ ਐਨਿਉਰਿਜ਼ਮ ਕਲਿੱਪ", ["neuro_clip"]),
    ("pa", "ਕੋਈ ਧਾਤ ਨਹੀਂ", []),
]

GIVEN = ["Ana", "Ben", "Chen", "Dana", "Eli", "Farah", "Gus", "Hira", "Ivan", "Jia"]
FAMILY = ["Lopez", "Nguyen", "Okoye", "Petrov", "Quinn", "Rossi", "Sato", "Tan"]


def _patient(rng: random.Random, i: int) -> Patient:
    given, family = rng.choice(GIVEN), rng.choice(FAMILY)
    return Patient(id=f"EVAL-{i}", given_name=given, family_name=family,
                   dob=date(rng.randint(1945, 2000), rng.randint(1, 12), rng.randint(1, 28)), sex=rng.choice("FM"),
                   phone=f"+1-416-555-01{rng.randint(0, 99):02d}", email=f"{given.lower()}@example.com",
                   address="1 Example St, Demo City, ON", health_card="".join(str(rng.randint(0, 9)) for _ in range(10)),
                   health_card_version="ZZ", preferred_language="en")


def build() -> None:
    rng = random.Random(SEED)
    DATASETS.mkdir(parents=True, exist_ok=True)
    referrer = Referrer(id="EVAL-R", name="Dr. Test Referrer", specialty="Family Medicine", clinic="Eval Clinic",
                        phone="+1-416-555-0100", fax="+1-416-555-0101", is_key=False)

    requisitions = []
    for i in range(50):
        case = CASES[i % len(CASES)]  # every case appears at least twice
        text, labels = generate(rng, _patient(rng, i), referrer, case=case)
        requisitions.append({"id": f"req-{i:02d}", "text": text, "labels": labels.to_dict()})
    (DATASETS / "requisitions.json").write_text(json.dumps(requisitions, indent=1, ensure_ascii=False))

    protocols = []
    for i in range(40):
        case = CASES[i % len(CASES)]
        protocols.append({"id": f"pro-{i:02d}", "requested_exam": rng.choice(case.exam_texts),
                          "clinical": rng.choice(case.indications), "protocol_id": case.protocol_id,
                          "protocol_name": BY_ID[case.protocol_id].name})
    (DATASETS / "protocols.json").write_text(json.dumps(protocols, indent=1, ensure_ascii=False))

    implants = [{"id": f"imp-{i:02d}", "language": lang, "text": text, "categories": cats}
                for i, (lang, text, cats) in enumerate(IMPLANT_ITEMS)]
    (DATASETS / "implants.json").write_text(json.dumps(implants, indent=1, ensure_ascii=False))
    print(f"Wrote {len(requisitions)} requisitions, {len(protocols)} protocol cases, {len(implants)} implant answers to {DATASETS}")


if __name__ == "__main__":
    build()
