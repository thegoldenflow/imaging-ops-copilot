"""What a caller asks for, by keyword, in English, French and Chinese, and which of the three they speak.

The rules of the scripted fallback (agent.py: the phone line when the model is down) and of the phone agent's mock
model (mock_agent.py: its eval without a key). A medical question or a request for a person is put through to staff
in every language, so `medical` errs on the side of matching. French words match with or without accents (speech
to text may drop them).
"""

from __future__ import annotations

import re

# rule -> {language: words}; a rule matches in any language.
WORDS = {
    "human": {"en": r"\b(human|person|someone|agent|operator|representative|staff)\b",
              "fr": r"quelqu'un|une personne|un humain",
              "zh": r"人工|真人|工作人员"},
    "medical": {"en": r"\b(pain|hurt|symptom|bleed|dizzy|result|diagnos|cancer|tumou?r|safe|dangerous|medication|"
                      r"medicine|pregnan|allerg|worried|radiation|side effect)\w*|is it normal",
                "fr": r"douleur|j'ai mal|sympt[oô]me|r[ée]sultat|dangereu|m[ée]dicament|enceinte|grossesse|inqui[eè]t|"
                      r"saign|tumeur|effets? secondaires?",
                "zh": r"疼|痛|结果|诊断|癌|肿瘤|安全|危险|药|怀孕|过敏|担心|症状|出血|头晕|正常吗|辐射|副作用"},
    "cancel": {"en": r"\bcancel\w*", "fr": r"\bannul\w*", "zh": r"取消"},
    "reschedule": {"en": r"\b(reschedul\w*|move|change|another (day|time)|different (day|time))\b",
                   "fr": r"d[ée]placer|changer|reporter|modifier|un autre (jour|moment)|une autre (date|heure)",
                   "zh": r"改约|改期|改时间|换个时间|换时间|推迟|延期"},
    "confirm": {"en": r"\bconfirm\b", "fr": r"\bconfirme(r|z)?\b", "zh": r"确认"},
    "prep": {"en": r"\b(prep\w*|instruction\w*|eat|fast\w*|drink|bring|wear)\b",
             "fr": r"pr[ée]par\w*|consigne|[àa] jeun|manger|boire|apporter",
             "zh": r"须知|准备|空腹|注意事项"},
    "site": {"en": r"\b(where|address|direction\w*|hours|open|close|parking|located)\b",
             "fr": r"adresse|horaire|heures d'ouverture|ouvert|stationnement|\boù\b",
             "zh": r"地址|营业|停车|在哪|怎么走|开门"},
    "yes": {"en": r"\b(yes|yeah|yep|correct|sure|please do|go ahead|ok(ay)?)\b",
            "fr": r"\boui\b|d'accord|parfait|exactement|allez-y",
            "zh": r"是|好|对|可以|行"},
    "no": {"en": r"\b(no|nope|don't|do not|never mind|keep)\b", "fr": r"\bnon\b|pas besoin|laissez|gardez",
           "zh": r"不|别|算了"},
    "done": {"en": r"\b(that's all|that is all|nothing else|bye|goodbye|thank you|thanks)\b",
             "fr": r"c'est tout|au revoir|merci|bonne journ[ée]e",
             "zh": r"再见|谢谢|没有了|没事了|就这样"},
}
RULES = {k: re.compile("|".join(v.values()), re.I) for k, v in WORDS.items()}
TASKS = ("cancel", "reschedule", "prep", "site", "confirm")  # what a caller can ask the phone line to do
ORDINAL_WORDS = [(re.compile(r"\bfirst\b|premi[eè]re?|第一|第1"), 0), (re.compile(r"\bsecond\b|deuxi[eè]me|第二|第2"), 1),
                 (re.compile(r"\bthird\b|troisi[eè]me|第三|第3"), 2)]
ORDINAL_NUMBERS = [(re.compile(r"\b(1|one)\b|(option|num[ée]ro) un\b"), 0), (re.compile(r"\b(2|two|deux)\b"), 1),
                   (re.compile(r"\b(3|three|trois)\b"), 2)]

# Which language a caller speaks: Chinese characters, else the French or English words of the rules above plus
# the small words only a French or an English sentence has (greetings, pronouns, articles, a birth date's month).
# A name, a number or "OK" says nothing: words both languages use count for neither.
CHINESE = re.compile(r"[一-鿿]")
SPOKEN = {
    "en": r"\b(hi|hello|hey|i|i'm|i'd|i'll|my|the|an|is|are|was|it|it's|this|that|that's|there|you|your|what|when|"
          r"how|can|could|would|will|do|does|have|please|want|need|like|to|for|of|and|with|in|at|appointment|born|"
          r"name|january|february|march|april|may|june|july|august|september|october|november|december)\b",
    "fr": r"\b(bonjour|bonsoir|all[ôo]|je|j'|m'appelle|moi|il|elle|nous|vous|votre|vos|mon|ma|mes|ce|ça|ici|y|le|la|"
          r"les|l'|une?|des|du|de|au|aux|est|c'est|est-ce|qu'|n'|ne|pas|pour|avec|quand|quel(le)?s?|rendez-vous|"
          r"examen|n[ée]e?|s'il|janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|"
          r"d[ée]cembre)\b",
}
SHARED = re.compile(r"\b(ok(ay)?|parking|agent|cancer|radiation|directions?|instructions?|preparation|diagnosti\w*|"
                    r"allergi\w*|symptomes?|resultats?)\b")
LANGUAGE_WORDS = {lang: re.compile("|".join([SPOKEN[lang], *(w[lang] for w in WORDS.values())]), re.I)
                  for lang in SPOKEN}


def _plain(text: str) -> str:
    return text.lower().replace("’", "'")  # speech to text may write j’ai, that’s


def has(rule: str, text: str) -> bool:
    return bool(RULES[rule].search(_plain(text)))


def language(text: str) -> str | None:
    """The language of a caller's turn (en, fr or zh), or None when it gives no sign: Chinese when it has Chinese
    characters, else French or English by which has more words in it."""
    t = _plain(text)
    if CHINESE.search(t):
        return "zh"
    t = SHARED.sub(" ", t)
    fr, en = (len(LANGUAGE_WORDS[lang].findall(t)) for lang in ("fr", "en"))
    return "fr" if fr > en else "en" if en > fr else None


def ordinal(text: str) -> int | None:
    """The option a caller picks (0-based): "the second one", "la deuxième", "第二个", "option 2"."""
    for rules in (ORDINAL_WORDS, ORDINAL_NUMBERS):  # "the second one" is option 2
        for rx, index in rules:
            if rx.search(_plain(text)):
                return index
    return None
