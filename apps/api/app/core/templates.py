"""Patient-facing message templates.

Translations are pre-written and stored here (never generated at send time).
In this demo they are marked as approved; a real deployment would have clinical
staff review each translation before it can be sent.
"""

from datetime import datetime

LANGUAGES = {"en": "English", "fr": "Français", "zh": "中文", "pa": "ਪੰਜਾਬੀ"}

TEMPLATES: dict[str, dict[str, str]] = {
    "reminder": {
        "en": "Reminder: your {exam} is on {when} at {site}. Reply C to confirm or X to cancel.",
        "fr": "Rappel : votre {exam} est prévu le {when} à {site}. Répondez C pour confirmer ou X pour annuler.",
        "zh": "提醒：您的{exam}检查安排在{when}，地点{site}。回复 C 确认，回复 X 取消。",
        "pa": "ਯਾਦ ਦਿਵਾਉਣਾ: ਤੁਹਾਡਾ {exam} {when} ਨੂੰ {site} ਵਿਖੇ ਹੈ। ਪੁਸ਼ਟੀ ਲਈ C ਜਾਂ ਰੱਦ ਕਰਨ ਲਈ X ਭੇਜੋ।",
    },
    "waitlist_offer": {
        "en": "A {exam} opening is available on {when} at {site}. Reply YES to book. The first patient to reply gets the slot.",
        "fr": "Une place pour {exam} s'est libérée le {when} à {site}. Répondez OUI pour réserver. Premier arrivé, premier servi.",
        "zh": "{site}在{when}空出一个{exam}检查时段。回复 YES 即可预约，先确认者得。",
        "pa": "{site} ਵਿਖੇ {when} ਨੂੰ {exam} ਲਈ ਸਮਾਂ ਖਾਲੀ ਹੋਇਆ ਹੈ। ਬੁੱਕ ਕਰਨ ਲਈ YES ਭੇਜੋ। ਪਹਿਲਾਂ ਜਵਾਬ ਦੇਣ ਵਾਲੇ ਨੂੰ ਸਮਾਂ ਮਿਲੇਗਾ।",
    },
    "offer_filled": {
        "en": "Thank you. The {exam} opening on {when} has already been filled. You remain on the waitlist.",
        "fr": "Merci. La place pour {exam} le {when} a déjà été attribuée. Vous restez sur la liste d'attente.",
        "zh": "谢谢。{when}的{exam}时段已被预约，您仍在候补名单上。",
        "pa": "ਧੰਨਵਾਦ। {when} ਵਾਲਾ {exam} ਸਮਾਂ ਪਹਿਲਾਂ ਹੀ ਭਰ ਗਿਆ ਹੈ। ਤੁਸੀਂ ਉਡੀਕ ਸੂਚੀ ਵਿੱਚ ਹੋ।",
    },
    "booking_confirmation": {
        "en": "Booked: {exam} on {when} at {site}, {address}. Please complete pre-registration: {link}",
        "fr": "Réservé : {exam} le {when} à {site}, {address}. Veuillez remplir la préinscription : {link}",
        "zh": "预约成功：{when}在{site}（{address}）做{exam}。请填写网上预登记：{link}",
        "pa": "ਬੁੱਕ ਹੋ ਗਿਆ: {exam} {when} ਨੂੰ {site}, {address} ਵਿਖੇ। ਕਿਰਪਾ ਕਰਕੇ ਪ੍ਰੀ-ਰਜਿਸਟ੍ਰੇਸ਼ਨ ਪੂਰੀ ਕਰੋ: {link}",
    },
    "mri_screening": {
        "en": "Before your MRI, please complete the safety questionnaire: {link}",
        "fr": "Avant votre IRM, veuillez remplir le questionnaire de sécurité : {link}",
        "zh": "做核磁共振（MRI）前，请填写安全问卷：{link}",
        "pa": "ਤੁਹਾਡੇ MRI ਤੋਂ ਪਹਿਲਾਂ, ਕਿਰਪਾ ਕਰਕੇ ਸੁਰੱਖਿਆ ਪ੍ਰਸ਼ਨਾਵਲੀ ਭਰੋ: {link}",
    },
    "cancel_confirmation": {
        "en": "Your {exam} on {when} has been cancelled. Call us to rebook.",
        "fr": "Votre {exam} du {when} a été annulé. Appelez-nous pour reprendre rendez-vous.",
        "zh": "您{when}的{exam}检查已取消。如需重新预约，请致电我们。",
        "pa": "ਤੁਹਾਡਾ {when} ਵਾਲਾ {exam} ਰੱਦ ਕਰ ਦਿੱਤਾ ਗਿਆ ਹੈ। ਦੁਬਾਰਾ ਬੁੱਕ ਕਰਨ ਲਈ ਸਾਨੂੰ ਫ਼ੋਨ ਕਰੋ।",
    },
}

