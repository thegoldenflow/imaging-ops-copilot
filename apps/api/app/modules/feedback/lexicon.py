"""Themes, the rule-based baseline behind the mock provider, and synthetic
seed comments in English, French, Chinese and Punjabi (all fictional)."""

import re

THEMES = {
    "wait_time": "Wait time",
    "staff_attitude": "Staff attitude",
    "environment": "Environment",
    "billing": "Billing",
    "scheduling": "Scheduling",
    "communication": "Communication",
    "parking_access": "Parking and access",
    "results": "Results",
}
SENTIMENTS = ("positive", "neutral", "negative")

THEME_WORDS = {
    "wait_time": ["wait", "late", "delay", "on time", "quick", "minutes", "hour", "attente", "attendu", "retard", "rapide",
                  "à l'heure", "等了", "等待", "排队", "准时", "很快", "ਉਡੀਕ", "ਦੇਰ", "ਜਲਦੀ", "ਸਮੇਂ ਸਿਰ"],
    "staff_attitude": ["staff", "technologist", "receptionist", "front desk", "nurse", "friendly", "rude", "kind", "polite",
                       "personnel", "technologue", "réceptionniste", "aimable", "impoli", "gentil", "工作人员", "技师", "前台",
                       "护士", "态度", "热情", "耐心", "ਸਟਾਫ", "ਟੈਕਨੋਲੋਜਿਸਟ", "ਰਿਸੈਪਸ਼ਨ", "ਰੁੱਖਾ", "ਮਿਹਰਬਾਨ"],
    "environment": ["clean", "dirty", "cold", "waiting room", "comfortable", "noisy", "propre", "sale", "froid",
                    "salle d'attente", "confortable", "干净", "脏", "冷", "环境", "舒适", "吵", "ਸਾਫ਼", "ਗੰਦਾ", "ਠੰਡ", "ਕਮਰਾ"],
    "billing": ["bill", "charge", "invoice", "insurance", "payment", "fee", "facture", "frais", "assurance", "payer",
                "收费", "账单", "费用", "保险", "付款", "ਬਿੱਲ", "ਫੀਸ", "ਪੈਸੇ", "ਬੀਮਾ"],
    "scheduling": ["book", "appointment", "reschedul", "phone line", "call back", "rendez-vous", "réserver", "reporté",
                   "预约", "改期", "ਅਪੌਇੰਟਮੈਂਟ", "ਬੁਕਿੰਗ"],
    "communication": ["explain", "instructions", "information", "told", "interpreter", "translat", "expliqué", "consignes",
                      "langue", "解释", "说明", "须知", "中文", "翻译", "告诉", "ਸਮਝਾਇਆ", "ਹਦਾਇਤਾਂ", "ਪੰਜਾਬੀ", "ਜਾਣਕਾਰੀ"],
    "parking_access": ["parking", "wheelchair", "elevator", "entrance", "stationnement", "ascenseur", "fauteuil",
                       "停车", "车位", "轮椅", "电梯", "入口", "ਪਾਰਕਿੰਗ", "ਵ੍ਹੀਲਚੇਅਰ", "ਲਿਫਟ"],
    "results": ["result", "report", "résultat", "rapport", "结果", "报告", "ਨਤੀਜ", "ਰਿਪੋਰਟ"],
}
POSITIVE = ["friendly", "kind", "great", "excellent", "thank", "quick", "clean", "helpful", "easy", "on time", "comfortable",
            "merci", "aimable", "rapide", "propre", "gentil", "facile", "谢谢", "很好", "满意", "干净", "热情", "耐心", "很快",
            "准时", "方便", "ਧੰਨਵਾਦ", "ਵਧੀਆ", "ਚੰਗਾ", "ਚੰਗੀ", "ਮਿਹਰਬਾਨ", "ਸਾਫ਼", "ਜਲਦੀ"]
