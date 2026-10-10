"""The identity check of the call centre (spec 7.4), in code: name + date of birth + the last four digits of the
synthetic health card, all three, before any personal intent; two failed attempts put the caller through to a
person.

`parse(text)` pulls the three pieces (and who is calling, when it is a relative) out of what the caller said, in
English or Chinese; the pieces add up over several turns ("I'm Mei Chen." "Born March 4, 1952." "Card ends in
4417."). Identity turns never go to the model: the dialog (callcenter.py) knows when it asked for them, and the
comparison with the record is the findCaller tool's, on the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.frontdesk.tools import parse_dob

DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
               "seven": "7", "eight": "8", "nine": "9", "零": "0", "〇": "0", "一": "1", "二": "2", "两": "2", "三": "3",
               "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "幺": "1"}
SPOKEN = re.compile(r"\b(zero|oh|one|two|three|four|five|six|seven|eight|nine)(?:[\s,-]+(zero|oh|one|two|three|four|"
                    r"five|six|seven|eight|nine)){3}\b", re.IGNORECASE)
ZH_DIGITS = re.compile(r"[零〇一二两三四五六七八九幺]{4}")
LAST4 = re.compile(r"(?:end(?:s|ing)?\s*(?:in|with)?|last\s*(?:four|4)(?:\s*digits)?(?:\s*(?:are|is|of[^\d]*))?|"
                   r"尾号|后四位|最后四位|末四位)[^\d]{0,12}(\d[\s-]?\d[\s-]?\d[\s-]?\d)(?!\d)", re.IGNORECASE)
FOUR = re.compile(r"(?<![\d.:/-])(\d{4})(?![\d.:/-])")
DATES = re.compile(r"\b(19\d{2}|20\d{2})-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/(19|20)\d{2}\b|(19|20)\d{2}年\d{1,2}月\d{1,2}"
                   r"(日|号)?|\b(january|february|march|april|may|june|july|august|september|october|november|"
                   r"december)\s+\d{1,2}(st|nd|rd|th)?,?\s+(19|20)\d{2}\b|\b\d{1,2}(st|nd|rd|th)?\s+(january|february|"
                   r"march|april|may|june|july|august|september|october|november|december),?\s+(19|20)\d{2}\b",
                   re.IGNORECASE)
RELATION = re.compile(r"\bmy (mother|mom|mum|father|dad|husband|wife|son|daughter|grandmother|grandfather|"
                      r"grandma|grandpa|sister|brother|aunt|uncle)\b|我(的)?(妈妈|妈|母亲|爸爸|爸|父亲|丈夫|先生|老公|太太|"
                      r"妻子|老婆|儿子|女儿|奶奶|爷爷|外婆|外公|姐姐|妹妹|哥哥|弟弟)", re.IGNORECASE)
RELATION_EN = {"mom": "mother", "mum": "mother", "dad": "father", "grandma": "grandmother", "grandpa": "grandfather"}
RELATION_ZH = {"妈妈": "mother", "妈": "mother", "母亲": "mother", "爸爸": "father", "爸": "father", "父亲": "father",
               "丈夫": "husband", "先生": "husband", "老公": "husband", "太太": "wife", "妻子": "wife", "老婆": "wife",
               "儿子": "son", "女儿": "daughter", "奶奶": "grandmother", "爷爷": "grandfather", "外婆": "grandmother",
               "外公": "grandfather", "姐姐": "sister", "妹妹": "sister", "哥哥": "brother", "弟弟": "brother"}
FILLER = re.compile(r"\b(hi|hello|hey|yes|yeah|ok|okay|sure|so|um|uh|well|thanks|thank you|this is|it'?s|i'?m|i am|my|"
                    r"name|names|is|her|his|the|patient|patient's|calling|for|about|on behalf of|mother|mom|mum|"
                    r"father|dad|husband|wife|son|daughter|grandmother|grandfather|sister|brother|born|birth|date of|"
                    r"dob|birthday|and|health|card|number|ending|ends|in|with|last|four|digits|are|of|a|an|i|want|"
                    r"need|to|would|like|please|appointment|cancel|reschedule|change|move|check|when|where|what|"
                    r"zero|oh|one|two|three|five|six|seven|eight|nine)\b", re.IGNORECASE)
CJK_NAME = re.compile(r"[一-鿿·]{2,}")
NAME_CUE = re.compile(r"\b(my name is|this is|i am|i'm|name's|calling for|calling about|on behalf of)\b|我叫|我是|叫|"
                      r"名字", re.IGNORECASE)
ZH_NOT_NAME = re.compile(r"出生|生日|健康卡|尾号|后四位|最后|号码|预约|取消|改期|我想|你好|您好|请问|谢谢|医院|门诊|妈妈|女儿|"
                         r"儿子|先生|太太|母亲|父亲|爸爸|打电话|帮我|一下")


@dataclass
class Parsed:
    name: str | None = None  # the words that may be the patient's name (matched against the record by the tool)
    birth_date: str | None = None  # YYYY-MM-DD
    card_last4: str | None = None
    relation: str | None = None
    found: list[str] = field(default_factory=list)  # which pieces this utterance gave


def _spoken_digits(text: str) -> str:
    text = SPOKEN.sub(lambda m: "".join(DIGIT_WORDS[w.lower()] for w in re.findall(r"[a-z]+", m.group(0), re.I)), text)
    return ZH_DIGITS.sub(lambda m: "".join(DIGIT_WORDS[c] for c in m.group(0)), text)


def parse(text: str, *, expecting: bool = True) -> Parsed:
    """`expecting`: the agent asked for the identity. Otherwise (the caller's opening request) a name counts only
    after a cue ("this is", "my name is", "我叫") or beside a birth date or card digits."""
    out = Parsed()
    raw = text or ""
    dob = parse_dob(raw)
    if dob:
        out.birth_date = dob.isoformat()
        out.found.append("birth_date")
    digits = _spoken_digits(raw)
    without_dates = DATES.sub(" ", digits)
    if m := LAST4.search(without_dates):
        out.card_last4 = re.sub(r"\D", "", m.group(1))
    else:
        fours = [f for f in FOUR.findall(without_dates) if not (dob and f == str(dob.year))]
        if len(fours) == 1:
            out.card_last4 = fours[0]
    if out.card_last4:
        out.found.append("card_last4")
    if m := RELATION.search(raw):
        word = (m.group(1) or m.group(3) or "").lower()
        out.relation = RELATION_EN.get(word, word) if m.group(1) else RELATION_ZH.get(m.group(3), m.group(3))
    out.name = _name(raw) if expecting or out.birth_date or out.card_last4 or NAME_CUE.search(raw) else None
    if out.name:
        out.found.append("name")
    return out


def _name(text: str) -> str | None:
    """The name-like rest of the utterance once dates, digits and filler words are gone: the Chinese characters
    left (a name after 我叫 or 妈妈叫 stays inside them), or the remaining English words. It need not be only
    the name: the record's name has to appear in it (findCaller), together with the birth date and card digits."""
    rest = re.sub(r"\d+", " ", DATES.sub(" ", _spoken_digits(text)))
    chinese = " ".join(n for n in CJK_NAME.findall(ZH_NOT_NAME.sub(" ", rest)) if len(n) >= 2)
    english = " ".join(re.findall(r"[A-Za-z][A-Za-z'-]+", FILLER.sub(" ", re.sub(r"[一-鿿]", " ", rest))))
    found = " ".join(x for x in (chinese, english) if x).strip()
    return found or None


def combine(names: list[str]) -> str:
    """The name candidates of several identity turns, newest last, within the tool's 120 characters."""
    joined = " ".join(dict.fromkeys(n for n in names if n))
    return joined[-120:].strip()
