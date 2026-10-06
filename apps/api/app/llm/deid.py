"""De-identification before anything is sent to Claude.

Known identifiers (patient names, health card numbers, phone numbers, emails,
addresses) are replaced with stable placeholder tokens such as [PERSON_1].
Dates of birth become ages. The token map stays on our side, so tool inputs
coming back from the model can be re-identified before they run.
"""

import re
from datetime import date

from app.core.models import Patient

PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
HEALTH_CARD_RE = re.compile(r"\b\d{10}\b")
ISO_DATE_RE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
LONG_DATE_RE = re.compile(rf"\b({MONTHS})\s+\d{{1,2}}(st|nd|rd|th)?,?\s+(19|20)\d{{2}}\b", re.IGNORECASE)


class Pseudonymizer:
    def __init__(self, known_patients: list[Patient] | None = None) -> None:
        self._forward: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._patients = known_patients or []

    def _token(self, kind: str, value: str) -> str:
        key = value.strip()
        if key.lower() in self._forward:
            return self._forward[key.lower()]
        self._counts[kind] = self._counts.get(kind, 0) + 1
        token = f"[{kind}_{self._counts[kind]}]"
        self._forward[key.lower()] = token
        self._reverse[token] = key
        return token

    def redact(self, text: str) -> str:
        for patient in self._patients:
            for value, kind in (
                (patient.full_name, "PERSON"),
                (patient.address, "ADDRESS"),
                (patient.health_card, "HEALTH_CARD"),
                (patient.email, "EMAIL"),
            ):
                if value and value.lower() in text.lower():
                    text = re.sub(re.escape(value), self._token(kind, value), text, flags=re.IGNORECASE)
        text = EMAIL_RE.sub(lambda m: self._token("EMAIL", m.group()), text)
        text = HEALTH_CARD_RE.sub(lambda m: self._token("HEALTH_CARD", m.group()), text)
        text = ISO_DATE_RE.sub(lambda m: self._token("DATE", m.group()), text)
        text = LONG_DATE_RE.sub(lambda m: self._token("DATE", m.group()), text)
        text = PHONE_RE.sub(lambda m: self._token("PHONE", m.group()), text)
        return text

    def restore(self, value):
        """Re-identify tokens inside strings, lists and dicts (e.g. tool inputs)."""
        if isinstance(value, str):
            for token, original in self._reverse.items():
                value = value.replace(token, original)
            return value
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore(v) for k, v in value.items()}
        return value


def age_from_dob(dob: date, on: date | None = None) -> int:
    on = on or date.today()
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))
