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

# Patient feedback (rating, comment) with expected sentiment and themes; 15 per language.
FEEDBACK_ITEMS = [
    ("en", 5, "Everyone was friendly and the scan started on time.", "positive", ["staff_attitude", "wait_time"]),
    ("en", 1, "Waited 90 minutes past my appointment. Terrible.", "negative", ["wait_time"]),
    ("en", 2, "The receptionist was rude and did not listen.", "negative", ["staff_attitude"]),
    ("en", 4, "Clean and comfortable. Thank you.", "positive", ["environment"]),
    ("en", 2, "I was charged for parking validation and the bill was wrong.", "negative", ["billing", "parking_access"]),
    ("en", 3, "It was okay.", "neutral", []),
    ("en", 5, "The technologist explained everything clearly, great experience.", "positive", ["staff_attitude", "communication"]),
    ("en", 1, "No one could tell me when my results would be ready.", "negative", ["results", "communication"]),
    ("en", 4, "Booking an appointment online was quick.", "positive", ["scheduling", "wait_time"]),
    ("en", 2, "The waiting room was cold and dirty.", "negative", ["environment"]),
    ("en", 3, "Hard to find the entrance but staff were helpful.", "neutral", ["parking_access", "staff_attitude"]),
    ("en", 1, "They rescheduled me three times without calling.", "negative", ["scheduling", "communication"]),
    ("en", 5, "Fast, friendly, easy. Thanks!", "positive", ["staff_attitude", "wait_time"]),
    ("en", 2, "Insurance was billed twice for one exam.", "negative", ["billing"]),
    ("en", 4, "No complaints.", "positive", []),
    ("fr", 5, "Personnel très gentil et examen rapide.", "positive", ["staff_attitude", "wait_time"]),
    ("fr", 1, "Attente de deux heures, aucune explication.", "negative", ["wait_time", "communication"]),
    ("fr", 2, "La facture était fausse et personne ne répond.", "negative", ["billing"]),
    ("fr", 4, "Salle d'attente propre et calme.", "positive", ["environment"]),
    ("fr", 3, "Correct dans l'ensemble.", "neutral", []),
    ("fr", 2, "Pas de stationnement disponible.", "negative", ["parking_access"]),
    ("fr", 5, "Le technologue m'a bien expliqué l'examen, merci.", "positive", ["staff_attitude", "communication"]),
    ("fr", 1, "La réceptionniste était impolie.", "negative", ["staff_attitude"]),
    ("fr", 4, "Rendez-vous facile à réserver.", "positive", ["scheduling"]),
    ("fr", 2, "Les consignes n'étaient pas en français.", "negative", ["communication"]),
    ("fr", 3, "Résultats arrivés chez mon médecin, mais un peu lents.", "neutral", ["results"]),
    ("fr", 5, "Excellent service, à l'heure.", "positive", ["wait_time"]),
    ("fr", 2, "Il faisait froid dans la salle.", "negative", ["environment"]),
    ("fr", 1, "Mon rendez-vous a été reporté sans prévenir.", "negative", ["scheduling"]),
    ("fr", 4, "Ascenseur et accès en fauteuil faciles.", "positive", ["parking_access"]),
    ("zh", 5, "工作人员态度很好，很快就做完了。", "positive", ["staff_attitude", "wait_time"]),
    ("zh", 1, "等了两个小时，太久了。", "negative", ["wait_time"]),
    ("zh", 2, "前台不耐烦，态度差。", "negative", ["staff_attitude"]),
    ("zh", 4, "环境干净舒适。", "positive", ["environment"]),
    ("zh", 2, "收费不清楚，账单有错。", "negative", ["billing"]),
    ("zh", 3, "一般吧。", "neutral", []),
    ("zh", 5, "有中文须知，技师解释得很清楚，谢谢。", "positive", ["communication", "staff_attitude"]),
    ("zh", 1, "一个星期了医生还没收到报告。", "negative", ["results"]),
    ("zh", 4, "网上预约很方便。", "positive", ["scheduling"]),
    ("zh", 2, "停车位太少，找了半天。", "negative", ["parking_access"]),
    ("zh", 3, "检查还行，就是候诊室有点冷。", "neutral", ["environment"]),
    ("zh", 1, "改期了两次也没人通知我。", "negative", ["scheduling", "communication"]),
    ("zh", 5, "准时，护士很热情。", "positive", ["wait_time", "staff_attitude"]),
    ("zh", 2, "保险没有直接付款，要自己先付。", "negative", ["billing"]),
    ("zh", 4, "整体满意。", "positive", []),
    ("pa", 5, "ਸਟਾਫ ਬਹੁਤ ਚੰਗਾ ਸੀ ਅਤੇ ਜਲਦੀ ਹੋ ਗਿਆ।", "positive", ["staff_attitude", "wait_time"]),
    ("pa", 1, "ਦੋ ਘੰਟੇ ਉਡੀਕ ਕਰਨੀ ਪਈ।", "negative", ["wait_time"]),
    ("pa", 2, "ਰਿਸੈਪਸ਼ਨ ਤੇ ਰੁੱਖਾ ਵਿਹਾਰ।", "negative", ["staff_attitude"]),
    ("pa", 4, "ਕਮਰਾ ਸਾਫ਼ ਸੀ।", "positive", ["environment"]),
    ("pa", 2, "ਬਿੱਲ ਗਲਤ ਸੀ।", "negative", ["billing"]),
    ("pa", 3, "ਠੀਕ ਸੀ।", "neutral", []),
    ("pa", 5, "ਹਦਾਇਤਾਂ ਪੰਜਾਬੀ ਵਿੱਚ ਸਨ, ਧੰਨਵਾਦ।", "positive", ["communication"]),
    ("pa", 1, "ਮੇਰੇ ਡਾਕਟਰ ਨੂੰ ਰਿਪੋਰਟ ਨਹੀਂ ਮਿਲੀ।", "negative", ["results"]),
    ("pa", 4, "ਬੁਕਿੰਗ ਆਸਾਨ ਸੀ।", "positive", ["scheduling"]),
    ("pa", 2, "ਪਾਰਕਿੰਗ ਨਹੀਂ ਮਿਲੀ।", "negative", ["parking_access"]),
    ("pa", 3, "ਸਕੈਨ ਠੀਕ ਸੀ ਪਰ ਕਮਰੇ ਵਿੱਚ ਠੰਡ ਸੀ।", "neutral", ["environment"]),
    ("pa", 1, "ਅਪੌਇੰਟਮੈਂਟ ਬਦਲ ਦਿੱਤੀ ਅਤੇ ਕਿਸੇ ਨੇ ਨਹੀਂ ਦੱਸਿਆ।", "negative", ["scheduling", "communication"]),
    ("pa", 5, "ਟੈਕਨੋਲੋਜਿਸਟ ਨੇ ਸਭ ਕੁਝ ਸਮਝਾਇਆ।", "positive", ["staff_attitude", "communication"]),
    ("pa", 2, "ਬੀਮਾ ਨੇ ਪੈਸੇ ਨਹੀਂ ਦਿੱਤੇ, ਫੀਸ ਆਪ ਭਰਨੀ ਪਈ।", "negative", ["billing"]),
    ("pa", 4, "ਵ੍ਹੀਲਚੇਅਰ ਲਈ ਲਿਫਟ ਸੀ, ਵਧੀਆ।", "positive", ["parking_access"]),
]

