"""System 19 · Patient Feedback.

When an exam is completed the patient gets a short satisfaction survey (rating
plus free text) by SMS in their preferred language. Claude classifies each
comment's sentiment and themes; staff confirm or correct the labels. A low
rating (1-2 stars, no AI needed) or a negative AI reading notifies the site
manager right away. Ratings and themes are shown by site and week."""

import random
import secrets
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.core.models import Appointment, AppointmentStatus, ImagingStudy, MessageOutbox
from app.core.store import Store
from app.llm.gateway import get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture
from app.modules.feedback.lexicon import SEED_COMMENTS, SENTIMENTS, THEMES, baseline
from app.modules.scheduling import service as scheduling

LOW_RATING = 2
SURVEY_TEXT = {
    "en": "Thank you for visiting {site}. How was your {exam}? Tell us in 1 minute: {link}",
    "fr": "Merci de votre visite à {site}. Comment s'est passé votre examen ({exam}) ? Répondez en 1 minute : {link}",
    "zh": "感谢您到{site}就诊。您的{exam}检查体验如何？一分钟告诉我们：{link}",
    "pa": "{site} ਆਉਣ ਲਈ ਧੰਨਵਾਦ। ਤੁਹਾਡਾ {exam} ਕਿਵੇਂ ਰਿਹਾ? 1 ਮਿੰਟ ਵਿੱਚ ਦੱਸੋ: {link}",
}
SITE_MANAGERS = {"LKS": "Morgan Hale", "NGT": "Riley Chen", "WBK": "Taylor Brooks", "EVW": "Jamie Ortiz", "MTN": "Avery Singh"}

ThemeKey = Literal["wait_time", "staff_attitude", "environment", "billing", "scheduling", "communication",
                   "parking_access", "results"]


class Classification(BaseModel):
    sentiment: Literal["positive", "neutral", "negative"]
    themes: list[ThemeKey]
    summary_en: str = Field(description="One short sentence in English")


CLASSIFY_PROMPT = Prompt(
    name="feedback_classify",
    version="feedback_classify@1",
    system=(
        "You label patient satisfaction comments for an outpatient imaging clinic. The comment may be in English, "
        "French, Chinese or Punjabi. Give the overall sentiment (positive, neutral, negative), every theme the comment "
        f"actually mentions from: {', '.join(THEMES)}, and a one-sentence English summary. Use an empty theme list if "
        "none applies. Staff review every label."
    ),
    template="Star rating: {rating}/5\nComment:\n{comment}",
)


@mock_fixture("feedback_classify")
def _mock(text: str, images: list, attempt: int) -> dict:
    rating = int(text.split("Star rating: ", 1)[1][0])
    return baseline(rating, text.split("Comment:\n", 1)[1])


class Survey(BaseModel):
    id: str
    token: str
    appointment_id: str
    patient_id: str
    site_id: str
    exam_code: str
    language: str
    sent_at: datetime
    status: str = "sent"  # sent, answered


class FeedbackResponse(BaseModel):  # FHIR QuestionnaireResponse (satisfaction survey)
    id: str
    survey_id: str | None
    patient_id: str
    site_id: str
    exam_code: str
    language: str
    rating: int = Field(ge=1, le=5)
    comment: str
    submitted_at: datetime
    ai_status: str | None = None  # None = waiting for classification; ok, needs_human, unavailable, seeded
    ai_sentiment: str | None = None
    ai_themes: list[str] = []
    ai_summary: str | None = None
    llm_call_id: str | None = None
    model: str | None = None
    sentiment: str | None = None  # confirmed by staff (or the AI label until then)
    themes: list[str] = []
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class Alert(BaseModel):
    id: str
    response_id: str
    site_id: str
    reason: str
    created_at: datetime
    notified: str  # who was told
    status: str = "open"  # open, followed_up
    follow_up: str | None = None
    followed_up_by: str | None = None
    followed_up_at: datetime | None = None


def surveys(store: Store) -> dict[str, Survey]:
    return store.module("feedback_surveys", dict)


def responses(store: Store) -> dict[str, FeedbackResponse]:
    return store.module("feedback_responses", dict)


def alerts(store: Store) -> dict[str, Alert]:
    return store.module("feedback_alerts", dict)


def survey_by_token(store: Store, token: str) -> Survey | None:
    return next((s for s in list(surveys(store).values()) if s.token == token), None)