NEGATIVE = ["rude", "dirty", "terrible", "disappointed", "confusing", "no one", "wrong", "overcharged", "never", "too long",
            "over an hour", "cold", "couldn't", "could not", "not ", "impoli", "sale", "déçu", "jamais", "trop long", "froid",
            "pas ", "confus", "不", "太久", "差", "失望", "脏", "没有人", "没人", "冷", "ਨਹੀਂ", "ਮਾੜਾ", "ਬਹੁਤ ਦੇਰ", "ਗੰਦਾ",
            "ਰੁੱਖਾ", "ਠੰਡ"]


def baseline(rating: int, comment: str) -> dict:
    """Keyword classifier used by the mock provider and for seeded responses."""
    text = comment.lower()
    themes = [t for t, words in THEME_WORDS.items() if any(w in text for w in words)]
    pos = sum(text.count(w) for w in POSITIVE)
    neg = sum(text.count(w) for w in NEGATIVE)
    score = pos - neg + (rating - 3) * 1.5
    sentiment = "positive" if score > 0.5 else "negative" if score < -0.5 else "neutral"
    if rating <= 2:
        sentiment = "negative"
    first = re.split(r"(?<=[.!?。！？])\s*", comment.strip())[0][:160]
    return {"sentiment": sentiment, "themes": themes, "summary_en": first or "(no comment)"}


# (language, rating, comment) for the seed. Themes and sentiment come from the baseline.
SEED_COMMENTS = [
    ("en", 5, "The technologist was friendly and explained every step. In and out in 30 minutes."),
    ("en", 4, "Quick and easy, staff were polite. Parking was a bit hard to find."),
    ("en", 2, "Waited over an hour past my appointment time and no one told us why."),
    ("en", 1, "The receptionist was rude when I asked about my bill."),
    ("en", 5, "Clean, comfortable waiting room and they were on time."),
    ("en", 3, "Fine overall. The prep instructions could be clearer."),
    ("en", 2, "I was charged a fee nobody mentioned when I booked."),
    ("en", 4, "Booking online was easy and I got a reminder."),
    ("en", 2, "My doctor still has not received the report after a week."),
    ("en", 1, "The waiting room was dirty and cold."),
    ("fr", 5, "Personnel très aimable, merci beaucoup."),
    ("fr", 4, "Rapide et efficace, la salle d'attente était propre."),
    ("fr", 2, "Attente trop longue, plus d'une heure de retard."),
    ("fr", 3, "Correct, mais le stationnement est difficile."),
    ("fr", 1, "La réceptionniste était impolie et les consignes en français étaient confuses."),
    ("fr", 5, "Le technologue m'a tout expliqué. Excellent service."),
    ("zh", 5, "技师很耐心，解释得很清楚，谢谢。"),
    ("zh", 4, "预约方便，准时开始。"),
    ("zh", 2, "等了一个多小时，太久了，也没人解释。"),
    ("zh", 1, "前台态度差，收费也不清楚。"),
    ("zh", 3, "还可以，就是停车位太少。"),
    ("zh", 5, "环境干净，有中文须知，很满意。"),
    ("zh", 2, "检查须知没有中文，看不懂。"),
    ("pa", 5, "ਸਟਾਫ ਬਹੁਤ ਮਿਹਰਬਾਨ ਸੀ, ਧੰਨਵਾਦ।"),
    ("pa", 4, "ਜਲਦੀ ਹੋ ਗਿਆ, ਕਮਰਾ ਸਾਫ਼ ਸੀ।"),
    ("pa", 2, "ਬਹੁਤ ਦੇਰ ਉਡੀਕ ਕਰਨੀ ਪਈ।"),
    ("pa", 1, "ਰਿਸੈਪਸ਼ਨ ਤੇ ਰੁੱਖਾ ਵਿਹਾਰ, ਬਿੱਲ ਬਾਰੇ ਕੋਈ ਜਾਣਕਾਰੀ ਨਹੀਂ।"),
    ("pa", 3, "ਠੀਕ ਸੀ, ਪਰ ਪਾਰਕਿੰਗ ਨਹੀਂ ਮਿਲੀ।"),
    ("pa", 5, "ਹਦਾਇਤਾਂ ਪੰਜਾਬੀ ਵਿੱਚ ਸਨ, ਬਹੁਤ ਵਧੀਆ।"),
]