# Policy questions: expected policy document, or None when the documents do not cover it.
POLICY_QUESTIONS = [
    ("How long must an outpatient stay after a contrast injection?", "POL-CONTRAST"),
    ("Which patients need an eGFR before contrast?", "POL-CONTRAST"),
    ("What do we do if contrast leaks into the tissue (extravasation)?", "POL-CONTRAST"),
    ("Who has to review the request when the eGFR is below 30?", "POL-CONTRAST"),
    ("Can a visitor go into the magnet room without screening?", "POL-MRI"),
    ("What is needed before MRI for someone who had metal in the eyes?", "POL-MRI"),
    ("Can I look up my own test results in the work system?", "POL-PRIVACY"),
    ("How quickly must a suspected privacy breach be reported?", "POL-PRIVACY"),
    ("Within what time must a level 1 critical finding be phoned to the ordering physician?", "POL-CRITICAL"),
    ("What happens if the ordering physician cannot be reached about a critical result?", "POL-CRITICAL"),
    ("How soon must a CT dose exceedance be reviewed?", "POL-RADIATION"),
    ("How often are staff dosimeters read?", "POL-RADIATION"),
    ("Which two identifiers confirm a patient's identity?", "POL-ID"),
    ("How are endocavity ultrasound probes disinfected?", "POL-INFECTION"),
    ("What percentage of signed reports is sampled for peer review?", "POL-QA"),
    ("How often does a scanner need preventive maintenance?", "POL-EQUIPMENT"),
    ("What is the fee for parking validation?", None),
    ("How many vacation days do technologists get?", None),
    ("What is the dress code for front desk staff?", None),
    ("Which courier do we use for film couriers to hospitals?", None),
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
    feedback = [{"id": f"fb-{i:02d}", "language": lang, "rating": rating, "comment": text, "sentiment": sent, "themes": themes}
                for i, (lang, rating, text, sent, themes) in enumerate(FEEDBACK_ITEMS)]
    (DATASETS / "feedback.json").write_text(json.dumps(feedback, indent=1, ensure_ascii=False))
    print(f"Wrote {len(feedback)} feedback comments")
    policy = [{"id": f"pol-{i:02d}", "question": q, "expected_doc": doc} for i, (q, doc) in enumerate(POLICY_QUESTIONS)]
    (DATASETS / "policy_qa.json").write_text(json.dumps(policy, indent=1, ensure_ascii=False))
    print(f"Wrote {len(policy)} policy questions")
    print(f"Wrote {len(requisitions)} requisitions, {len(protocols)} protocol cases, {len(implants)} implant answers to {DATASETS}")


if __name__ == "__main__":
    build()
