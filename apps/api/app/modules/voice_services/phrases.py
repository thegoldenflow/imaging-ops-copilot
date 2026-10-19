"""What the voice services say, in English, Simplified and Traditional Chinese (spec 7.4).

Every sentence a caller hears is written here by hand in the three scripts (as WP7's patient instructions are):
the model recognises intents and classifies follow-up calls, it never writes what the patient hears, so it cannot
give medical advice and the languages cannot drift apart. Placeholders are filled from structured data (the
caller's first name, an appointment, a red flag of the patient's signed instructions).

Also here: the clinics and services a call-centre appointment can be for (where, how long, opening hours, and the
preparation: a hand-written text, or the imaging module's approved prep instructions for imaging exams).

What the agent says carries its English rendering (`Said.en`): the same templates filled with the same values in
English. A Chinese call's transcript stores it with each agent turn (`Turn.gloss`), so an English-speaking audience
can follow the call (the storyline's demo mode shows it as a subtitle). It is the hand-written English of the same
sentence, not a model's translation.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.modules.documentation.phrases import MONTHS_EN, WEEKDAYS, Phrase, p

HOSPITAL = p("Lakeside General Hospital", "湖滨综合医院", "湖濱綜合醫院")
LANG_FOR = {"en": "en", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "zh": "zh-Hans", "zh-Hans": "zh-Hans",
            "zh-Hant": "zh-Hant"}
CJK = re.compile(r"[　-鿿＀-￯]")


class Said(str):
    """A sentence in the caller's language that also knows its English rendering (`en`)."""

    en: str

    def __new__(cls, text: str, en: str | None = None) -> Said:
        said = super().__new__(cls, text)
        said.en = text if en is None else en
        return said


def english(value):
    """A value's English rendering: a Said's `en`, anything else as it is."""
    return value.en if isinstance(value, Said) else value


def lang_of(code: str | None) -> str:
    """The rendering for a patient's communication.language (zh-CN -> Simplified, zh-TW -> Traditional)."""
    return LANG_FOR.get(code or "en", "en")


def say(phrase: Phrase, lang: str, **values) -> Said:
    return Said(phrase.get(lang, phrase["en"]).format(**values),
                phrase["en"].format(**{k: english(v) for k, v in values.items()}))


def when(dt: datetime, lang: str) -> Said:
    """"Tuesday, October 14 at 10:30" / "10月14日（星期二）10:30"."""
    en = f"{WEEKDAYS['en'][dt.weekday()]}, {MONTHS_EN[dt.month - 1]} {dt.day} at {dt:%H:%M}"
    if lang == "en":
        return Said(en)
    return Said(f"{dt.month}月{dt.day}日（{WEEKDAYS[lang][dt.weekday()]}）{dt:%H:%M}", en)


