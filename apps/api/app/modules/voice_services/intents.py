"""The call centre's rule layer (spec 7.4): what must never wait for a model, and the intent fallback.

Checked on every caller turn, before the model: an emergency (the 911 prompt and the nurse line), distress or a
request for a person (put through at once). When the model is unavailable (no key, provider down, prod mode before
the agent passed its eval) these keyword rules recognise the intent instead, in English and Chinese, so the call
centre keeps working. Pure functions over text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.voice_services import redflags

INTENTS = ("reschedule", "cancel", "lookup", "faq", "prep", "clinical", "transfer", "goodbye", "other")
PERSONAL = frozenset({"reschedule", "cancel", "lookup", "prep", "clinical"})  # need the identity check first
FAQ_TOPICS = ("visiting_hours", "location", "parking", "general")


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


HUMAN = _rx(r"\b(human|real person|a person|someone|somebody|agent|operator|representative|receptionist|staff member|"
            r"talk to (a|an|the)? ?(nurse|doctor|person)|speak (to|with) (a|an|the)? ?(nurse|doctor|person))\b|人工|真人|"
            r"转接|接线员|工作人员|找护士|找医生|跟人说")
DISTRESS = _rx(r"\b(crying|i'?m (so )?(scared|terrified|panicking)|panic|furious|so angry|ridiculous|unacceptable|"
               r"desperate|can'?t take (it|this)|help me please|this is a nightmare|i'?m begging)\b|急死了|害怕|救命|"
               r"哭|太过分|受不了|气死|崩溃|求求你")
EMERGENCY = _rx(r"\b(collapsed|unconscious|not breathing|can'?t wake|stroke|seizure|overdose)\b|昏迷|晕倒|叫不醒|"
                r"没有呼吸|中风|抽搐")
CANCEL = _rx(r"\bcancel\w*|\bcall off\b|\bwon'?t (be able to )?(make|come)\b|\bcan'?t (make|come)\b|取消|不去了|去不了")
RESCHEDULE = _rx(r"\b(reschedul\w*|rebook\w*|move (my|the|it|her|his)|change (my|the|her|his|that|this) "
                 r"(appointment|time|date|day|visit)|change the date|another (day|time|date)|different (day|time|date)|"
                 r"later date|earlier (time|date)|postpone\w*|push (it|my appointment) back)\b|改期|改约|改.{0,6}(时间|日期|"
                 r"天|時間)|换.{0,4}(时间|日期|一天|天)|推迟|延期|提前|改到|改天")
# a question about the caller's own appointment (checked before the general questions)
LOOKUP = _rx(r"\b(my appointments?\b(?!.*\b(cancel|move|reschedul))|check (my|her|his|the) appointment|do i have (an|any)"
             r"|upcoming|next appointment|appointment (time|date))\b|(查|看|问|确认).{0,6}(预约|預約)|我的(预约|預約)|"
             r"有没有预约|(预约|預約).{0,4}(时间|几点|哪天|什么时候|在哪|時間)")
WHEN = _rx(r"\b(when is|what time is|when are)\b|几点|什么时候|哪天|幾點|什麼時候")  # general: after the FAQ
PREP = _rx(r"\b(prepare|preparation|prep|fast(ing)?|before (my|the|her|his) (test|scan|visit|appointment|procedure|"
           r"surgery)|what (should|do) (i|she|he) bring|bring anything|can (i|she|he) eat|empty stomach|arrive early)\b|"
           r"准备|準備|注意事项|空腹|要带什么|带什么|能不能吃|要不要禁食|提前多久")
CLINICAL = _rx(r"\b(pain|hurts?|ache|symptom\w*|bleed\w*|fever|dizz\w*|nause\w*|vomit\w*|medicine|medication|pills?|"
               r"dose|dosage|side effects?|blood thinner|warfarin|ibuprofen|results?|diagnos\w*|is it normal|is it safe|"
               r"is that normal|serious|swelling|swollen|rash|wound|cough\w*|infection|stop taking|start taking)\b|痛|疼|"
               r"发烧|發燒|药|藥|剂量|副作用|结果|化验|正常吗|严重吗|要不要紧|要不要緊|伤口|傷口|头晕|頭暈|咳嗽|肿|腫|出血|感染|"
               r"停药|减量")
FAQ = _rx(r"\b(visiting|visitors?|visit hours|where (is|are|do)|address|directions?|located|location|parking|park|"
          r"garage|floor|level|cafeteria|pharmacy|interpreter|translator|wifi|bus|subway|drop[ -]off|wheelchair|"
          r"medical records)\b|探视|探視|探望|在哪|地址|怎么走|怎么去|怎麼走|停车|停車|几楼|幾樓|餐厅|食堂|药房|翻译|轮椅|"
          r"接送|病历|病歷")
GOODBYE = _rx(r"\b(that'?s all|that is all|nothing else|no,? thanks?|no thank you|bye|goodbye|have a good|"
              r"that'?s it)\b|没有了|没事了|再见|就这些|不用了")
THANKS_ONLY = _rx(r"^\s*(ok(ay)?[,.]?\s*)?(thanks|thank you)( very much| so much)?[.!]?\s*$|^\s*谢谢(你|您)?[。！]?\s*$")
YES = _rx(r"\b(yes|yeah|yep|correct|right|sure|please do|go ahead|ok(ay)?|that'?s right|do it)\b|^\s*(是|好|对|行|可以|"
          r"确定|没错)|是的|好的|对的|可以的")
NO = _rx(r"\b(no|nope|don'?t|do not|never mind|wait|not)\b|不要|不用|不是|算了|等一下|不对")
# Ordinal words first ("the second one" is option 2, not "one"), then bare numbers.
ORDINALS = [(0, _rx(r"\b(first|1st)\b|第一|头一个")),
            (1, _rx(r"\b(second|2nd)\b|第二")),
            (2, _rx(r"\b(third|3rd)\b|第三")),
            (0, _rx(r"\b(one|number one|option one|the 1)\b|一号|^\s*1\s*$")),
            (1, _rx(r"\b(two|number two|option two|the 2)\b|二号|^\s*2\s*$")),
            (2, _rx(r"\b(three|number three|option three|the 3)\b|三号|^\s*3\s*$"))]
CHINESE = re.compile(r"[一-鿿]")
ASK_CHINESE = _rx(r"\b(mandarin|chinese|cantonese)\b|中文|普通话|国语|華語|中文服务")
ASK_ENGLISH = _rx(r"\benglish\b|英文|英语")
TOPIC_WORDS = [("parking", _rx(r"\b(park|parking|garage|drop[ -]off|wheelchair)\b|停车|停車|轮椅|接送")),
               ("visiting_hours", _rx(r"\b(visit\w*|visitors?)\b|探视|探視|探望|探病")),
               ("location", _rx(r"\b(where|address|directions?|located|location|floor|level|bus|subway)\b|在哪|地址|"
                                r"怎么走|怎么去|几楼"))]


@dataclass
class Recognised:
    intent: str
    confidence: float
    source: str  # model / rules
    faq_topic: str | None = None
    language: str | None = None  # en / zh
    distress: bool = False
    emergency: bool = False


def emergency(text: str) -> bool:
    return bool(EMERGENCY.search(text or "")) or any(h.emergency for h in redflags.scan(text or ""))


def faq_topic(text: str) -> str:
    return next((topic for topic, rx in TOPIC_WORDS if rx.search(text or "")), "general")


def recognise(text: str) -> Recognised:
    """The rule fallback (and the mock model's basis): the first matching intent in safety-then-action order."""
    t = text or ""
    lang = "zh" if CHINESE.search(t) else "en"
    distress, urgent = bool(DISTRESS.search(t)), emergency(t)
    if HUMAN.search(t):
        return Recognised("transfer", 0.9, "rules", language=lang, distress=distress, emergency=urgent)
    for intent, rx in (("reschedule", RESCHEDULE), ("cancel", CANCEL)):  # "can't make it, change the date": move
        if rx.search(t):
            return Recognised(intent, 0.85, "rules", language=lang, distress=distress, emergency=urgent)
    if CLINICAL.search(t) or urgent:
        return Recognised("clinical", 0.85, "rules", language=lang, distress=distress, emergency=urgent)
    if PREP.search(t):
        return Recognised("prep", 0.85, "rules", language=lang, distress=distress)
    if LOOKUP.search(t):
        return Recognised("lookup", 0.8, "rules", language=lang, distress=distress)
    if FAQ.search(t):
        return Recognised("faq", 0.8, "rules", faq_topic=faq_topic(t), language=lang, distress=distress)
    if WHEN.search(t):
        return Recognised("lookup", 0.7, "rules", language=lang, distress=distress)
    if GOODBYE.search(t) or THANKS_ONLY.search(t):
        return Recognised("goodbye", 0.8, "rules", language=lang, distress=distress)
    return Recognised("other", 0.3, "rules", language=lang, distress=distress)


def yes_no(text: str) -> str:
    """A confirmation: whichever of yes and no the caller said first ("yes please" / "no, wait")."""
    t = text or ""
    yes, no = YES.search(t), NO.search(t)
    if no and re.search(r"\bno problem\b|没问题", t, re.I):
        no = None
    if yes and no:
        return "yes" if yes.start() < no.start() else "no"
    return "yes" if yes else "no" if no else "unclear"


def ordinal(text: str) -> int | None:
    return next((i for i, rx in ORDINALS if rx.search(text or "")), None)
