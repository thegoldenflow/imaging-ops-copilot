"""What the imaging phone line's scripted fallback says, in English, French and Simplified Chinese.

The fallback (agent.py ScriptedAgent: the phone line when the model is down) answers in the caller's language: every
sentence is written here by hand in the three languages, as WP8's call centre does (voice_services/phrases.py), and
the placeholders are filled from structured data: the caller's first name, an appointment's exam, time and site, a
site's address, opening hours and parking, the approved preparation instructions (System 9, app/modules/prep). Exam
and site names stay as they are, as in the SMS templates (app/core/templates.py). The English sentences are the
ones the fallback said before it spoke French and Chinese.
"""

from __future__ import annotations

from datetime import datetime

from app.core.models import Site

Phrase = dict[str, str]  # {"en": ..., "fr": ..., "zh": ...}
LANGS = ("en", "fr", "zh")  # the phone line's languages; a patient's other preferred language gets English


def p(en: str, fr: str, zh: str) -> Phrase:
    return {"en": en, "fr": fr, "zh": zh}


def say(phrase: Phrase, lang: str, **values) -> str:
    return phrase.get(lang, phrase["en"]).format(**values)


WEEKDAYS = {"en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
            "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
            "zh": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")}
MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre")


def clock(dt: datetime, lang: str) -> str:
    """"10:30" / "10 h 30" (French: "10 h" on the hour)."""
    if lang == "fr":
        return f"{dt.hour} h {dt:%M}" if dt.minute else f"{dt.hour} h"
    return f"{dt:%H:%M}"


def when(dt: datetime, lang: str) -> str:
    """"Tuesday October 14 at 10:30" / "mardi 14 octobre à 10 h 30" / "10月14日（星期二）10:30"."""
    if lang == "fr":
        day = "1er" if dt.day == 1 else str(dt.day)
        return f"{WEEKDAYS['fr'][dt.weekday()]} {day} {MONTHS_FR[dt.month - 1]} à {clock(dt, 'fr')}"
    if lang == "zh":
        return f"{dt.month}月{dt.day}日（{WEEKDAYS['zh'][dt.weekday()]}）{clock(dt, 'zh')}"
    return dt.strftime("%A %B %d at %H:%M")  # as the phone tools say it


def opening_hours(site: Site, lang: str) -> str:
    """"Monday to Saturday, 7:00 to 21:00" / "du lundi au samedi, de 7 h à 21 h" / "星期一至星期六，7:00至21:00"."""
    days, names = sorted(site.open_days), WEEKDAYS.get(lang, WEEKDAYS["en"])
    first, last = names[days[0]], names[days[-1]]
    if lang == "fr":
        return f"du {first} au {last}, de {site.open_hour} h à {site.close_hour} h"
    if lang == "zh":
        return f"{first}至{last}，{site.open_hour}:00至{site.close_hour}:00"
    return f"{first} to {last}, {site.open_hour}:00 to {site.close_hour}:00"


# A site's parking note (seeded in English): approved translations by the English text; other notes are read in
# English.
PARKING: dict[str, Phrase] = {
    "Free underground parking; entrance on the east side.": p(
        "Free underground parking; entrance on the east side.",
        "Stationnement souterrain gratuit; l'entrée est du côté est.",
        "设有免费地下停车场，入口在东侧。"),
}


def parking(text: str, lang: str) -> str:
    return say(PARKING[text], lang) if text in PARKING else text


def site_names(name: str) -> dict[str, str]:
    """A site's name as the phrases use it: {site}, and in French {le_site} and {au_site} ("L'Eastview Imaging
    Centre", "à l'Eastview Imaging Centre", "au Lakeshore Imaging Centre")."""
    vowel = name[:1].lower() in "aeiouéèêà"
    return {"site": name, "le_site": f"L'{name}" if vowel else f"Le {name}",
            "au_site": f"à l'{name}" if vowel else f"au {name}"}


def options(items: list[str], lang: str) -> str:
    return "；".join(items) if lang == "zh" else ", ".join(items)


SAY: dict[str, Phrase] = {
    # ----- before verification -----
    "not_caught": p("Sorry, I didn't catch that. Please tell me your full name and date of birth.",
                    "Désolé, je n'ai pas bien compris. Veuillez me dire votre nom complet et votre date de naissance.",
                    "抱歉，我没有听清楚。请告诉我您的全名和出生日期。"),
    "ask_name": p("Thank you. And your full name, please?", "Merci. Et votre nom complet, s'il vous plaît ?",
                  "谢谢。请问您的全名是？"),
    "ask_dob": p("Thank you. And your date of birth?", "Merci. Et votre date de naissance ?", "谢谢。请问您的出生日期是？"),
    "identity_retry": p("I couldn't match those details. Please repeat your full name and date of birth.",
                        "Ces renseignements ne correspondent pas à notre dossier. Veuillez répéter votre nom complet et "
                        "votre date de naissance.",
                        "这些信息和我们的记录不符。请再说一遍您的全名和出生日期。"),
    "identity_failed": p("I couldn't verify those details. Let me transfer you to a team member.",
                         "Je n'ai pas pu vérifier ces renseignements. Je vous transfère à un membre de l'équipe.",
                         "无法核实这些信息。现在为您转接工作人员。"),
    "verified": p("Thank you, {first}, you're verified. I see {appointment}. Would you like to reschedule, cancel or "
                  "confirm it, hear the preparation instructions, or get directions?",
                  "Merci, {first}, votre identité est vérifiée. Je vois {appointment}. Voulez-vous le déplacer, "
                  "l'annuler ou le confirmer, entendre les consignes de préparation ou avoir l'adresse ?",
                  "谢谢，{first}，身份已核实。查到{appointment}。您想改期、取消还是确认这个预约？也可以听检查前的准备须知，或者问地址。"),
    "verified_none": p("Thank you, {first}. I don't see any upcoming appointments. Can I help with anything else?",
                       "Merci, {first}. Je ne vois aucun rendez-vous à venir. Puis-je vous aider avec autre chose ?",
                       "谢谢，{first}。没有查到您即将到来的预约。还有其他需要帮忙的吗？"),
    "appointment": p("your {exam} on {when} at {site}", "votre {exam} du {when}, {au_site}",
                     "您{when}在{site}的{exam}检查"),
    # ----- put through to a person -----
    "transfer_person": p("Of course. I'm transferring you to a member of our front desk team now.",
                         "Bien sûr. Je vous transfère maintenant à un membre de notre équipe d'accueil.",
                         "好的，现在为您转接前台工作人员。"),
    "transfer_medical": p("I'm not able to answer medical questions, but our staff can help. "
                          "I'm transferring you to a team member now.",
                          "Je ne peux pas répondre aux questions médicales, mais notre personnel peut vous aider. "
                          "Je vous transfère maintenant à un membre de l'équipe.",
                          "我不能回答医疗方面的问题，但我们的工作人员可以帮您。现在为您转接工作人员。"),
    # ----- the caller's requests -----
    "no_appointment": p("I don't see an upcoming appointment for you. Would you like me to transfer you to a team "
                        "member?",
                        "Je ne vois aucun rendez-vous à venir à votre nom. Voulez-vous que je vous transfère à un "
                        "membre de l'équipe ?",
                        "没有查到您即将到来的预约。需要为您转接工作人员吗？"),
    "confirm_cancel": p("Just to confirm, you'd like to cancel {appointment}?",
                        "Pour confirmer : vous voulez annuler {appointment} ?", "跟您核对一下：要取消{appointment}，对吗？"),
    "cancelled": p("Done. Your appointment is cancelled and you'll receive a text confirmation. "
                   "Is there anything else I can help with?",
                   "C'est fait. Votre rendez-vous est annulé et vous recevrez une confirmation par texto. "
                   "Puis-je vous aider avec autre chose ?",
                   "好的，您的预约已取消，稍后会收到短信通知。还有其他需要帮忙的吗？"),
    "kept": p("Okay, I've left your appointment as it is. Anything else?",
              "D'accord, je n'ai pas touché à votre rendez-vous. Autre chose ?", "好的，您的预约保持不变。还有其他需要吗？"),
    "no_slots": p("I couldn't find open times in the next week. Let me transfer you to a team member.",
                  "Je n'ai pas trouvé de plage libre la semaine prochaine. Je vous transfère à un membre de l'équipe.",
                  "未来一周没有找到空余时间。现在为您转接工作人员。"),
    "offer": p("I have {options}. Which would you like?", "J'ai {options}. Laquelle voulez-vous ?",
               "可以改到这些时间：{options}。您想选哪一个？"),
    "offer_item": p("option {n}, {when}", "option {n}, {when}", "第{n}个，{when}"),
    "pick_again": p("Sorry, which option would you like: the first, second, or third?",
                    "Désolé, quelle option voulez-vous : la première, la deuxième ou la troisième ?",
                    "抱歉，您想选第一个、第二个还是第三个？"),
    "confirm_move": p("Just to confirm: move your appointment to {when}?",
                      "Pour confirmer : je déplace votre rendez-vous au {when} ?", "跟您核对一下：把预约改到{when}，对吗？"),
    "moved": p("You're all set for {appointment}. You'll get a confirmation text. Anything else?",
               "C'est noté pour {appointment}. Vous recevrez une confirmation par texto. Autre chose ?",
               "已为您改好：{appointment}。稍后会收到短信通知。还有其他需要吗？"),
    "not_moved": p("No problem, I haven't changed anything. Anything else?",
                   "Pas de problème, je n'ai rien changé. Autre chose ?", "没问题，我没有做任何更改。还有其他需要吗？"),
    "prep": p("{text} I've also sent these instructions to you by text.",
              "{text} Je vous ai aussi envoyé ces consignes par texto.", "{text}这些须知也已经用短信发给您了。"),
    # System 9: without an approved translation the caller hears the approved English text, and why.
    "prep_in_english": p("These instructions are not approved in your language yet, so I'll read them in English.",
                         "Ces consignes ne sont pas encore approuvées en français, je vous les lis donc en anglais.",
                         "这项检查的准备须知还没有审核通过的中文版本，下面用英文为您读一遍。"),
    "site": p("{site} is at {address}, open {hours}. {parking}",
              "{le_site} se trouve au {address}. Heures d'ouverture : {hours}. {parking}",
              "{site}的地址是{address}，营业时间是{hours}。{parking}"),
    "confirmed": p("Your appointment is confirmed. Anything else?", "Votre rendez-vous est confirmé. Autre chose ?",
                   "您的预约已确认。还有其他需要吗？"),
    "confirm_blocked": p("I can't confirm this appointment yet because a safety screening still needs staff review. "
                         "Our team will call you. Anything else?",
                         "Je ne peux pas encore confirmer ce rendez-vous : un questionnaire de sécurité doit d'abord "
                         "être vérifié par notre personnel. Notre équipe vous appellera. Autre chose ?",
                         "这个预约暂时还不能确认，因为安全筛查还需要工作人员审核。我们的工作人员会给您打电话。还有其他需要吗？"),
    "menu": p("I can help you reschedule, cancel or confirm an appointment, give preparation instructions or "
              "directions, or transfer you to a person.",
              "Je peux déplacer, annuler ou confirmer un rendez-vous, vous donner les consignes de préparation ou "
              "l'adresse, ou vous transférer à une personne.",
              "我可以帮您改期、取消或确认预约，告诉您检查前的准备须知或地址，也可以为您转接工作人员。"),
    "goodbye": p("Thank you for calling Lakeshore Imaging. Goodbye!", "Merci d'avoir appelé Lakeshore Imaging. Au revoir !",
                 "感谢您致电Lakeshore Imaging，再见！"),
}
