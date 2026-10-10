"""Red-flag rules of the follow-up call (spec 7.4): deterministic, English and Chinese, run before any model.

The nine red flags of 7.4: chest pain, shortness of breath, heavy bleeding, a temperature of 38.5 °C or more,
altered consciousness, a fall, heavy wound drainage, unable to eat or drink, and the patient asking for a person.
`scan(text)` finds them in what the patient said, clause by clause, skipping negated mentions ("no chest pain",
"没有胸痛", "I haven't fallen"); a temperature counts from 38.5 °C (or 101.3 °F) whatever words surround it, and a
reported fever without a reading counts too (recall first: a nurse checks). A hit escalates the call at once,
without waiting for the model (followup.py). The classifier comes second and can only add escalations.

`polarity(text)` reads a yes or a no; a yes to one of the patient's own red-flag questions is a hit as well
(followup.py). Patterns are kept simple and readable; the follow-up eval (evals/followup) checks them on 50 scripts.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# 7.4 rule code -> (English label, Chinese label, emergency: the 911 prompt; else the nurse call-back)
RULES: dict[str, tuple[str, str, bool]] = {
    "chest_pain": ("Chest pain", "胸痛", True),
    "short_breath": ("Shortness of breath", "呼吸困难", True),
    "heavy_bleeding": ("Heavy bleeding", "大量出血", True),
    "fever": ("Temperature of 38.5 °C or more", "体温38.5度或以上", False),
    "altered_consciousness": ("Altered consciousness", "意识改变", True),
    "fall": ("A fall", "跌倒", False),
    "wound_drainage": ("Heavy wound drainage", "伤口大量渗液", False),
    "cannot_eat_drink": ("Unable to eat or drink", "无法进食或饮水", False),
    "wants_person": ("Asks to speak to a person", "要求与人通话", False),
}
# The patient instructions' red flags (WP7 phrases.RED_FLAGS) that are the same finding as a 7.4 rule
INSTRUCTION_FLAG_RULE = {"chest_pain": "chest_pain", "short_breath": "short_breath", "heavy_bleeding": "heavy_bleeding",
                         "fever": "fever", "confusion": "altered_consciousness", "fainting": "altered_consciousness",
                         "fall": "fall", "cannot_eat_drink": "cannot_eat_drink", "wound_infection": "wound_drainage",
                         "puncture_site": "wound_drainage"}

FEVER_C = 38.5

_PATTERNS: dict[str, list[str]] = {
    "chest_pain": [
        r"chest (pain|pains|pressure|tightness|discomfort|hurts?|is hurting|feels? (tight|heavy|like))",
        r"pain (in|across|on) (my|the|his|her) chest", r"tight(ness)? in (my|the|his|her) chest",
        r"胸(口|部)?(有点|有些|有一点|很|好|特别|老是|一直|总是|还是)?(痛|疼|闷|发闷|压|憋|紧)", r"心口(痛|疼)"],
    "short_breath": [
        r"short(ness)? of breath", r"(can'?t|cannot|can not|hard to|trouble|difficulty|struggling to|unable to) breath",
        r"out of breath", r"breathless", r"gasping", r"wheez",
        r"呼吸(困难|不畅|急促|不过来)", r"喘不(过|上)气", r"气(短|急|喘)", r"透不过气", r"上不来气", r"憋气"],
    "heavy_bleeding": [
        r"(heavy|heavily|a lot of|lots of|so much|too much|serious) (bleeding|blood)",
        r"bleed(ing|s)? (a lot|heavily|badly|everywhere|through)", r"(won'?t|will not|doesn'?t|does not|can'?t) stop "
        r"bleeding", r"bleed\w*[a-z ]{0,30}(won'?t|will not|doesn'?t|does not|can'?t) stop", r"soaked (in|with) blood",
        r"blood everywhere",
        r"(大量|很多|好多)(出血|流血|的血)", r"(流|出)血不止", r"血(止不住|流不止|一直流|不停)", r"出了(很多|好多|一大片)血"],
    "altered_consciousness": [
        r"confus", r"disorient", r"not making sense", r"(hard|difficult|impossible) to wake", r"won'?t wake",
        r"(passed|blacked) out", r"faint(ed|ing)?\b", r"unconscious", r"unresponsive", r"doesn'?t (know|recognise|recognize) "
        r"(me|where)", r"not (himself|herself|myself)", r"seeing things",
        r"(意识|神志)(不清|模糊|改变)", r"糊涂", r"叫不醒", r"昏迷", r"(晕|昏)倒", r"不认(人|识人)", r"说胡话", r"迷迷糊糊"],
    "fall": [
        r"\bfell\b", r"\bfallen\b", r"(had|took) a (bad )?fall", r"\bfall(s)? (down|over)\b", r"slipped and",
        r"tripped (over|and)", r"hit (my|his|her) head", r"(摔|跌)(倒|了|跤|下)", r"跌了一跤", r"摔了一跤", r"撞到头"],
    "wound_drainage": [
        r"(wound|incision|cut|stitches|dressing|site|scar)[a-z ]{0,25}(leak|ooz|drain|discharg|weep|seep)(ing|s|ed)?"
        r"[a-z ]{0,20}(a lot|lots|heavily|loads|non-?stop|all the time|keeps|constantly|through|so much|more and more)",
        r"(a lot of|lots of|so much|heavy|loads of|constant) (fluid|discharge|drainage|liquid|ooz\w*|leak\w*) "
        r"(from|out of|on|at|coming from) (the|my|his|her) (wound|incision|cut|dressing|site)",
        r"(soaked|soaking|drenched) (through )?(the|my|his|her) (dressing|bandage|gauze|pad)",
        r"(dressing|bandage|gauze)[a-z ]{0,20}(soaked|soaking|wet through|drenched)", r"\bpus\b", r"pus-like",
        r"伤口.{0,8}(渗液|渗出|流水|流液|渗水|冒水).{0,6}(很多|好多|大量|不停|一直|湿透|越来越多|止不住)",
        r"伤口.{0,6}(一直|不停|大量|好多|很多)(在)?(渗|流|冒)", r"(纱布|敷料|绷带).{0,6}(湿透|全湿|都湿)",
        r"(流脓|化脓|有脓)"],
    "cannot_eat_drink": [
        r"(can'?t|cannot|can not|unable to|not able to) (eat|drink|keep (anything|food|water|fluids?|it) down|swallow)",
        r"(not|haven'?t been|hasn'?t been) (eating or drinking|eating and drinking|drinking anything|eating anything)",
        r"(throwing|throw) up (everything|all|whatever)", r"vomit(ing|s)? (everything|all|whatever)",
        r"nothing stays down",
        r"吃不(下|进)", r"喝不(下|进)", r"(不能|没法|无法)(吃|喝|进食|吞)", r"吃什么(都)?吐", r"一吃就吐", r"一喝就吐",
        r"(吃|喝)不了东西", r"什么都吃不(下|进)"],
    "wants_person": [
        r"(talk|speak|chat) (to|with) (a|an|the|some|real)? ?(nurse|doctor|person|human|someone|somebody|staff|real "
        r"person)", r"(want|need|like) (a|the) (nurse|doctor) to call", r"call me back", r"real person",
        r"(找|和|跟|叫)(护士|医生|真人|人工)", r"(护士|医生).{0,4}(打|回)(电话|过来)", r"转人工", r"人工服务"],
}
_RULES = {code: [re.compile(p, re.IGNORECASE) for p in pats] for code, pats in _PATTERNS.items()}

FEVER_WORDS = re.compile(r"\bfever(ish)?\b|\bhigh temperature\b|\brunning a temperature\b|发烧|发热|高烧|烧得", re.I)
TEMP = re.compile(r"(\d{2,3}(?:[.,]\d{1,2})?)\s*(°\s*[cf]|℃|℉|degrees?(?:\s*(?:c|f|celsius|fahrenheit))?|度(\d)?)",
                  re.IGNORECASE)
TEMP_PLAIN = re.compile(r"(?:temperature|temp|fever|体温|发烧|烧)[^\d]{0,20}(\d{2,3}(?:[.,]\d)?)", re.IGNORECASE)
ZH_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
ZH_TEMP = re.compile(r"(三|四)十([一二三四五六七八九])?度(半|[一二三四五六七八九])?")

# Clause breaks: punctuation (not a decimal point), "but"; Chinese conjunctions only where they cannot be part of a
# word ("不过" is also in 喘不过气, so it is not a break)
CLAUSE = re.compile(r"(?<!\d)[.,](?!\d)|[;!?。；！？\n]|，|、|\bbut\b|\bhowever\b|但是|可是", re.IGNORECASE)
NEG_EN = re.compile(r"\b(no|not|never|without|haven'?t|hasn'?t|hadn'?t|didn'?t|don'?t|doesn'?t|isn'?t|wasn'?t|"
                    r"aren'?t|weren'?t|nor|none|nothing|denies)\b(?! (sure|certain|only|just))", re.IGNORECASE)
JOINER = re.compile(r"\b(and|or|so|because|then|now)\b", re.IGNORECASE)  # a negation does not carry past these
NEG_ZH = re.compile(r"(没有|没|不|无|未|并无|别)[^\s，。]{0,2}$")

YES = re.compile(r"\b(yes|yeah|yep|yup|ya|i have|i did|i do|i am|i was|it is|it does|a little|a bit|some|sometimes|"
                 r"correct|right|sure|of course|definitely|uh-huh|mm-hmm|i know|i remember|got it|i'?m taking|"
                 r"i am taking)\b|^\s*(有|是|对|嗯|好|知道|在吃|吃了|按时|会)"
                 r"|有一点|有点|有的|是的|对的|知道的|记得|我(都)?(有|知道|记得|在吃|按时)", re.IGNORECASE)
NO = re.compile(r"\b(no|nope|nah|not really|none|never|haven'?t|hasn'?t|didn'?t|don'?t|doesn'?t|nothing|not at all|"
                r"not that i know)\b|没有|没|不是|不知道|不记得|不清楚|无|都没|不会|没吃|忘了", re.IGNORECASE)


@dataclass
class Hit:
    code: str
    label: str
    emergency: bool
    quote: str  # the clause the patient said
    how: str = "keyword"  # keyword, temperature, answer (a yes to the patient's own red-flag question)
    temperature_c: float | None = None
    question: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def hit(code: str, quote: str, *, how: str, temperature_c: float | None = None, question: str | None = None) -> Hit:
    en, _, emergency = RULES[code]
    return Hit(code, en, emergency, quote.strip()[:300], how, temperature_c, question)


def clauses(text: str) -> list[str]:
    return [c.strip() for c in CLAUSE.split(text or "") if c and c.strip()]


def _negated(clause: str, start: int) -> bool:
    """A negation shortly before the mention, in the same part of the clause ("no chest pain", "没有胸痛";
    not "I didn't sleep and my chest hurts")."""
    before = clause[:start]
    if NEG_ZH.search(before.rstrip()):
        return True
    parts = JOINER.split(before)
    return bool(NEG_EN.search(parts[-1][-40:] if parts else before[-40:]))


def _zh_temperature(text: str) -> float | None:
    m = ZH_TEMP.search(text)
    if not m:
        return None
    value = (3 if m.group(1) == "三" else 4) * 10 + (ZH_NUM[m.group(2)] if m.group(2) else 0)
    if m.group(3) == "半":
        return value + 0.5
    return value + (ZH_NUM[m.group(3)] / 10 if m.group(3) else 0)


def temperatures(text: str) -> list[float]:
    """Temperatures in °C the patient read out (°F converted; "38度5" = 38.5; "三十八度五" = 38.5)."""
    out = []
    for m in TEMP.finditer(text or ""):
        value = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "").lower()
        if m.group(3):  # 38度5
            value = float(f"{int(value)}.{m.group(3)}")
        if "f" in unit or "℉" in unit or value > 50:
            value = round((value - 32) * 5 / 9, 1)
        if 30 <= value <= 45:
            out.append(value)
    if not out:
        for m in TEMP_PLAIN.finditer(text or ""):
            value = float(m.group(1).replace(",", "."))
            if 34 <= value <= 43:
                out.append(value)
            elif 93 <= value <= 110:
                out.append(round((value - 32) * 5 / 9, 1))
    if (zh := _zh_temperature(text or "")) is not None:
        out.append(zh)
    return out


def scan(text: str) -> list[Hit]:
    """The 7.4 red flags in what the patient said (one hit per rule, the first clause that shows it). A temperature
    read out decides the fever rule (from 38.5 °C); without a reading, a fever the patient reports counts."""
    hits: dict[str, Hit] = {}
    readings = temperatures(text)
    for clause in clauses(text):
        for code, patterns in _RULES.items():
            if code in hits:
                continue
            for pattern in patterns:
                m = pattern.search(clause)
                if m and not _negated(clause, m.start()):
                    hits[code] = hit(code, clause, how="keyword")
                    break
        if "fever" in hits:
            continue
        if readings:
            here = temperatures(clause)
            if here and max(here) >= FEVER_C:
                hits["fever"] = hit("fever", clause, how="temperature", temperature_c=max(here))
        elif (m := FEVER_WORDS.search(clause)) and not _negated(clause, m.start()):
            hits["fever"] = hit("fever", clause, how="keyword")
    return list(hits.values())


def polarity(text: str) -> str:
    """yes / no / unclear for an answer to a yes-or-no question (the first clause that says either)."""
    for clause in clauses(text) or [text or ""]:
        no, yes = NO.search(clause), YES.search(clause)
        if no and (not yes or no.start() <= yes.start() or yes.group(0) in no.group(0)):
            return "no"
        if yes:
            return "yes"
    return "unclear"
