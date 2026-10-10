"""What a caller asks for, by keyword, in English, French and Chinese.

The rules of the scripted fallback (agent.py: the phone line when the model is down) and of the phone agent's mock
model (mock_agent.py: its eval without a key). A medical question or a request for a person is put through to staff
in every language, so `medical` errs on the side of matching. French words match with or without accents (speech
to text may drop them).
"""

from __future__ import annotations

import re

WORDS = {
    "human": r"\b(human|person|someone|agent|operator|representative|staff)\b|quelqu'un|une personne|un humain|"
             r"人工|真人|工作人员",
    "medical": r"\b(pain|hurt|symptom|bleed|dizzy|result|diagnos|cancer|tumou?r|safe|dangerous|medication|medicine|"
               r"pregnan|allerg|worried|radiation|side effect)\w*|is it normal|"
               r"douleur|j'ai mal|sympt[oô]me|r[ée]sultat|dangereu|m[ée]dicament|enceinte|grossesse|inqui[eè]t|"
               r"saign|tumeur|effets? secondaires?|"
               r"疼|痛|结果|诊断|癌|肿瘤|安全|危险|药|怀孕|过敏|担心|症状|出血|头晕|正常吗|辐射|副作用",
    "cancel": r"\bcancel\w*|\bannul\w*|取消",
    "reschedule": r"\b(reschedul\w*|move|change|another (day|time)|different (day|time))\b|d[ée]placer|changer|"
                  r"reporter|modifier|un autre (jour|moment)|une autre (date|heure)|"
                  r"改约|改期|改时间|换个时间|换时间|推迟|延期",
    "confirm": r"\bconfirm(e|er)?\b|确认",
    "prep": r"\b(prep\w*|instruction\w*|eat|fast\w*|drink|bring|wear)\b|pr[ée]par\w*|consigne|[àa] jeun|manger|"
            r"boire|apporter|须知|准备|空腹|注意事项",
    "site": r"\b(where|address|direction\w*|hours|open|close|parking|located)\b|adresse|horaire|heures d'ouverture|"
            r"ouvert|stationnement|\boù\b|地址|营业|停车|在哪|怎么走|开门",
    "yes": r"\b(yes|yeah|yep|correct|sure|please do|go ahead|ok(ay)?)\b|\boui\b|d'accord|parfait|exactement|"
           r"allez-y|是|好|对|可以|行",
    "no": r"\b(no|nope|don't|do not|never mind|keep)\b|\bnon\b|pas besoin|laissez|gardez|不|别|算了",
    "done": r"\b(that's all|that is all|nothing else|bye|goodbye|thank you|thanks)\b|c'est tout|au revoir|merci|"
            r"bonne journ[ée]e|再见|谢谢|没有了|没事了|就这样",
}
RULES = {k: re.compile(v, re.I) for k, v in WORDS.items()}
TASKS = ("cancel", "reschedule", "prep", "site", "confirm")  # what a caller can ask the phone line to do
ORDINAL_WORDS = [(re.compile(r"\bfirst\b|premi[eè]re?|第一|第1"), 0), (re.compile(r"\bsecond\b|deuxi[eè]me|第二|第2"), 1),
                 (re.compile(r"\bthird\b|troisi[eè]me|第三|第3"), 2)]
ORDINAL_NUMBERS = [(re.compile(r"\b(1|one)\b|(option|num[ée]ro) un\b"), 0), (re.compile(r"\b(2|two|deux)\b"), 1),
                   (re.compile(r"\b(3|three|trois)\b"), 2)]


def _plain(text: str) -> str:
    return text.lower().replace("’", "'")  # speech to text may write j’ai, that’s


def has(rule: str, text: str) -> bool:
    return bool(RULES[rule].search(_plain(text)))


def ordinal(text: str) -> int | None:
    """The option a caller picks (0-based): "the second one", "la deuxième", "第二个", "option 2"."""
    for rules in (ORDINAL_WORDS, ORDINAL_NUMBERS):  # "the second one" is option 2
        for rx, index in rules:
            if rx.search(_plain(text)):
                return index
    return None
