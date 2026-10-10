"""De-identification of the documentation agents' input (spec 7.3, "names and dates -> tokens, restored after
drafting").

The runtime's `gather` already de-identified the resources (app/llm/fhir_deid.py) and
learned the record's identifiers (names, MRN, health card, phones, addresses). The
agents here feed the model plain facts built from those resources, so:

- names: the learned identifiers are replaced wherever they appear (`[PERSON_1]`,
  `[STAFF_2]`, `[MRN_…]`);
- dates: every clinical date becomes a token with its day relative to the admission
  (`[DATE_3:D+2]`), the platform's token format; `Pseudonymizer.restore` maps it
  back to the date after drafting (the LLM gateway does that with the same map);
- free text (notes, report conclusions, care plan descriptions, the pharmacist's
  reasons) goes through the free-text rule layer as well (dates, phones, addresses,
  names after titles), relative to the admission day.

The patient's language preference never goes to the model: the instructions are
rendered in it after drafting (render.py).
"""

from __future__ import annotations

import re
from datetime import date

from app.agents.runtime import Context

ISO_DAY = re.compile(r"^(19|20)\d{2}-\d{2}-\d{2}$")
DATE_KEYS = {"admitted", "discharged", "onset", "date", "authored", "ordered", "when"}
TEXT_KEYS = {"note", "conclusion", "description", "change_note", "pharmacist_reason", "previous", "text", "title",
             "location", "what"}


class Deid:
    def __init__(self, ctx: Context, admitted: date | None) -> None:
        self.pseudo = ctx.pseudo
        self.freetext = ctx.freetext
        self.admitted = admitted or date.today()
        self.freetext.reference = self.admitted  # free-text dates get offsets from the admission day too

    def day(self, iso: str) -> str:
        if not iso or not ISO_DAY.match(iso[:10]):
            return iso
        token = self.pseudo.token("DATE", iso[:10])
        offset = (date.fromisoformat(iso[:10]) - self.admitted).days
        dated = f"{token[:-1]}:D{offset:+d}]"
        self.pseudo.alias(dated, iso[:10])
        return dated

    def text(self, value: str) -> str:
        return self.freetext.redact(value) if value else value

    def value(self, key: str, value):
        if isinstance(value, dict):
            return {k: self.value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(key, v) for v in value]
        if not isinstance(value, str):
            return value
        if key in DATE_KEYS:
            return self.day(value)
        if key in TEXT_KEYS:
            return self.text(value)
        return self.pseudo.redact_known(value)

    def facts(self, facts: list[dict]) -> list[dict]:
        return [self.value("", f) for f in facts]

    def restore(self, value):
        return self.pseudo.restore(value)