PREP: dict[str, dict[str, str]] = {
    "fasting": {
        "en": "Do not eat or drink for 8 hours before your exam. You may take regular medication with a sip of water.",
        "fr": "Ne mangez ni ne buvez rien 8 heures avant l'examen. Vous pouvez prendre vos médicaments habituels avec une gorgée d'eau.",
        "zh": "检查前 8 小时请勿进食或饮水。常规药物可用少量水送服。",
        "pa": "ਜਾਂਚ ਤੋਂ 8 ਘੰਟੇ ਪਹਿਲਾਂ ਕੁਝ ਨਾ ਖਾਓ ਜਾਂ ਪੀਓ। ਆਮ ਦਵਾਈ ਥੋੜ੍ਹੇ ਪਾਣੀ ਨਾਲ ਲੈ ਸਕਦੇ ਹੋ।",
    },
    "contrast": {
        "en": "This exam uses contrast dye. Arrive 30 minutes early, drink water the day before, and tell staff about kidney problems, diabetes medication or past contrast reactions.",
        "fr": "Cet examen utilise un produit de contraste. Arrivez 30 minutes en avance, buvez de l'eau la veille et signalez tout problème rénal, médicament contre le diabète ou réaction antérieure au contraste.",
        "zh": "本检查需使用造影剂。请提前 30 分钟到达，检查前一天多喝水；如有肾病、正在服用糖尿病药物或曾对造影剂过敏，请告知工作人员。",
        "pa": "ਇਸ ਜਾਂਚ ਵਿੱਚ ਕੰਟ੍ਰਾਸਟ ਦਵਾਈ ਵਰਤੀ ਜਾਂਦੀ ਹੈ। 30 ਮਿੰਟ ਪਹਿਲਾਂ ਆਓ, ਇੱਕ ਦਿਨ ਪਹਿਲਾਂ ਪਾਣੀ ਪੀਓ, ਅਤੇ ਗੁਰਦੇ ਦੀ ਸਮੱਸਿਆ, ਸ਼ੂਗਰ ਦੀ ਦਵਾਈ ਜਾਂ ਪਹਿਲਾਂ ਹੋਈ ਕੰਟ੍ਰਾਸਟ ਪ੍ਰਤੀਕਿਰਿਆ ਬਾਰੇ ਸਟਾਫ਼ ਨੂੰ ਦੱਸੋ।",
    },
    "mri": {
        "en": "Remove all metal objects. Tell staff about any pacemaker, implant, or metal in your body. Arrive 15 minutes early.",
        "fr": "Retirez tout objet métallique. Signalez tout stimulateur cardiaque, implant ou métal dans votre corps. Arrivez 15 minutes en avance.",
        "zh": "请取下所有金属物品。如体内有心脏起搏器、植入物或金属，请告知工作人员。请提前 15 分钟到达。",
        "pa": "ਸਾਰੀਆਂ ਧਾਤ ਦੀਆਂ ਚੀਜ਼ਾਂ ਉਤਾਰ ਦਿਓ। ਪੇਸਮੇਕਰ, ਇਮਪਲਾਂਟ ਜਾਂ ਸਰੀਰ ਵਿੱਚ ਧਾਤ ਬਾਰੇ ਸਟਾਫ਼ ਨੂੰ ਦੱਸੋ। 15 ਮਿੰਟ ਪਹਿਲਾਂ ਆਓ।",
    },
    "full_bladder": {
        "en": "Drink 1 litre of water one hour before your exam and do not empty your bladder.",
        "fr": "Buvez 1 litre d'eau une heure avant l'examen et ne videz pas votre vessie.",
        "zh": "检查前 1 小时喝 1 升水，并保持膀胱充盈，不要排尿。",
        "pa": "ਜਾਂਚ ਤੋਂ ਇੱਕ ਘੰਟਾ ਪਹਿਲਾਂ 1 ਲੀਟਰ ਪਾਣੀ ਪੀਓ ਅਤੇ ਪਿਸ਼ਾਬ ਨਾ ਕਰੋ।",
    },
    "none": {
        "en": "No special preparation. Wear comfortable clothing without metal and arrive 10 minutes early.",
        "fr": "Aucune préparation particulière. Portez des vêtements confortables sans métal et arrivez 10 minutes en avance.",
        "zh": "无需特别准备。请穿无金属的舒适衣物，提前 10 分钟到达。",
        "pa": "ਕੋਈ ਖ਼ਾਸ ਤਿਆਰੀ ਨਹੀਂ। ਬਿਨਾਂ ਧਾਤ ਵਾਲੇ ਆਰਾਮਦਾਇਕ ਕੱਪੜੇ ਪਾਓ ਅਤੇ 10 ਮਿੰਟ ਪਹਿਲਾਂ ਆਓ।",
    },
}

PREP_FOR_EXAM = {
    "US_ABD": "fasting",
    "US_PELVIS": "full_bladder",
    "CT_CHEST_C": "contrast",
    "CT_ABD_PEL": "contrast",
    "MR_BRAIN": "mri",
    "MR_LSPINE": "mri",
    "MR_KNEE": "mri",
}


def render(kind: str, language: str, **values) -> str:
    templates = TEMPLATES[kind]
    return templates.get(language, templates["en"]).format(**values)


def prep_text(exam_code: str, language: str) -> str:
    texts = PREP[PREP_FOR_EXAM.get(exam_code, "none")]
    return texts.get(language, texts["en"])


def format_when(dt: datetime, language: str) -> str:
    if language == "zh":
        return f"{dt.month}月{dt.day}日 {dt:%H:%M}"
    return f"{dt:%a %b %d, %H:%M}"