def _join_en(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def join(items: list[str], lang: str) -> Said:
    if not items:
        return Said("")
    en = _join_en([english(i) for i in items])
    return Said(en if lang == "en" else "、".join(items), en)


def cat(*parts: str, sep: str = " ") -> Said:
    """Sentences one after the other (Chinese runs on without a space: `sep=""`); the English always with one."""
    kept = [x for x in parts if x]
    return Said(sep.join(kept).strip(), " ".join(str(english(x)) for x in kept).strip())


def gloss(text: str, lang: str) -> str | None:
    """The English subtitle of an agent turn in another language: its English rendering, when every piece of it had
    one (a sentence kept from an earlier turn, such as a stored prompt, has none: then no subtitle)."""
    if lang == "en" or not isinstance(text, Said) or text.en == text or CJK.search(text.en):
        return None
    return text.en


# ---------- the call centre ----------

CC: dict[str, Phrase] = {
    "greeting": p("Thank you for calling Lakeside General Hospital. I'm the automated assistant. I can help with your "
                  "appointments, visiting hours, directions and parking, or put you through to a person. 需要中文服务"
                  "请直接说中文。How can I help you today?",
                  "您好，这里是湖滨综合医院的自动语音助理。我可以帮您处理预约，告诉您探视时间、科室位置和停车信息，"
                  "也可以为您转接工作人员。请问有什么可以帮您？",
                  "您好，這裡是湖濱綜合醫院的自動語音助理。我可以幫您處理預約，告訴您探視時間、科室位置和停車資訊，"
                  "也可以為您轉接工作人員。請問有什麼可以幫您？"),
    "switched": p("Sure, let's continue in English.", "好的，我们用中文继续。", "好的，我們用中文繼續。"),
    "options": p("I can reschedule or cancel an appointment, tell you when your appointments are, give you the "
                 "preparation instructions, answer questions about visiting hours, locations and parking, or put you "
                 "through to a person. What would you like to do?",
                 "我可以帮您改期或取消预约、查询预约时间、告诉您检查或就诊前的准备事项，回答探视时间、科室位置和停车的问题，"
                 "也可以为您转接工作人员。请问您需要什么帮助？",
                 "我可以幫您改期或取消預約、查詢預約時間、告訴您檢查或看診前的準備事項，回答探視時間、科室位置和停車的問題，"
                 "也可以為您轉接工作人員。請問您需要什麼幫助？"),
    "not_understood": p("Sorry, I didn't catch what you need.", "抱歉，我没有听明白您的需要。", "抱歉，我沒有聽明白您的需要。"),
    "ask_identity": p("I can help with that. First I need to confirm who the patient is: please tell me the patient's "
                      "full name, date of birth, and the last four digits of their health card.",
                      "可以。首先需要核对患者身份：请告诉我患者的全名、出生日期，以及健康卡号码的最后四位数字。",
                      "可以。首先需要核對病人身分：請告訴我病人的全名、出生日期，以及健康卡號碼的最後四位數字。"),
    "ask_name": p("Thank you. And the patient's full name?", "谢谢。请问患者的全名是？", "謝謝。請問病人的全名是？"),
    "ask_dob": p("Thank you. And the date of birth?", "谢谢。请问出生日期是？", "謝謝。請問出生日期是？"),
    "ask_card": p("Thank you. And the last four digits of the health card?", "谢谢。请问健康卡号码的最后四位是？",
                  "謝謝。請問健康卡號碼的最後四位是？"),
    "identity_retry": p("I'm sorry, those details don't match our records. Please say the full name, date of birth "
                        "and the last four digits of the health card again.",
                        "抱歉，这些信息和我们的记录不符。请再说一遍患者的全名、出生日期和健康卡号码的最后四位。",
                        "抱歉，這些資料和我們的紀錄不符。請再說一遍病人的全名、出生日期和健康卡號碼的最後四位。"),
    "identity_failed": p("I'm sorry, I still can't match those details, so I can't discuss this record. I'm putting you "
                         "through to a member of our staff now.",
                         "抱歉，信息仍然无法核对，我不能讨论这份记录。现在为您转接工作人员。",
                         "抱歉，資料仍然無法核對，我不能討論這份紀錄。現在為您轉接工作人員。"),
    "verified": p("Thank you, I've found the record for {first}.", "谢谢，已经找到{first}的记录。",
                  "謝謝，已經找到{first}的紀錄。"),
    "transfer": p("I'm putting you through to a member of our staff now. Please stay on the line.",
                  "现在为您转接工作人员，请不要挂断。", "現在為您轉接工作人員，請不要掛斷。"),
    "transfer_unrecognised": p("I'm sorry I couldn't help with that. I'm putting you through to a member of our staff "
                               "now. Please stay on the line.",
                               "抱歉没能帮上忙，现在为您转接工作人员，请不要挂断。", "抱歉沒能幫上忙，現在為您轉接工作人員，請不要掛斷。"),
    "transfer_distress": p("I can hear this is hard. I'm putting you through to a person right away. Please stay on the "
                           "line.", "我能感觉到您很着急，现在马上为您转接工作人员，请不要挂断。",
                           "我能感覺到您很著急，現在馬上為您轉接工作人員，請不要掛斷。"),
    "emergency": p("If this is an emergency, please hang up and call 911 now, or go to the nearest emergency "
                   "department. I'm also passing your call to our nurse line.",
                   "如果是紧急情况，请立即挂断电话拨打911，或马上去最近的急诊室。我同时把您的情况转给护士热线。",
                   "如果是緊急情況，請立即掛斷電話撥打911，或馬上去最近的急診室。我同時把您的情況轉給護理師專線。"),
    "clinical": p("I'm not able to answer medical questions. I've passed your question to our nurse line, and a nurse "
                  "will call you back today. If it gets worse or feels urgent, call 911.",
                  "我不能回答医疗方面的问题。我已经把您的问题转给护士热线，护士今天会给您回电话。如果情况加重或很紧急，"
                  "请拨打911。",
                  "我不能回答醫療方面的問題。我已經把您的問題轉給護理師專線，護理師今天會給您回電話。如果情況加重或很緊急，"
                  "請撥打911。"),
    "no_appointments": p("I don't see any upcoming appointments for {first}.", "没有查到{first}即将到来的预约。",
                         "沒有查到{first}即將到來的預約。"),
    "appointments": p("{first} has {count} upcoming appointment(s): {items}.", "{first}有{count}个即将到来的预约：{items}。",
                      "{first}有{count}個即將到來的預約：{items}。"),
    "appointment_item": p("{service} on {when}, {location}", "{when}，{service}，地点：{location}",
                          "{when}，{service}，地點：{location}"),
    "which": p("Which appointment do you mean: {items}?", "请问是哪一个预约：{items}？", "請問是哪一個預約：{items}？"),
    "which_item": p("number {n}, {service} on {when}", "第{n}个，{when}的{service}", "第{n}個，{when}的{service}"),
    "offer": p("For your {service}, I can offer {items}. Which one would you like?", "您的{service}可以改到：{items}。"
               "您想选哪一个？", "您的{service}可以改到：{items}。您想選哪一個？"),
    "offer_item": p("option {n}, {when}", "第{n}个，{when}", "第{n}個，{when}"),
    "pick_again": p("Sorry, which option would you like: the first, second or third?", "抱歉，请问您选第一个、第二个还是第三个？",
                    "抱歉，請問您選第一個、第二個還是第三個？"),
    "no_slots": p("I couldn't find an open time in the next two weeks. I'm passing your request to our booking clerk, "
                  "who will call you.", "未来两周没有找到空余时间。我把您的请求转给预约文员，他们会给您打电话。",
                  "未來兩週沒有找到空餘時間。我把您的請求轉給預約文員，他們會給您打電話。"),
    "confirm_move": p("To confirm: move your {service} from {old} to {new}?", "跟您确认一下：把{service}从{old}改到{new}，对吗？",
                      "跟您確認一下：把{service}從{old}改到{new}，對嗎？"),
    "moved": p("Done. I've asked our booking clerk to move your appointment to {new}. Once a clerk confirms it, the new "
               "time is booked and you'll be contacted. Is there anything else I can help with?",
               "好的。我已经请预约文员把您的预约改到{new}。文员确认后新时间就正式生效，我们会通知您。还有其他需要帮忙的吗？",
               "好的。我已經請預約文員把您的預約改到{new}。文員確認後新時間就正式生效，我們會通知您。還有其他需要幫忙的嗎？"),
    "confirm_cancel": p("To confirm: cancel your {service} on {when}?", "跟您确认一下：取消{when}的{service}，对吗？",
                        "跟您確認一下：取消{when}的{service}，對嗎？"),
    "cancelled": p("I've sent your cancellation to our booking clerk; you'll be contacted once it's done. Is there "
                   "anything else I can help with?", "我已经把取消请求交给预约文员，办好后会通知您。还有其他需要帮忙的吗？",
                   "我已經把取消請求交給預約文員，辦好後會通知您。還有其他需要幫忙的嗎？"),
    "kept": p("Okay, I haven't changed anything. Is there anything else I can help with?", "好的，我没有做任何更改。还有其他需要帮忙的吗？",
              "好的，我沒有做任何更改。還有其他需要幫忙的嗎？"),
    "prep": p("Here is how to prepare for your {service} on {when}: {text} I've noted on your record that we went over "
              "this. Is there anything else I can help with?",
              "{when}的{service}需要这样准备：{text}我已在您的记录中注明我们说明过这些事项。还有其他需要帮忙的吗？",
              "{when}的{service}需要這樣準備：{text}我已在您的紀錄中註明我們說明過這些事項。還有其他需要幫忙的嗎？"),
    "faq": p("{answer} Is there anything else I can help with?", "{answer}还有其他需要帮忙的吗？", "{answer}還有其他需要幫忙的嗎？"),
    "faq_none": p("I don't have that information. I can put you through to a person if you like.",
                  "我这里没有这方面的信息。需要的话我可以为您转接工作人员。", "我這裡沒有這方面的資訊。需要的話我可以為您轉接工作人員。"),
    "arrival_registered": p("Thank you. I have prepared {first}'s registration for the emergency department; the "
                            "registration desk will confirm it right away, so the triage nurse can see her as soon as "
                            "she arrives.",
                            "谢谢。我已经为{first}准备好急诊登记，登记处会马上确认，她一到急诊，分诊护士就能马上接诊。",
                            "謝謝。我已經為{first}準備好急診登記，登記處會馬上確認，她一到急診，分診護士就能馬上接診。"),
    "anything_else": p("Is there anything else I can help with?", "还有其他需要帮忙的吗？", "還有其他需要幫忙的嗎？"),
    "goodbye": p("Thank you for calling Lakeside General Hospital. Goodbye.", "感谢您致电湖滨综合医院，再见。",
                 "感謝您致電湖濱綜合醫院，再見。"),
}
# "switched" in Chinese says the call goes on in Chinese: its subtitle (the English template says the opposite)
SWITCHED_TO_CHINESE = "Sure, let's continue in Chinese."


def switched(lang: str) -> Said:
    """The sentence that confirms the call's new language, with its subtitle."""
    said = say(CC["switched"], lang)
    return said if lang == "en" else Said(said, SWITCHED_TO_CHINESE)

# ---------- the follow-up call ----------

FU: dict[str, Phrase] = {
    "intro": p("Hello, this is the automated follow-up call from Lakeside General Hospital for {first}. We're calling "
               "to see how you are doing since you went home; a nurse reads every call. To make sure I'm speaking with "
               "the right person, please tell me your date of birth.",
               "您好，这里是湖滨综合医院的自动随访电话，找{first}。我们想了解您出院回家后的情况，护士会查看每一次通话的记录。"
               "为了确认身份，请告诉我您的出生日期。",
               "您好，這裡是湖濱綜合醫院的自動隨訪電話，找{first}。我們想了解您出院回家後的情況，護理師會查看每一次通話的紀錄。"
               "為了確認身分，請告訴我您的出生日期。"),
    "dob_retry": p("Sorry, that doesn't match our record. Please say your date of birth again, for example March 4, "
                   "1952.", "抱歉，和我们的记录不符。请再说一遍您的出生日期，例如1952年3月4日。",
                   "抱歉，和我們的紀錄不符。請再說一遍您的出生日期，例如1952年3月4日。"),
    "dob_failed": p("I'm sorry, I can't confirm who I'm speaking with, so I'll stop here. A nurse from the hospital will "
                    "call you. Goodbye.", "抱歉，无法确认您的身份，这次通话就到这里。医院的护士会再给您打电话。再见。",
                    "抱歉，無法確認您的身分，這次通話就到這裡。醫院的護理師會再給您打電話。再見。"),
    "feeling": p("Thank you. How have you been feeling since you went home?", "谢谢。您出院回家以后感觉怎么样？",
                 "謝謝。您出院回家以後感覺怎麼樣？"),
    "flag": p("Since you went home, have you had this: {flag}?", "出院以后，您有没有出现这种情况：{flag}？",
              "出院以後，您有沒有出現這種情況：{flag}？"),
    "med": p("Are you taking {name} ({times}), as on your medicine timetable?", "您是否按照用药时间表（{times}）服用{name}？",
             "您是否按照用藥時間表（{times}）服用{name}？"),
    "med_stopped": p("Your instructions say to stop taking {name}. Have you stopped it?", "出院指导上说要停用{name}。您已经停用了吗？",
                     "出院指導上說要停用{name}。您已經停用了嗎？"),
    "med_problems": p("Have you had any problems with your medicines, such as side effects or running out?",
                      "您用药有没有遇到问题，比如不舒服的副作用，或者药快用完了？",
                      "您用藥有沒有遇到問題，比如不舒服的副作用，或者藥快用完了？"),
    "follow_up": p("Do you know about this follow-up appointment? {appointment}", "您知道这个复诊安排吗？{appointment}",
                   "您知道這個複診安排嗎？{appointment}"),
    "follow_up_tell": p("Here it is: {appointment} It's also in your written instructions.",
                        "复诊安排是：{appointment}出院指导上也写着。", "複診安排是：{appointment}出院指導上也寫著。"),
    "wound": p("How does your wound look? Is there any redness, swelling, or fluid coming from it?",
               "您的伤口看起来怎么样？有没有发红、肿胀，或者有液体渗出来？", "您的傷口看起來怎麼樣？有沒有發紅、腫脹，或者有液體滲出來？"),
    "care": p("Are you managing with your care at home: {items}?", "居家注意事项您都能做到吗：{items}",
              "居家注意事項您都能做到嗎：{items}"),
    "other": p("Is there anything else you would like the nurse to know?", "还有什么想让护士知道的吗？",
               "還有什麼想讓護理師知道的嗎？"),
    "unclear": p("Sorry, was that a yes or a no?", "抱歉，请问是“有”还是“没有”？", "抱歉，請問是「有」還是「沒有」？"),
    "no_advice": p("I can't give medical advice, but I've noted your question for the nurse.",
                   "我不能提供医疗建议，但已经把您的问题记下来转给护士。", "我不能提供醫療建議，但已經把您的問題記下來轉給護理師。"),
    "escalate_call": p("Thank you for telling me. A nurse will call you back within {minutes} minutes.",
                       "谢谢您告诉我。护士会在{minutes}分钟内给您回电话。", "謝謝您告訴我。護理師會在{minutes}分鐘內給您回電話。"),
    "escalate_emergency": p("This could be serious. Please hang up and call 911 now, or go to the nearest emergency "
                            "department. A nurse from the hospital will also call you. Goodbye.",
                            "这可能很严重。请立即挂断电话拨打911，或马上去最近的急诊室。医院的护士也会给您打电话。再见。",
                            "這可能很嚴重。請立即掛斷電話撥打911，或馬上去最近的急診室。醫院的護理師也會給您打電話。再見。"),
    "close_ok": p("Thank you, {first}, that's all my questions. If anything on your list of warning signs happens, do "
                  "what your instructions say. Take care. Goodbye.",
                  "谢谢您，{first}，我的问题问完了。如果出现出院指导里列出的警示症状，请按指导去做。祝您早日康复，再见。",
                  "謝謝您，{first}，我的問題問完了。如果出現出院指導裡列出的警示症狀，請按指導去做。祝您早日康復，再見。"),
    "close_escalated": p("Thank you, {first}. A nurse will call you back within {minutes} minutes. If it gets worse "
                         "before then, call 911. Goodbye.",
                         "谢谢您，{first}。护士会在{minutes}分钟内给您回电话。如果在这之前情况加重，请拨打911。再见。",
                         "謝謝您，{first}。護理師會在{minutes}分鐘內給您回電話。如果在這之前情況加重，請撥打911。再見。"),
    "close_review": p("Thank you, {first}. A nurse will look at what you told me and call you if needed. If anything "
                      "on your list of warning signs happens, do what your instructions say. Goodbye.",
                      "谢谢您，{first}。护士会查看您告诉我的情况，需要时会给您打电话。如果出现警示症状，请按出院指导去做。再见。",
                      "謝謝您，{first}。護理師會查看您告訴我的情況，需要時會給您打電話。如果出現警示症狀，請按出院指導去做。再見。"),
}

# ---------- services a call-centre appointment can be for ----------
# code -> name, place (shown as written), minutes, clinic hours (start, end), preparation: ("text", phrase) or
# ("imaging", exam code of the imaging module's approved prep instructions, System 9)
SERVICES: dict[str, dict] = {
    "fracture-clinic": {"name": p("fracture clinic visit", "骨科门诊", "骨科門診"),
                        "place": p("Fracture Clinic, Main Building, Level 1", "主楼一楼骨科门诊", "主樓一樓骨科門診"),
                        "minutes": 20, "hours": (9, 16),
                        "prep": ("text", p("Bring your medicines and any X-ray results from other hospitals. Wear loose "
                                           "clothes that fit over your cast or splint, and arrive 15 minutes early.",
                                           "请带上您的药物和在其他医院拍的X光结果。穿宽松、能套在石膏或夹板外面的衣服，提前15分钟到。",
                                           "請帶上您的藥物和在其他醫院拍的X光結果。穿寬鬆、能套在石膏或夾板外面的衣服，提前15分鐘到。"))},
    "anticoag-clinic": {"name": p("blood thinner clinic visit", "抗凝血药物门诊", "抗凝血藥物門診"),
                        "place": p("Anticoagulation Clinic, Main Building, Level 2", "主楼二楼抗凝血药物门诊",
                                   "主樓二樓抗凝血藥物門診"),
                        "minutes": 15, "hours": (8, 12),
                        "prep": ("text", p("Take your blood thinner as usual the evening before. Bring your blood thinner "
                                           "booklet and your list of medicines; the blood test is done at the visit.",
                                           "前一天晚上照常服用抗凝血药。请带上抗凝血药记录本和用药清单，抽血检查在门诊时做。",
                                           "前一天晚上照常服用抗凝血藥。請帶上抗凝血藥紀錄本和用藥清單，抽血檢查在門診時做。"))},
    "heart-clinic": {"name": p("cardiology clinic visit", "心脏科门诊", "心臟科門診"),
                     "place": p("Cardiology Clinic, Main Building, Level 3", "主楼三楼心脏科门诊", "主樓三樓心臟科門診"),
                     "minutes": 30, "hours": (9, 16),
                     "prep": ("text", p("Take your usual medicines unless your doctor told you otherwise. Bring your list "
                                        "of medicines, and wear a top that is easy to open for the heart tracing.",
                                        "除非医生另有交代，请照常服药。带上用药清单，穿方便解开的上衣，方便做心电图。",
                                        "除非醫師另有交代，請照常服藥。帶上用藥清單，穿方便解開的上衣，方便做心電圖。"))},
    "heart-failure-clinic": {"name": p("heart function clinic visit", "心功能门诊", "心功能門診"),
                             "place": p("Heart Function Clinic, Main Building, Level 3", "主楼三楼心功能门诊",
                                        "主樓三樓心功能門診"),
                             "minutes": 30, "hours": (9, 16),
                             "prep": ("text", p("Weigh yourself that morning and bring your weight diary and all your "
                                                "medicines.", "当天早上称一下体重，带上体重记录本和所有药物。",
                                                "當天早上量一下體重，帶上體重紀錄本和所有藥物。"))},
    "lung-clinic": {"name": p("respirology clinic visit", "呼吸科门诊", "胸腔科門診"),
                    "place": p("Respirology Clinic, Main Building, Level 4", "主楼四楼呼吸科门诊", "主樓四樓胸腔科門診"),
                    "minutes": 30, "hours": (9, 16),
                    "prep": ("text", p("Bring your inhalers. Do not use your quick-relief inhaler in the 4 hours before "
                                       "the breathing test unless you need it.",
                                       "请带上您的吸入器。做肺功能检查前4小时内，除非需要，不要使用急救吸入器。",
                                       "請帶上您的吸入器。做肺功能檢查前4小時內，除非需要，不要使用急救吸入器。"))},
    "surgery-clinic": {"name": p("surgery clinic visit", "外科门诊", "外科門診"),
                       "place": p("General Surgery Clinic, Main Building, Level 2", "主楼二楼普通外科门诊",
                                  "主樓二樓一般外科門診"),
                       "minutes": 20, "hours": (9, 16),
                       "prep": ("text", p("Bring your list of medicines. You can shower as usual before you come; the "
                                          "nurse will check your wound.", "请带上用药清单。来之前可以照常淋浴，护士会检查您的伤口。",
                                          "請帶上用藥清單。來之前可以照常淋浴，護理師會檢查您的傷口。"))},
    "preadmission-clinic": {"name": p("pre-admission clinic visit", "术前评估门诊", "術前評估門診"),
                            "place": p("Pre-admission Clinic, Main Building, Level 1", "主楼一楼术前评估门诊",
                                       "主樓一樓術前評估門診"),
                            "minutes": 60, "hours": (8, 15),
                            "prep": ("text", p("Bring all your medicines in their boxes and your health card. The visit "
                                               "takes about an hour and includes blood tests.",
                                               "请带上所有药物（连同药盒）和健康卡。这次就诊大约一小时，包括抽血检查。",
                                               "請帶上所有藥物（連同藥盒）和健康卡。這次看診大約一小時，包括抽血檢查。"))},
    "ct-scan": {"name": p("CT scan", "CT扫描", "電腦斷層掃描"),
                "place": p("Medical Imaging, Main Building, Level 1", "主楼一楼影像科", "主樓一樓影像科"),
                "minutes": 30, "hours": (8, 17), "prep": ("imaging", "CT_ABD_PEL")},
    "ultrasound": {"name": p("ultrasound", "超声检查", "超音波檢查"),
                   "place": p("Medical Imaging, Main Building, Level 1", "主楼一楼影像科", "主樓一樓影像科"),
                   "minutes": 30, "hours": (8, 17), "prep": ("imaging", "US_ABD")},
}
