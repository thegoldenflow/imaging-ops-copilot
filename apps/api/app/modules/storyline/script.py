"""The hospital storyline's script (spec 8.2): who the patient is, who plays each part, and the twelve steps.

One synthetic patient through the whole hospital: Wang Xiuying, 72, Mandarin-speaking, on warfarin for atrial
fibrillation, falls at home and breaks her hip. Every step is "AI proposes -> a person signs -> audit trail". The
step texts here are what the storyline page and `demo/hospital_journey.md` show: the click path, the one sentence
to say, and what the screen should show. Ids are fixed (`*-j0001`), so tests, the page and the demo script can
name them; times are the hospital clock's and come from the plan when the story runs.

Everything here is fictional (synthetic data only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

PATIENT = "pat-j0001"
MRN = "40720001"
HEALTH_CARD = "6845294821"  # synthetic; the caller gives the last four digits
HEALTH_CARD_VERSION = "AB"
GIVEN, FAMILY = "Xiuying", "Wang"
GIVEN_ZH, FAMILY_ZH = "秀英", "王"
AGE = 72
BIRTH_MONTH_DAY = (3, 8)
PHONE = "+1-416-555-0188"  # 555-01xx: reserved for fiction
DAUGHTER = ("Li", "Chen", "陈丽", "+1-416-555-0172")
LANGUAGE = "zh-CN"

IMAGING_PATIENT = "PT-J0001"  # her record at the imaging centre (a separate system: linked by the preset MPI record)
IMAGING_REFERRER = "R-ED01"
REQUISITIONS = ("REQ-J0001", "REQ-J0002")  # hip X-rays, CT hip

VISIT = "ed-j0001"  # the ED visit (Encounter EMER)
STAY = "stay-j0001"  # the admission (Encounter IMP)
SURGERY = "surg-j0001"  # the OR case (Appointment from the generator's numbering, request orreq-surg-j0001)
JOURNEY = f"journey-{STAY}"
FOLLOWUP_CALL = f"FU-{STAY}"  # her follow-up call (app/modules/voice_services/followup.py `call_id_for`)
SURGEON = "prac-surg-02"  # Dr. Victor Osei, Orthopedics: attending and surgeon
PROCEDURE = "52734007"  # total replacement of hip (the generator's operation for a neck-of-femur fracture)
DIAGNOSIS = "5913000"  # fracture of neck of femur
COMPLAINT = "fall"

# Triage: the vital signs the paramedics hand over (the admission model gives 0.82 for them with CTAS 2)
TRIAGE_VITALS = {"8867-4": 112, "8480-6": 140, "8462-4": 82, "9279-1": 22, "8310-5": 36.8, "59408-5": 91,
                 "72514-3": 7, "9269-2": 15}
SUGGESTED_CTAS = 2
INR_CRITICAL = 3.8
INR_THRESHOLD = 3.5  # the 8.1 stub's preset threshold table: INR at or above this before surgery is critical
# First post-operative night: spot vital signs with the flowsheet's NEWS2 (preset, not computed: 8.1 stub)
#   RR 23 (2) + SpO2 93% on air (2) + SBP 104 (1) + HR 118 (2) + 38.0 °C (0) + alert (0) = 7
NIGHT_VITALS = {"8867-4": 118, "8480-6": 104, "8462-4": 62, "9279-1": 23, "8310-5": 38.0, "59408-5": 93,
                "72514-3": 5, "9269-2": 15}
NEWS2_SCORE = 7

# The people who play the parts (generated hospital staff; ids follow the practitioner ids)
CAST = {
    "clerk": "U-CLER-01",  # registration clerk, and the health-records coder in step 12 (no separate role in 6.3)
    "triage_nurse": "U-NURS-01",  # ED nurse
    "ed_physician": "U-DOC-01",  # ED physician
    "bed_manager": "U-OPS",  # operations manager: bed manager and OR coordinator (no separate roles in 6.3)
    "pharmacist": "U-PHAR-01",
    "surgeon": "U-SURG-02",  # orthopaedic surgeon, attending: co-signs the MedRec, signs the discharge summary
    "ward_nurse": "U-NURS-13",  # Surgery ward nurse (she recovers in a Surgery overflow bed)
    "admin": "U-ADMIN",
}

# What the daughter and the patient say (the storyline page offers them as one-click phrases), each with its English
# translation: fixed text, the subtitles for an English-speaking audience (no model translates them)
CALL_LINES = (("你好，我妈妈在家摔倒了，救护车正在把她送到你们急诊。",
               "Hello, my mother fell at home, and an ambulance is bringing her to your emergency department."),
              ("我是她女儿。她叫王秀英，{birth_zh}出生，健康卡尾号4821。",
               "I'm her daughter. Her name is Wang Xiuying, born {birth_en}, health card ending in 4821."),
              ("没有了，谢谢。", "No, that's all, thank you."))
FOLLOWUP_LINES = (("{birth_zh}", "{birth_en}"),
                  ("还行，就是伤口一直在渗液，纱布都湿透了。", "I'm all right, but the wound keeps leaking; the gauze is soaked through."))
FOLLOWUP_CLOSING = ("没有了。", "No, nothing else.")  # the follow-up's remaining questions, until the call ends

IMAGING_REQUEST = {
    REQUISITIONS[0]: ("XR_HIP", "X-ray left hip and pelvis"),
    REQUISITIONS[1]: ("CT_HIP", "CT left hip without contrast"),
}
IMAGING_FINDINGS = {
    "XR_HIP": "Displaced subcapital fracture of the left femoral neck (Garden IV). No other fracture.",
    "CT_HIP": "Displaced left femoral neck fracture; no acetabular involvement. Moderate osteopenia.",
}
CODING = {"diagnosis": ("S72.0", "Fracture of neck of femur", "ICD-10-CA"),
          "secondary": [("W19", "Unspecified fall", "ICD-10-CA"),
                        ("I48", "Atrial fibrillation and flutter", "ICD-10-CA"),
                        ("Z92.1", "Personal history of long-term (current) use of anticoagulants", "ICD-10-CA")],
          "procedure": ("1.VA.53.^^", "Implantation of internal device, hip joint (rubric; qualifiers by the coder)",
                        "CCI")}
ROADMAP_NOTE = "Roadmap item (8.1): preset data, no real logic; architecture in docs/data-model.md and the registry."


def birth_date(day: date) -> date:
    """She is 72 on the hospital day whatever the date (the admission model sees her age)."""
    month, dom = BIRTH_MONTH_DAY
    year = day.year - AGE - (1 if (day.month, day.day) < (month, dom) else 0)
    return date(year, month, dom)


def birth_zh(day: date) -> str:
    b = birth_date(day)
    return f"{b.year}年{b.month}月{b.day}日"


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def birth_en(day: date) -> str:
    b = birth_date(day)
    return f"{MONTHS[b.month - 1]} {b.day}, {b.year}"


def lines(pairs, day: date) -> list[tuple[str, str]]:
    """The scripted lines with her birth date filled in: (what is said, its English translation)."""
    values = {"birth_zh": birth_zh(day), "birth_en": birth_en(day)}
    return [(text.format(**values), en.format(**values)) for text, en in pairs]


@dataclass(frozen=True)
class Step:
    n: int
    key: str
    title: str
    module: str  # spec section / module
    cast: str  # key in CAST: who does it
    route: str  # where to click (web path)
    click_path: tuple[str, ...]
    say: str
    expect: tuple[str, ...]
    ai: str  # what the AI proposes
    signoff: str  # who signs or approves
    writes: str  # what is written back
    seconds: int  # time budget in the 10-minute demo
    stub: bool = False  # an 8.1 roadmap stub on preset data
    beat: str | None = None  # the clock beat this step waits for (storyline advance)
    roles: tuple[str, ...] = field(default=())  # the roles whose view the step contrasts
    permission: str = ""  # what switching to this role shows (6.3)


STEPS: tuple[Step, ...] = (
    Step(1, "call", "A daughter calls in Mandarin; the clerk confirms the ED registration", "7.4 call centre", "clerk",
         "/call-centre",
         ("Call centre", "Start call", "Say the daughter's two lines (phrases below)", "End call",
          "Requests: ED registration", "Confirm"),
         "Her daughter calls in Mandarin. The call centre answers in Chinese, checks name, birth date and the last "
         "four card digits, and pre-fills the ED registration; the clerk confirms it and the EHR registers the visit.",
         ("Transcript in Chinese with 'Verified'", "Request card: ED registration, Wang Xiuying, MRN 40720001",
          "After Confirm: the visit ed-j0001 (EMER) is registered"),
         "identity check, language switch, the registration pre-filled", "registration clerk",
         "Encounter (EMER), ADT^A01", 60, roles=("clerk",),
         permission="The clerk sees who the patient is and that she is registered, never why: the chart shows the visit without its reason for the clerk's role."),
    Step(2, "triage", "Triage: CTAS 2 suggested, admission probability 0.82 with three reasons", "7.1 triage view",
         "triage_nurse", "/reviews",
         ("Review queue", "Triage suggestion: CTAS 2", "Accept", "Control Tower: her ED row"),
         "The triage assist suggests CTAS 2 from the handover vital signs and the warfarin; the nurse accepts, the EHR "
         "records the triage, and the Control Tower predicts admission at 0.82 with its three main reasons.",
         ("Review card with the CTAS 2 reasons and 'Admission probability 0.82: CTAS 2, SpO₂ 91%, heart rate 112'",
          "Control Tower ED list: her row at CTAS 2 with the 0.82 bar"),
         "CTAS level with reasons; admission probability with three reasons", "triage nurse",
         "Observation (CTAS, vital signs), Flag (fall risk)", 50, roles=("nurse", "operations_manager"),
         permission="The ED nurse sees her because she is in the ED; the bed manager sees the same row as an MRN, with the factor names but not the values."),
    Step(3, "imaging", "Hip X-rays and CT: urgency and protocol suggested, the ED physician orders", "imaging pipeline",
         "ed_physician", "/reviews",
         ("Review queue", "Imaging orders: hip X-rays and CT hip", "Accept"),
         "The ED physician's request goes through the imaging centre's own pipeline: urgency P2 with its reason and "
         "a protocol for each exam from the library; accepting places both orders in the EHR.",
         ("Card: P2 'urgent', protocols XR-HIP-2V and CT-HIP-FX", "Orders placed: two ServiceRequests (asap)"),
         "urgency (P1-P4) and protocol per exam", "ED physician", "ServiceRequest (x2), ORM^O01", 45,
         roles=("physician",),
         permission="Hospital physicians have no imaging-centre screens: the ED physician decides on the card; the requisitions also sit in the imaging centre's queue, confirmed."),
    Step(4, "inr", "INR 3.8: a critical value routed to the ED physician", "8.1 stub: critical results", "ed_physician",
         "/reviews",
         ("Advance the clock to the INR result", "Review queue", "Critical value: INR 3.8", "Acknowledge"),
         "The INR comes back at 3.8 before surgery: the critical-value router, a roadmap stub on a preset threshold, "
         "sends it to the ED physician, who acknowledges it.",
         ("Card: 'Critical value: INR 3.8 (preset threshold ≥ 3.5)' with the Roadmap badge",),
         "route the critical value (preset rule, roadmap)", "ED physician", "Task, Observation", 35, stub=True,
         beat="inr",
         permission="Only a physician may acknowledge the critical value; a nurse's or clerk's queue does not show it."),
    Step(5, "bed", "Orthopedics is full; Surgery has 2 discharge-ready patients; the bed manager approves", "7.1 Control Tower",
         "bed_manager", "/control-tower",
         ("Advance the clock to the admission decision", "Control Tower", "Exception: Orthopedics at 100% occupancy",
          "Action drawer: early discharge rounds on Surgery", "Approve"),
         "She needs an orthopaedic bed and there is none. The rule engine opens the exception, the AI writes the "
         "narrative from the engine's facts and recommends early rounds on Surgery's two discharge-ready patients; "
         "the bed manager approves, and the first bed they free is hers.",
         ("Exception card 'Orthopedics at 100% occupancy' (high)", "Action: early discharge rounds on Surgery (2)",
          "After the approval: Tasks for the owner roles; then Encounter.location = a Surgery bed"),
         "the exception's narrative and actions", "bed manager", "Task (flow-action), Encounter.location", 65,
         beat="decision", roles=("operations_manager", "nurse"),
         permission="The bed manager decides for the whole hospital but sees patients as MRN and bed only ('Hidden for your role'); the Surgery nurse sees the clinical card of the same bed."),
    Step(6, "medrec", "Admission MedRec: warfarin + naproxen (major), amlodipine left out", "7.2 medication safety",
         "pharmacist", "/med-safety",
         ("Advance the clock until she has a bed (the Surgery bed the early discharges freed)", "Medication safety",
          "Her naproxen order: major interaction with warfarin", "Request a change",
          "Admission reconciliation", "Handle each discrepancy", "Sign", "As the surgeon: co-sign"),
         "The rules catch the warfarin and naproxen interaction at once and the reconciliation finds her home "
         "amlodipine missing; the pharmacist decides each item, signs, and the surgeon co-signs.",
         ("Finding: warfarin + naproxen, major", "MedRec rows: warfarin held, amlodipine omission",
          "Record medrec-stay-j0001-admission final"),
         "interaction and discrepancies with explanations", "pharmacist, surgeon co-signs",
         "DocumentReference (MedRec, final)", 65, beat="admitted", roles=("pharmacist", "physician"),
         permission="The pharmacist reads medications, allergies, laboratory results and diagnoses, not the notes; only the pharmacist handles discrepancies, and a physician of her care team co-signs."),
    Step(7, "or", "OR: predicted 115 min against 90 booked, past the block; the coordinator adjusts", "7.1 OR board",
         "bed_manager", "/control-tower",
         ("Control Tower", "OR list: her case", "Adjust booking to the prediction"),
         "The surgical-duration model predicts 115 minutes for her case, booked for 90: it would run past the 16:00 "
         "block end. The OR coordinator adjusts the booking.",
         ("OR row: booked 90, predicted 115, block overtime risk", "After Adjust: booked 115"),
         "case duration with its three reasons", "OR coordinator", "Appointment", 40, roles=("operations_manager",),
         permission="Only the OR coordinator (operations manager) may change a booked case; the gateway refuses anyone else and audits it."),
    Step(8, "news2", "First night after surgery: NEWS2 7 raises a flag", "8.1 stub: deterioration alert",
         "ward_nurse", "/reviews",
         ("Advance the clock to the first post-operative night", "Review queue", "NEWS2 7", "Record the response"),
         "At 02:00 her score is 7: the deterioration alert, a roadmap stub on the preset score, raises a NEWS2 flag "
         "and the ward nurse responds.",
         ("Flag NEWS2 on her stay", "Nurse task 'NEWS2 7' with the Roadmap badge"),
         "raise the NEWS2 flag (preset score, roadmap)", "ward nurse", "Flag, Task", 35, stub=True, beat="night",
         permission="The Surgery ward nurse sees her because her bed is on the nurse's unit; a nurse of another unit would need break-glass."),
    Step(9, "summary", "Discharge summary: the validator blocks an untraceable medicine", "7.3 documentation",
         "surgeon", "/documentation",
         ("Advance the clock to the discharge", "Documentation", "Her stay", "Draft with AI",
          "Red item: lisinopril, no source", "Remove", "Sign"),
         "The draft cites a source for every line. One medicine came from a note, not from an order, so the "
         "validator marks it red and the signature is blocked until the surgeon removes it.",
         ("Red item 'lisinopril' with 'no matching source'", "Sign enabled after the removal", "dsum-stay-j0001 final"),
         "the summary with sources", "orthopaedic surgeon", "DocumentReference (final)", 70, beat="discharge",
         roles=("physician",),
         permission="The surgeon may sign because he is her attending; another unit's physician would be asked for break-glass first."),
    Step(10, "instructions", "Bilingual instructions: warfarin diet and wound red flags", "7.3 documentation",
         "ward_nurse", "/documentation",
         ("Documentation", "Her patient instructions (Chinese)", "Check the medicines, diet and red flags", "Sign"),
         "From the signed summary: the same content in Chinese and English, with warfarin's steady greens and the "
         "wound red flags; the nurse signs.",
         ("Instructions in 简体中文 by default", "Warfarin: 每周吃的绿叶蔬菜量要大致相同", "Wound red flags"),
         "patient-language instructions", "ward nurse", "DocumentReference (final)", 45, roles=("nurse",),
         permission="A nurse signs the patient instructions, not a physician; the default language follows her record (Simplified Chinese)."),
    Step(11, "followup", "48 hours later: a Chinese follow-up call escalates heavy wound drainage", "7.4 follow-up",
         "ward_nurse", f"/follow-up?call={FOLLOWUP_CALL}",  # her call, on the Calls tab (not yet in the Queue)
         ("Advance the clock 48 hours", f"Follow-up calls, Calls tab: her call (MRN {MRN})", "Place call",
          "Answer as the patient", "Queue tab: her escalation on top", "Call back"),
         "Two days after discharge the AI calls her in Mandarin with questions from her signed instructions. She "
         "says the wound keeps leaking: the rule layer escalates at once, and the nurse calls her back.",
         ("Red flag: heavy wound drainage, with her words", "Task followup-escalation (urgent)",
          "Nurse call-back recorded"),
         "the call in her language; red-flag escalation (rules first)", "ward nurse",
         "Communication, Observation (patient-reported), Task", 60, beat="followup", roles=("nurse",),
         permission="Escalations go to the nurses of her last unit; the call never gives medical advice."),
    Step(12, "coding", "Coding suggestion: main diagnosis and procedure codes", "8.1 stub: coding assistance", "clerk",
         "/reviews",
         ("Review queue", "Coding suggestion", "Accept"),
         "From the signed summary, the coding assistant, a roadmap stub, suggests the main diagnosis and procedure "
         "codes; the coder accepts.",
         ("Card: S72.0 fracture of neck of femur; CCI 1.VA.53 with the Roadmap badge",),
         "ICD-10-CA and CCI codes (preset, roadmap)", "clerk (as coder)", "card only", 25, stub=True,
         roles=("clerk",),
         permission="The clerk sees the coding card and her visits but not the clinical documents themselves."),
)
BY_KEY = {s.key: s for s in STEPS}
TOTAL_SECONDS = sum(s.seconds for s in STEPS)
assert TOTAL_SECONDS <= 600, TOTAL_SECONDS


def day_of(now: datetime) -> date:
    return now.date()