def send_survey(store: Store, appt: Appointment, now: datetime) -> Survey:
    patient = store.patients[appt.patient_id]
    lang = patient.preferred_language if patient.preferred_language in SURVEY_TEXT else "en"
    survey = Survey(id=store.next_id("SRV"), token=secrets.token_urlsafe(8), appointment_id=appt.id,
                    patient_id=patient.id, site_id=appt.site_id, exam_code=appt.exam_code, language=lang, sent_at=now)
    surveys(store)[survey.id] = survey
    body = SURVEY_TEXT[lang].format(site=store.sites[appt.site_id].name, exam=store.exams[appt.exam_code].name,
                                    link=f"/feedback/{survey.token}")
    msg = MessageOutbox(id=store.next_id("MSG"), channel="sms", kind="feedback_survey", patient_id=patient.id,
                        to=patient.phone, language=lang, body=body, appointment_id=appt.id, scheduled_for=now)
    store.outbox[msg.id] = msg
    return survey


def on_completed(store: Store, appt: Appointment, study: ImagingStudy) -> None:
    # Sent right away in the demo; a clinic would wait a couple of hours.
    send_survey(store, appt, datetime.now())


scheduling.COMPLETION_HOOKS.append(on_completed)


def _alert(store: Store, resp: FeedbackResponse, reason: str, now: datetime, notify: bool = True) -> Alert | None:
    if any(a.response_id == resp.id for a in alerts(store).values()):
        return None
    manager = SITE_MANAGERS.get(resp.site_id, "Site manager")
    alert = Alert(id=store.next_id("FBA"), response_id=resp.id, site_id=resp.site_id, reason=reason, created_at=now,
                  notified=f"{manager} (site manager, {resp.site_id})")
    alerts(store)[alert.id] = alert
    if notify:
        # Staff-facing message: no patient name, a pointer to the dashboard instead.
        msg = MessageOutbox(
            id=store.next_id("MSG"), channel="email", kind="feedback_alert", patient_id=None,
            to=f"manager.{resp.site_id.lower()}@example.com", language="en", scheduled_for=now,
            body=(f"Negative patient feedback at {store.sites[resp.site_id].name} ({resp.rating}/5, {reason}). "
                  f"Response {resp.id}: please follow up from the Patient feedback page within one business day."),
        )
        store.outbox[msg.id] = msg
    return alert


def submit(store: Store, survey: Survey, rating: int, comment: str, now: datetime) -> FeedbackResponse:
    resp = FeedbackResponse(id=store.next_id("FB"), survey_id=survey.id, patient_id=survey.patient_id,
                            site_id=survey.site_id, exam_code=survey.exam_code, language=survey.language, rating=rating,
                            comment=comment.strip()[:2000], submitted_at=now)
    responses(store)[resp.id] = resp
    survey.status = "answered"
    if rating <= LOW_RATING:  # rule-based, works without AI
        _alert(store, resp, f"{rating}-star rating", now)
    return resp


def classify(store: Store, resp: FeedbackResponse) -> None:
    patient = store.patients.get(resp.patient_id)
    outcome = get_gateway().structured(
        task="feedback_classify", prompt=CLASSIFY_PROMPT,
        variables={"rating": resp.rating, "comment": resp.comment or "(no comment)"},
        schema_cls=Classification, tier="fast", patients=[patient] if patient else None,
    )
    resp.ai_status, resp.llm_call_id, resp.model = outcome.status, outcome.call_id, outcome.model
    if outcome.status == "ok":
        resp.ai_sentiment, resp.ai_themes = outcome.data["sentiment"], outcome.data["themes"]
        resp.ai_summary = outcome.data["summary_en"]
        if not resp.confirmed_by:
            resp.sentiment, resp.themes = resp.ai_sentiment, list(resp.ai_themes)
        if resp.ai_sentiment == "negative":
            _alert(store, resp, "negative comment (AI)", datetime.now())


def process_pending(store: Store) -> int:
    pending = [r for r in list(responses(store).values()) if r.ai_status is None]
    for resp in pending:
        classify(store, resp)
    if pending:
        store.touch()
    return len(pending)


def confirm(resp: FeedbackResponse, sentiment: str, themes: list[str], by: str, now: datetime) -> None:
    if sentiment not in SENTIMENTS or any(t not in THEMES for t in themes):
        raise ValueError("Unknown sentiment or theme")
    resp.sentiment, resp.themes, resp.confirmed_by, resp.confirmed_at = sentiment, themes, by, now


# ---------- Seed ----------

