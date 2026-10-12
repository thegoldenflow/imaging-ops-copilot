"""Write the voice-services eval sets (spec 7.4): authored synthetic call scripts, no real calls or patients.

    cd apps/api && uv run python scripts/gen_voice_eval.py

- evals/call_center/cases.jsonl: 40 call-centre scripts (the six intents, English and Chinese; 6 with a failed
  identity check twice). `caller` names a planted demo caller (app/ehr/seed/voice.py); in `turns`, `{id}` / `{id_zh}`
  stand for the caller's identity line (name, birth date, last four card digits) and `{bad_id}` / `{bad_id_zh}` for
  one with a wrong birth date.
- evals/followup/cases.jsonl: 50 follow-up call scripts, 15 with a red flag (and the 7.4 rule codes they should
  hit), on the planted English (`en`) or Chinese (`zh`) follow-up patient. `answers` are keyed by question kind
  (open = "how have you been feeling", flag, med, med_problems, followup, wound, care, other) or `first_flag`;
  unanswered questions get a plain no / yes.
- evals/voice_bilingual/cases.jsonl: 10 scenarios, each in English and in Chinese, through the whole flow.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.evals.paths import EVALS_DIR  # noqa: E402

CALL_CENTER = [
    # id, caller, language, turns, intent, category
    ("cc-01", "en_fracture", "en", ["I need to move my fracture clinic appointment to another day", "{id}",
                                    "the first one", "yes please", "no, that's all"], "reschedule", "intent"),
    ("cc-02", "en_heart", "en", ["Can I reschedule my appointment?", "{bad_id}", "{bad_id}"], "reschedule",
     "identity_failure"),
    ("cc-03", "en_fracture", "en", ["Hi, I can't make the day of my CT scan, can we change the date?", "{id}",
                                    "the second", "yes"], "reschedule", "intent"),
    ("cc-04", "en_fracture", "en", ["{id} I'd like a different time for my fracture clinic visit.", "the third one",
                                    "yes"], "reschedule", "intent"),
    ("cc-05", "en_heart", "en", ["I want to postpone my heart clinic appointment", "{id}", "first", "yes"],
     "reschedule", "intent"),
    ("cc-06", "zh_anticoag", "zh", ["我想改一下预约时间", "{id_zh}", "第一个", "第一个", "好的"], "reschedule", "intent"),
    ("cc-07", "zh_surgery", "zh", ["外科门诊那天我去不了，能不能改到别的时间？", "{id_zh}", "第二个", "可以"], "reschedule",
     "intent"),
    ("cc-08", "en_heart", "en", ["I'd like to cancel my appointment please", "{id}", "yes"], "cancel", "intent"),
    ("cc-09", "en_fracture", "en", ["{id} Please cancel my CT scan.", "yes"], "cancel", "intent"),
    ("cc-10", "en_fracture", "en", ["I won't be able to come to the fracture clinic, cancel it", "{id}", "yes"],
     "cancel", "intent"),
    ("cc-11", "en_heart", "en", ["Cancel my heart clinic visit", "{bad_id}", "{bad_id}"], "cancel",
     "identity_failure"),
    ("cc-12", "zh_anticoag", "zh", ["我要取消预约", "{id_zh}", "第二个", "对"], "cancel", "intent"),
    ("cc-13", "zh_tw_lung", "zh", ["我想取消門診預約", "{bad_id_zh}", "{bad_id_zh}"], "cancel", "identity_failure"),
    ("cc-14", "en_fracture", "en", ["When is my next appointment?", "{id}", "that's all"], "lookup", "intent"),
    ("cc-15", "en_heart", "en", ["Can you check my appointment time?", "{bad_id}", "{bad_id}"], "lookup",
     "identity_failure"),
    ("cc-16", "en_none", "en", ["Do I have any appointments coming up?", "{id}"], "lookup", "intent"),
    ("cc-17", "en_fracture", "en", ["{id} What time is my appointment?"], "lookup", "intent"),
    ("cc-18", "zh_surgery", "zh", ["我想查一下我的预约", "{id_zh}"], "lookup", "intent"),
    ("cc-19", "zh_anticoag", "zh", ["请问我的预约是什么时候？", "{id_zh}"], "lookup", "intent"),
    ("cc-20", None, "en", ["What are your visiting hours?"], "faq", "intent"),
    ("cc-21", None, "en", ["Where do I park?"], "faq", "intent"),
    ("cc-22", None, "en", ["How do I get to the hospital by subway?"], "faq", "intent"),
    ("cc-23", None, "en", ["Which floor is the cardiology clinic on?"], "faq", "intent"),
    ("cc-24", None, "zh", ["探视时间是几点到几点？"], "faq", "intent"),
    ("cc-25", None, "zh", ["医院附近哪里可以停车？"], "faq", "intent"),
    ("cc-26", None, "zh", ["請問加護病房的探視時間？"], "faq", "intent"),
    ("cc-27", "en_fracture", "en", ["How should I prepare for my CT scan?", "{id}"], "prep", "intent"),
    ("cc-28", "en_heart", "en", ["What should I bring to my appointment?", "{id}"], "prep", "intent"),
    ("cc-29", "zh_anticoag", "zh", ["抗凝门诊前需要准备什么？", "{bad_id_zh}", "{bad_id_zh}"], "prep",
     "identity_failure"),
    ("cc-30", "zh_surgery", "zh", ["做超声检查要空腹吗？", "{id_zh}"], "prep", "intent"),
    ("cc-31", "en_fracture", "en", ["{id} Can I eat before my scan?"], "prep", "intent"),
    ("cc-32", "en_heart", "en", ["My ankles are swollen, is that normal?", "{id}"], "clinical", "intent"),
    ("cc-33", "en_fracture", "en", ["Should I stop my blood thinner before the CT?", "{id}"], "clinical", "intent"),
    ("cc-34", "zh_anticoag", "zh", ["我最近头晕，药要不要减量？", "{id_zh}"], "clinical", "intent"),
    ("cc-35", "zh_tw_lung", "zh", ["我咳嗽一直沒好，要不要緊？", "{id_zh}"], "clinical", "intent"),
    ("cc-36", "en_heart", "en", ["I got my test results, what do they mean?", "{bad_id}", "{bad_id}"], "clinical",
     "identity_failure"),
    ("cc-37", "en_fracture", "en", ["Is it safe to take ibuprofen with my pills?", "{id}"], "clinical", "intent"),
    ("cc-38", None, "en", ["I want to speak to a real person"], "transfer", "intent"),
    ("cc-39", None, "zh", ["请帮我转人工"], "transfer", "intent"),
    ("cc-40", None, "en", ["Operator please"], "transfer", "intent"),
]

FOLLOWUP = [
    # id, patient, red flag?, rule codes the rules should hit, answers, note
    ("fu-01", "en", True, ["fever"], {"open": "My temperature was 38.9 last night."}, "fever reading"),
    ("fu-02", "en", True, ["chest_pain"], {"open": "I've had some chest pain since yesterday."}, "emergency"),
    ("fu-03", "en", True, ["short_breath"], {"open": "I get short of breath just walking to the bathroom."},
     "emergency"),
    ("fu-04", "en", True, ["wound_drainage"], {"open": "I'm okay.",
                                               "other": "Also my wound keeps oozing a lot, the dressing is soaked."},
     "heavy drainage, told at the end"),
    ("fu-05", "en", True, ["fall"], {"other": "I fell in the bathroom on Tuesday."}, "a fall"),
    ("fu-06", "en", True, ["cannot_eat_drink"], {"open": "I can't keep anything down, I throw up everything."},
     "cannot eat or drink"),
    ("fu-07", "en", True, ["altered_consciousness"], {"open": "My daughter says I've been confused since this morning."},
     "altered consciousness"),
    ("fu-08", "en", True, ["wants_person"], {"other": "Yes, I want to talk to a nurse please."}, "asks for a person"),
    ("fu-09", "en", True, ["heavy_bleeding"], {"open": "There's bleeding from my nose that won't stop."},
     "heavy bleeding"),
    ("fu-10", "zh", True, ["wound_drainage"], {"open": "伤口一直在渗液，纱布都湿透了"}, "the demo case"),
    ("fu-11", "zh", True, ["fever"], {"open": "我昨天发烧到三十八度八"}, "fever in Chinese numerals"),
    ("fu-12", "zh", True, ["chest_pain"], {"open": "我胸口有点闷，有时候疼"}, "chest tightness"),
    ("fu-13", "zh", True, ["fall"], {"open": "还好", "other": "我前天在家里摔倒了"}, "a fall, told at the end"),
    ("fu-14", "zh", True, ["cannot_eat_drink"], {"open": "这两天吃不下东西，喝水也吐"}, "cannot eat or drink"),
    ("fu-15", "zh", True, ["@first_flag"], {"first_flag": "有，有一点"}, "yes to their own first warning sign"),
    # no red flag: all clear
    ("fu-16", "en", False, [], {"open": "I'm doing well, thank you."}, "all clear"),
    ("fu-17", "en", False, [], {"open": "Getting better every day."}, "all clear"),
    ("fu-18", "en", False, [], {"open": "Pretty good, just a bit tired."}, "all clear, tired"),
    ("fu-19", "en", False, [], {"open": "Fine.", "other": "No, that's all, thanks."}, "all clear"),
    ("fu-20", "en", False, [], {"open": "Much better than in hospital."}, "all clear"),
    ("fu-21", "en", False, [], {"open": "I feel good.", "followup": "Yes, my son will drive me."}, "all clear"),
    ("fu-22", "en", False, [], {"open": "All good here."}, "all clear"),
    ("fu-23", "en", False, [], {"open": "Not bad at all."}, "all clear"),
    ("fu-24", "en", False, [], {"open": "I'm recovering well."}, "all clear"),
    ("fu-25", "zh", False, [], {"open": "挺好的，谢谢"}, "all clear"),
    ("fu-26", "zh", False, [], {"open": "还不错，一天比一天好"}, "all clear"),
    ("fu-27", "zh", False, [], {"open": "都挺好的"}, "all clear"),
    ("fu-28", "zh", False, [], {"open": "还可以，就是有点累"}, "all clear, tired"),
    ("fu-29", "zh", False, [], {"open": "没什么问题"}, "all clear"),
    ("fu-30", "zh", False, [], {"open": "好多了", "other": "没有了，谢谢"}, "all clear"),
    ("fu-31", "zh", False, [], {"open": "挺好", "followup": "知道，我女儿会陪我去"}, "all clear"),
    ("fu-32", "zh", False, [], {"open": "身体还行"}, "all clear"),
    # no red flag: near misses the rules must not take for one
    ("fu-33", "en", False, [], {"open": "My temperature was 37.6, I feel fine."}, "low reading"),
    ("fu-34", "en", False, [], {"open": "No chest pain, no breathing problems, all fine."}, "negations"),
    ("fu-35", "en", False, [], {"open": "I'm okay.", "other": "There is a little clear fluid on the dressing, less "
                                                             "than before."}, "light drainage"),
    ("fu-36", "en", False, [], {"open": "Good.", "other": "No, I haven't fallen, I use the walker."}, "negated fall"),
    ("fu-37", "zh", False, [], {"open": "体温37度2，没什么不舒服"}, "low reading"),
    ("fu-38", "zh", False, [], {"open": "伤口有一点点渗液，比前两天少了"}, "light drainage"),
    ("fu-39", "zh", False, [], {"open": "没有胸痛，也没有喘不过气"}, "negations"),
    ("fu-40", "zh", False, [], {"open": "挺好，没摔倒"}, "negated fall"),
    # no red flag: concerns the classifier may escalate (over-escalation is recorded, not gated)
    ("fu-41", "en", False, [], {"open": "A bit dizzy when I stand up, otherwise okay."}, "dizziness"),
    ("fu-42", "en", False, [], {"open": "Fine.", "med": "No, I forgot it yesterday."}, "a missed medicine"),
    ("fu-43", "en", False, [], {"open": "Okay.", "med_problems": "Yes, I ran out of one of them."}, "ran out"),
    ("fu-44", "en", False, [], {"open": "Good.", "other": "Should I keep taking the antibiotic after the box is "
                                                         "finished?"}, "a question for the nurse"),
    ("fu-45", "en", False, [], {"open": "Good.", "followup": "No, I didn't know about it."},
     "did not know the follow-up"),
    ("fu-46", "zh", False, [], {"open": "有点头晕，别的还好"}, "dizziness"),
    ("fu-47", "zh", False, [], {"open": "还行", "med": "没有，昨天忘了吃"}, "a missed medicine"),
    ("fu-48", "zh", False, [], {"open": "还好", "other": "药吃完了还要不要继续吃？"}, "a question for the nurse"),
    ("fu-49", "zh", False, [], {"open": "腿有点肿"}, "swelling"),
    ("fu-50", "zh", False, [], {"open": "挺好", "followup": "不知道"}, "did not know the follow-up"),
]

BILINGUAL = [
    # scenario, kind, expected outcome; the English and the Chinese script
    ("faq", "call", {"intent": "faq", "outcome": "resolved"},
     [None, ["What are the visiting hours?", "That's all, thanks"]],
     [None, ["探视时间是几点？", "没有了，谢谢"]]),
    ("lookup", "call", {"intent": "lookup"},
     ["en_fracture", ["When is my next appointment?", "{id}", "No, that's all"]],
     ["zh_anticoag", ["我想查一下我的预约", "{id_zh}", "没有了"]]),
    ("prep", "call", {"intent": "prep", "action": "prep_given"},
     ["en_fracture", ["How should I prepare for my fracture clinic visit?", "{id}", "No, that's all"]],
     ["zh_anticoag", ["抗凝门诊前需要准备什么？", "{id_zh}", "没有了"]]),
    ("clinical", "call", {"intent": "clinical", "action": "nurse_line"},
     ["en_fracture", ["Is it normal to have pain at night?", "{id}", "No, that's all"]],
     ["zh_anticoag", ["晚上有点痛，这正常吗？", "{id_zh}", "没有了"]]),
    ("transfer", "call", {"intent": "transfer", "outcome": "transferred"},
     [None, ["Can I speak to a real person?"]],
     [None, ["我要找人工服务"]]),
    ("clear", "followup", {"escalated": False},
     ["en", {"open": "I'm doing well, thank you."}],
     ["zh", {"open": "挺好的，谢谢"}]),
    ("fever", "followup", {"escalated": True, "by": "rules", "codes": ["fever"]},
     ["en", {"open": "I had a temperature of 38.8 this morning."}],
     ["zh", {"open": "今天早上体温38度8"}]),
    ("wound", "followup", {"escalated": True, "by": "rules", "codes": ["wound_drainage"]},
     ["en", {"open": "The wound keeps leaking a lot, the dressing is soaked."}],
     ["zh", {"open": "伤口一直在渗液，纱布都湿透了"}]),
    ("missed_medicine", "followup", {"escalated": True, "by": "model"},
     ["en", {"open": "Okay.", "med": "No, I forgot it yesterday."}],
     ["zh", {"open": "还行", "med": "没有，昨天忘了吃"}]),
    ("person", "followup", {"escalated": True, "by": "rules", "codes": ["wants_person"]},
     ["en", {"other": "I want to talk to a nurse please."}],
     ["zh", {"other": "我想跟护士说话"}]),
]


def main() -> None:
    out = EVALS_DIR / "call_center"
    out.mkdir(parents=True, exist_ok=True)
    rows = [{"id": i, "caller": c, "language": lang, "turns": turns, "intent": intent, "category": cat,
             "source": "authored"} for i, c, lang, turns, intent, cat in CALL_CENTER]
    assert len(rows) == 40 and sum(r["category"] == "identity_failure" for r in rows) == 6
    (out / "cases.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    out = EVALS_DIR / "followup"
    out.mkdir(parents=True, exist_ok=True)
    rows = [{"id": i, "patient": p, "red_flag": flag, "codes": codes, "answers": answers, "note": note,
             "source": "authored"} for i, p, flag, codes, answers, note in FOLLOWUP]
    assert len(rows) == 50 and sum(r["red_flag"] for r in rows) == 15
    (out / "cases.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    out = EVALS_DIR / "voice_bilingual"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for scenario, kind, expect, en, zh in BILINGUAL:
        for lang, (who, script) in (("en", en), ("zh", zh)):
            rows.append({"id": f"bi-{scenario}-{lang}", "scenario": scenario, "kind": kind, "language": lang,
                         "who": who, "script": script, "expect": expect, "source": "authored"})
    assert len(rows) == 20
    (out / "cases.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    print("wrote 40 call-centre, 50 follow-up and 20 bilingual cases")


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
