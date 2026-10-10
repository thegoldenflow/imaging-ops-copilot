"""The documentation module's DocumentReference: readable text plus the structured content (spec 7.3).

One draft per document and encounter, with a deterministic id (`dsum-<encounter>`,
`pinstr-<encounter>`, `sbar-<encounter>-<YYYYMMDD><D|N>`), so a retried journey step
or a second click finds it instead of writing another. Content:

- `text/plain` attachments: the readable document (the patient instructions in
  English, Simplified and Traditional Chinese, each with its `language`);
- `application/json` titled `documentation-data`: the structured content (the
  schema of models.py), the AI draft as the model wrote it, the validator's report,
  the sources the draft may cite (what the model was given), and the run.

A draft with untraceable items carries the extension `sign-block` (the validator's
summary): the signing service refuses it, whatever path the signature comes from.
Pure functions over plain FHIR dicts.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

from app.ehr.codes import DOC_TYPES, EXT, concept
from app.fhir.dt import ref

DATA_TITLE = "documentation-data"
SIGN_BLOCK = EXT + "sign-block"
KINDS = {"discharge_summary": ("discharge_summary", "dsum", "Discharge summary"),
         "patient_instructions": ("patient_instructions", "pinstr", "Patient discharge instructions"),
         "handoff": ("handoff", "sbar", "Nursing handoff (SBAR)")}
SCHEMAS = {"discharge_summary": "discharge-summary@1", "patient_instructions": "patient-instructions@1",
           "handoff": "sbar-handoff@1"}
AGENTS = {"discharge_summary": "discharge_summary", "patient_instructions": "patient_instructions",
          "handoff": "nursing_handoff"}


def shift_key(at: datetime) -> str:
    """The nursing shift a moment falls in: day 07:00-19:00 (D), night 19:00-07:00 (N, dated by its start)."""
    if 7 <= at.hour < 19:
        return f"{at:%Y%m%d}D"
    start = at if at.hour >= 19 else at - timedelta(days=1)
    return f"{start:%Y%m%d}N"


def document_id(kind: str, encounter_id: str, shift: str | None = None) -> str:
    prefix = KINDS[kind][1]
    return f"{prefix}-{encounter_id}" + (f"-{shift}" if shift else "")


def kind_of(document: dict) -> str | None:
    """discharge_summary / patient_instructions / handoff for this module's documents (by their data), else None."""
    data = read_data(document)
    if not data:
        return None
    for kind, schema in SCHEMAS.items():
        if data.get("schema") == schema:
            return kind
    return None


def read_data(document: dict) -> dict | None:
    for content in document.get("content") or []:
        a = content.get("attachment") or {}
        if a.get("contentType") == "application/json" and a.get("title") == DATA_TITLE and a.get("data"):
            return json.loads(base64.b64decode(a["data"]).decode())
    return None


def read_texts(document: dict) -> dict[str, str]:
    out = {}
    for content in document.get("content") or []:
        a = content.get("attachment") or {}
        if (a.get("contentType") or "").startswith("text/") and a.get("data"):
            out[a.get("language") or "en"] = base64.b64decode(a["data"]).decode()
    return out


def sign_block(document: dict) -> str | None:
    return next((e.get("valueString") for e in document.get("extension") or [] if e.get("url") == SIGN_BLOCK), None)


def _attachments(texts: list[dict], data: dict, title: str) -> list[dict]:
    out = [{"attachment": {"contentType": "text/plain", "language": t.get("language") or "en",
                           "title": t.get("title") or title,
                           "data": base64.b64encode(t["text"].encode()).decode()}} for t in texts]
    out.append({"attachment": {"contentType": "application/json", "title": DATA_TITLE,
                               "data": base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()}})
    return out


def apply_content(document: dict, *, texts: list[dict], data: dict, title: str, block: str | None,
                  sources: list[str] | None = None, when: str | None = None) -> dict:
    """Put new content on a draft (create or update), with or without the sign block."""
    doc = dict(document)
    doc["content"] = _attachments(texts, data, title)
    doc["description"] = title
    if when:
        doc["date"] = when
    if sources is not None:
        doc["context"] = {**(doc.get("context") or {}), "related": [{"reference": r} for r in sources[:200]]}
    exts = [e for e in doc.get("extension") or [] if e.get("url") != SIGN_BLOCK]
    if block:
        exts.append({"url": SIGN_BLOCK, "valueString": block[:500]})
    doc["extension"] = exts
    return doc


def new_document(kind: str, doc_id: str, *, patient_id: str, encounter_id: str, title: str, texts: list[dict],
                 data: dict, block: str | None, when: str, author: str, sources: list[str]) -> dict:
    system, code, display = DOC_TYPES[KINDS[kind][0]]
    doc = {"resourceType": "DocumentReference", "id": doc_id, "status": "current", "docStatus": "preliminary",
           "type": concept(system, code, display), "subject": ref("Patient", patient_id), "date": when,
           "author": [{"display": author}],
           "context": {"encounter": [ref("Encounter", encounter_id)]}}
    return apply_content(doc, texts=texts, data=data, title=title, block=block, sources=sources)