def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """About 300 answered surveys over 90 days, pre-labelled with the baseline;
    Westbrook's waits get worse over the last month. Two responses arrive
    unclassified (the worker labels them through the gateway at startup) and
    three surveys are still waiting for an answer, always one of them in
    Chinese (the end-to-end test opens it from the 15 newest)."""
    done = sorted((a for a in s.appointments.values() if a.status == AppointmentStatus.COMPLETED and a.end < now),
                  key=lambda a: a.id)
    by_lang: dict[str, list[tuple]] = {}
    for item in SEED_COMMENTS:
        by_lang.setdefault(item[0], []).append(item)
    fresh = sorted(done, key=lambda a: a.end)[-40:]  # the most recent exams, whatever the weekday
    for appt in rng.sample(done[:-40] if len(done) > 340 else done, 300):
        patient = s.patients[appt.patient_id]
        lang = patient.preferred_language
        bank = by_lang[lang]
        recent = (now - appt.end).days < 30
        if appt.site_id == "WBK" and recent and rng.random() < 0.6:
            bank = [c for c in bank if c[1] <= 2] or bank  # Westbrook's bad month
        elif rng.random() < 0.55:
            bank = [c for c in bank if c[1] >= 4]  # most people are happy
        _, rating, comment = rng.choice(bank)
        when = appt.end + timedelta(hours=rng.randint(2, 30))
        if when >= now:
            continue
        survey = Survey(id=s.next_id("SRV"), token=secrets.token_urlsafe(8), appointment_id=appt.id, patient_id=patient.id,
                        site_id=appt.site_id, exam_code=appt.exam_code, language=lang, sent_at=appt.end, status="answered")
        surveys(s)[survey.id] = survey
        label = baseline(rating, comment)
        resp = FeedbackResponse(id=s.next_id("FB"), survey_id=survey.id, patient_id=patient.id, site_id=appt.site_id,
                                exam_code=appt.exam_code, language=lang, rating=rating, comment=comment, submitted_at=when,
                                ai_status="seeded", ai_sentiment=label["sentiment"], ai_themes=label["themes"],
                                ai_summary=label["summary_en"], model="baseline-rules", sentiment=label["sentiment"],
                                themes=label["themes"])
        if rng.random() < 0.7 and when < now - timedelta(days=2):
            resp.confirmed_by, resp.confirmed_at = "Jordan Lee", when + timedelta(days=1)
        responses(s)[resp.id] = resp
        if rating <= LOW_RATING or label["sentiment"] == "negative":
            alert = _alert(s, resp, f"{rating}-star rating" if rating <= LOW_RATING else "negative comment (AI)", when,
                           notify=False)
            if alert and when < now - timedelta(days=3):
                alert.status, alert.follow_up = "followed_up", "Called the patient and apologised; issue passed to the site lead."
                alert.followed_up_by, alert.followed_up_at = SITE_MANAGERS[appt.site_id], when + timedelta(days=1)
    # Two fresh responses for the worker to classify, and three unanswered surveys (one in Chinese).
    for appt, (rating, comment) in zip(rng.sample(fresh, 2), [
        (2, "We waited almost two hours and the waiting room was cold. Nobody explained the delay."),
        (5, "Very kind technologist, quick scan, thank you!"),
    ]):
        survey = Survey(id=s.next_id("SRV"), token=secrets.token_urlsafe(8), appointment_id=appt.id,
                        patient_id=appt.patient_id, site_id=appt.site_id, exam_code=appt.exam_code, language="en",
                        sent_at=appt.end, status="answered")
        surveys(s)[survey.id] = survey
        resp = FeedbackResponse(id=s.next_id("FB"), survey_id=survey.id, patient_id=appt.patient_id, site_id=appt.site_id,
                                exam_code=appt.exam_code, language="en", rating=rating, comment=comment,
                                submitted_at=now - timedelta(minutes=rng.randint(5, 50)))
        responses(s)[resp.id] = resp
        if rating <= LOW_RATING:
            _alert(s, resp, f"{rating}-star rating", resp.submitted_at)
    zh = [a for a in fresh if s.patients[a.patient_id].preferred_language == "zh"][:1]
    late = not zh
    if late:
        # None of the latest patients speaks Chinese: take the latest earlier exam of one who does and send its
        # survey now (a clinic waits a few hours anyway), so it tops the list of recent surveys. No rng used.
        surveyed = {sv.appointment_id for sv in surveys(s).values()}
        zh = sorted((a for a in done if a.id not in surveyed and s.patients[a.patient_id].preferred_language == "zh"),
                    key=lambda a: a.end)[-1:]
    for appt in zh + [a for a in fresh if a not in zh][-2:]:
        send_survey(s, appt, now if late and appt in zh else appt.end)
