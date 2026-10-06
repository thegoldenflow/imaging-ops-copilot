"""System 9 · Patient Prep Instructions.

English templates per prep type are approved by clinical staff. Translations
are never generated at send time: Claude drafts them ahead of time, a person
approves them, and only approved text is sent. Without an approved translation
the patient gets the approved English version and staff see why."""

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.core.models import Appointment
from app.core.store import Store, get_store
from app.core.templates import LANGUAGES, PREP, PREP_FOR_EXAM, format_when
from app.integrations.mocks import queue_message
from app.llm.gateway import get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture
from app.modules.scheduling import service as scheduling

PREP_NAMES = {
    "fasting": "Fasting (abdominal ultrasound)", "contrast": "IV contrast (CT)", "mri": "MRI safety and preparation",
    "full_bladder": "Full bladder (pelvic ultrasound)", "none": "No special preparation",
}
SEED_APPROVER = "Dr. Daniel Okafor"
# Punjabi drafts for these keys start unapproved so the demo shows the approval gate.
PENDING_AT_SEED = {("contrast", "pa"), ("mri", "pa")}


class Translation(BaseModel):
    text: str
    status: str  # draft, approved
    source: str  # pre-written, ai, edited
    llm_call_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    updated_at: datetime


class PrepTemplate(BaseModel):
    key: str
    name: str
    english: str
    english_approved_by: str
    exam_codes: list[str]
    translations: dict[str, Translation]


class TranslationOutput(BaseModel):
    text: str


TRANSLATE_PROMPT = Prompt(
    name="prep_translation",
    version="prep_translation@1",
    system=(
        "Translate patient preparation instructions for a medical imaging appointment. Keep the meaning exact, "
        "use plain language a patient understands, keep numbers and units unchanged, and add nothing. "
        "A clinical staff member reviews every translation before it is sent."
    ),
    template="Target language: {language}\n\nEnglish text:\n{text}",
)

_LANG_BY_NAME = {name: code for code, name in LANGUAGES.items()}


@mock_fixture("prep_translation")
def _mock(text: str, images: list, attempt: int) -> dict:
    language = text.split("\n", 1)[0].split(": ", 1)[1]
    english = text.split("English text:\n", 1)[1].strip()
    key = next(k for k, v in PREP.items() if v["en"] == english)
    return {"text": PREP[key][_LANG_BY_NAME.get(language, "en")]}


def templates(store: Store) -> dict[str, PrepTemplate]:
    def build() -> dict[str, PrepTemplate]:
        now = datetime.now()
        out = {}
        for key, texts in PREP.items():
            translations = {}
            for lang in LANGUAGES:
                if lang == "en":
                    continue
                pending = (key, lang) in PENDING_AT_SEED
                translations[lang] = Translation(
                    text=texts[lang], status="draft" if pending else "approved", source="ai" if pending else "pre-written",
                    approved_by=None if pending else SEED_APPROVER, approved_at=None if pending else now, updated_at=now,
                )
            out[key] = PrepTemplate(
                key=key, name=PREP_NAMES[key], english=texts["en"], english_approved_by=SEED_APPROVER,
                exam_codes=[code for code, k in PREP_FOR_EXAM.items() if k == key], translations=translations,
            )
        return out

    return store.module("prep_templates", build)


def key_for(exam_code: str) -> str:
    return PREP_FOR_EXAM.get(exam_code, "none")


def message_text(store: Store, exam_code: str, language: str) -> tuple[str, str, str | None]:
    """(text, language actually used, note). Only approved text is ever returned."""
    template = templates(store)[key_for(exam_code)]
    if language == "en":
        return template.english, "en", None
    translation = template.translations.get(language)
    if translation and translation.status == "approved":
        return translation.text, language, None
    return template.english, "en", f"{LANGUAGES.get(language, language)} translation not approved yet; sent in English"


def draft_translation(store: Store, key: str, language: str) -> Translation:
    template = templates(store)[key]
    outcome = get_gateway().structured(
        task="prep_translation", prompt=TRANSLATE_PROMPT,
        variables={"language": LANGUAGES[language], "text": template.english},
        schema_cls=TranslationOutput, tier="fast",
    )
    if outcome.status != "ok":
        raise RuntimeError(outcome.error or "Translation unavailable")
    translation = Translation(text=outcome.data["text"], status="draft", source="ai", llm_call_id=outcome.call_id,
                              updated_at=datetime.now())
    template.translations[language] = translation
    store.touch()
    return translation


def on_booked(appt: Appointment) -> None:
    store = get_store()
    patient = store.patients[appt.patient_id]
    exam = store.exams[appt.exam_code]
    text, used, note = message_text(store, appt.exam_code, patient.preferred_language)
    header = f"{exam.name}, {format_when(appt.start, used)}: "
    queue_message(channel="email", kind="prep", to=patient.email, language=used, patient_id=patient.id,
                  appointment_id=appt.id, body=header + text, note=note)
    send_at = appt.start - timedelta(hours=48)
    if send_at > datetime.now():
        queue_message(channel="sms", kind="prep_48h", to=patient.phone, language=used, patient_id=patient.id,
                      appointment_id=appt.id, scheduled_for=send_at, body=header + text, note=note)


scheduling.BOOKING_HOOKS.append(on_booked)
