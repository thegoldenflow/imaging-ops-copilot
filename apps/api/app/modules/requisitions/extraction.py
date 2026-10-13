"""Shared first step: turn a requisition into structured fields, once.

Every field carries the exact quote it came from and a confidence, so the UI can
highlight the source and flag low-confidence values for a person to confirm.
The mock provider uses `baseline_extract`, a rule-based extractor that also
serves as the comparison baseline in the evals."""

import re

from pydantic import BaseModel, Field, field_validator

from app.agents.prompts import registered
from app.llm.providers import mock_fixture

LOW_CONFIDENCE = 0.7


class Sourced(BaseModel):
    value: str
    source_quote: str = Field(description="Exact text copied from the requisition, or empty if absent")
    confidence: float = Field(ge=0, le=1)


NONE_WORDS = {"none", "nkda", "none known", "nil", "n/a"}


class Extraction(BaseModel):
    requested_exam: Sourced
    clinical_indication: Sourced
    relevant_history: list[Sourced]
    prior_imaging: list[Sourced] = Field(description="Prior imaging at an outside facility the requisition names "
                                                     "(for retrieval from there); empty when there is none")
    allergies: list[Sourced]
    renal_or_diabetes: list[Sourced]
    implant_hints: list[Sourced]
    stated_urgency: Sourced  # value: urgent, routine or unspecified

    @field_validator("relevant_history", "prior_imaging", "allergies", "renal_or_diabetes", "implant_hints")
    @classmethod
    def _drop_none_entries(cls, items: list[Sourced]) -> list[Sourced]:
        """A form's "Previous imaging: None known" is an empty list, not one item with that value (or an empty one):
        the eval and the priors module would read it as a prior."""
        return [i for i in items if i.value.strip().strip(".").strip().lower() not in NONE_WORDS | {""}]


FIELD_LABELS = {
    "requested_exam": "Requested exam",
    "clinical_indication": "Clinical indication",
    "relevant_history": "Relevant history",
    "prior_imaging": "Prior imaging at an outside facility",
    "allergies": "Allergies",
    "renal_or_diabetes": "Kidney disease or diabetes",
    "implant_hints": "Implant hints",
    "stated_urgency": "Urgency stated by referrer",
}

EXTRACT_PROMPT = registered("requisition_extract")

LABELLED = {
    "requested_exam": r"^Exam requested:\s*(.+)$",
    "clinical_indication": r"^Clinical information:\s*(.+)$",
    "history": r"^Relevant history:\s*(.+)$",
    "allergies": r"^Allergies:\s*(.+)$",
    "prior": r"^Previous imaging:\s*(.+)$",
}
RENAL = re.compile(r"[^.;,\n]*(diabet\w*|metformin|chronic kidney disease|\bckd\b|renal (impairment|failure|insufficiency)|kidney (disease|function))[^.;,\n]*", re.I)
IMPLANT = re.compile(r"[^.;,\n]*(pacemaker|defibrillator|\bicd\b|cochlear|aneurysm clip|neurostimulator|stimulator|insulin pump|"
                     r"metal fragment|shrapnel|shunt|hip replacement|knee replacement|stent|sternal wires)[^.;,\n]*", re.I)
URGENT = re.compile(r"urgent\w*[^.\n]*|expedite[^.\n]*|as soon as possible|\basap\b", re.I)
ROUTINE = re.compile(r"routine[^.\n]*", re.I)


def _s(value: str, confidence: float, quote: str | None = None) -> dict:
    return {"value": value.strip(), "source_quote": (quote if quote is not None else value).strip(), "confidence": confidence}


def _strip_label(fragment: str) -> str:
    """Drop a form label such as 'Relevant history:' or 'PMH:' in front of a match."""
    return re.sub(r"^\s*[A-Za-z ]+:\s*", "", fragment)


def _split(value: str) -> list[str]:
    return [part.strip() for part in re.split(r";|,(?![^()]*\))", value) if part.strip()]


def baseline_extract(text: str) -> dict:
    """Rule-based extraction: labelled form fields first, then free-text patterns."""
    found = {k: re.search(p, text, re.M | re.I) for k, p in LABELLED.items()}
    out: dict = {}
    if found["requested_exam"]:
        out["requested_exam"] = _s(found["requested_exam"].group(1), 0.95)
    elif m := re.search(r"please arrange (?:an? )?(.+?) for ", text, re.I):
        out["requested_exam"] = _s(m.group(1), 0.8)
    else:
        out["requested_exam"] = _s("", 0.0, "")

    if found["clinical_indication"]:
        out["clinical_indication"] = _s(found["clinical_indication"].group(1), 0.9)
    elif m := re.search(r"\)\.\s*(.+?\.)(?:\s|$)", text):
        out["clinical_indication"] = _s(m.group(1), 0.65)
    else:
        out["clinical_indication"] = _s("", 0.0, "")

    history = found["history"].group(1) if found["history"] else (m.group(1) if (m := re.search(r"PMH:\s*(.+?)\.", text)) else "")
    out["relevant_history"] = [] if history.lower() in NONE_WORDS else [_s(h, 0.85) for h in _split(history)]

    allergies = found["allergies"].group(1) if found["allergies"] else (
        m.group(1) if (m := re.search(r"allergic to (.+?\))", text, re.I)) else "")
    out["allergies"] = [] if allergies.lower() in NONE_WORDS else [_s(a, 0.85 if found["allergies"] else 0.7) for a in _split(allergies)]

    # Prior imaging at an outside facility (requisition_extract@2): a form's "Previous imaging:" line or a letter's
    # "had a ... at ...", only when it names where (not "None known", not imaging mentioned without a facility).
    prior = found["prior"].group(1) if found["prior"] else (m.group(1) if (m := re.search(r"\b(?:had|has had) an? (.+? at .+?)\.", text)) else "")
    at_facility = prior.lower() not in NONE_WORDS and re.search(r"\bat\b", prior, re.I)
    out["prior_imaging"] = [_s(prior, 0.9 if found["prior"] else 0.6)] if at_facility else []

    out["renal_or_diabetes"] = [_s(_strip_label(m.group(0)), 0.85) for m in RENAL.finditer(text)]
    out["implant_hints"] = [_s(_strip_label(m.group(0)), 0.8) for m in IMPLANT.finditer(text)]

    if m := URGENT.search(text):
        out["stated_urgency"] = _s("urgent", 0.9, m.group(0))
    elif m := ROUTINE.search(text):
        out["stated_urgency"] = _s("routine", 0.9, m.group(0))
    else:
        out["stated_urgency"] = _s("unspecified", 0.8, "")
    return out


@mock_fixture("requisition_extract")
def _mock(text: str, images: list, attempt: int) -> dict:
    body = text.split("<<<\n", 1)[-1].rsplit("\n>>>", 1)[0]
    return baseline_extract(body)


def low_confidence_fields(extraction: dict) -> list[str]:
    flagged = []
    for key, value in extraction.items():
        items = value if isinstance(value, list) else [value]
        if any(item["confidence"] < LOW_CONFIDENCE and item["value"] for item in items):
            flagged.append(key)
    return flagged
