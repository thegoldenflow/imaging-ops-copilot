"""De-identification of FHIR resources before they go into a model prompt (spec 6.1, 6.2).

`FhirDeidentifier.redact_all(resources)` first learns the identifiers in the
whole set (patient and contact names, including Chinese names; staff names;
phone numbers; health card numbers; MRNs; street addresses), then returns
copies where

- structured identifiers are replaced: names -> [PERSON_n] / [STAFF_n] (one token
  per person, whichever spelling), birth date -> an `age-years` extension,
  address -> [ADDRESS_n], phone / email -> [PHONE_n] / [EMAIL_n], health card ->
  [HEALTH_CARD_n], MRN -> [MRN_<keyed hash>] (the blind-index HMAC, so the model
  never sees the MRN and an 8-digit MRN cannot be found by hashing all of them),
  other patient identifiers -> [ID_n];
- every reference display naming a person (Patient, Practitioner, PractitionerRole,
  RelatedPerson, or no reference at all) is replaced the same way;
- free-text fields (FREE_TEXT: note, conclusion, presentedForm, document content,
  descriptions, payloads, ...) have every learned identifier replaced, then the
  regex layer of app/llm/deid.py (emails, 10-digit numbers, ISO and long dates,
  phone numbers); text attachments are decoded, redacted and re-encoded, other
  attachments lose their data;
- the generated narrative (`text`) is dropped (a FHIR server may write names into it).

Codes, ids, references and clinical dates stay, so the model can still reason over
the record; the patient appears only by token. The token map stays on the server:
`restore()` re-identifies model output. The stronger free-text layer (partial
names, mixed date formats, organisations, a second model pass) is WP4 (6.3).
"""

from __future__ import annotations

import base64
import copy
from collections.abc import Iterable, Iterator
from datetime import date

from app.ehr.codes import EXT, HCN_SYSTEM, MRN_SYSTEM
from app.fhir.dt import parse
from app.llm.deid import Pseudonymizer, age_from_dob

PERSON_REFERENCES = ("Patient/", "Practitioner/", "PractitionerRole/", "RelatedPerson/")

# Elements that carry patient or staff identifiers, per resource type (paths below the
# resource; lists are walked implicitly). Reference displays and the narrative are
# handled for every type in addition (see the module docstring).
STRUCTURED: dict[str, tuple[str, ...]] = {
    "Patient": ("name", "birthDate", "address", "telecom", "identifier", "contact"),
    "Practitioner": ("name", "telecom", "address"),
}
FREE_TEXT: dict[str, tuple[str, ...]] = {
    "Patient": (),
    "Practitioner": (),
    "PractitionerRole": (),
    "Organization": (),
    "Location": (),
    "Encounter": (),
    "Appointment": ("description", "comment", "patientInstruction"),
    "Schedule": ("comment",),
    "Slot": ("comment",),
    "ServiceRequest": ("note.text", "patientInstruction"),
    "MedicationRequest": ("note.text",),
    "MedicationStatement": ("note.text",),
    "AllergyIntolerance": ("note.text", "reaction.description"),
    "Observation": ("valueString", "note.text"),
    "DiagnosticReport": ("conclusion", "presentedForm"),
    "Procedure": ("note.text",),
    "Condition": ("note.text",),
    "CarePlan": ("title", "description", "note.text", "activity.detail.description"),
    "DocumentReference": ("description", "content.attachment"),
    "Task": ("description", "note.text"),
    "Flag": (),
    "Communication": ("payload.contentString", "payload.contentAttachment", "note.text"),
    "Provenance": (),
    "Consent": (),
    "EpisodeOfCare": (),
}


def mrn_token(mrn: str) -> str:
    from app.core.db.crypto import cipher

    return f"[MRN_{cipher().blind_index(mrn)[:10]}]"


def _targets(node, parts: list[str]) -> Iterator[tuple[dict, str]]:
    """(container, key) for every element at a dotted path, walking lists."""
    if isinstance(node, list):
        for item in node:
            yield from _targets(item, parts)
        return
    if not isinstance(node, dict) or parts[0] not in node:
        return
    if len(parts) == 1:
        yield node, parts[0]
    else:
        yield from _targets(node[parts[0]], parts[1:])


def _name_spellings(name: dict) -> list[str]:
    """The ways a HumanName may be written in text: its text, given + family, family + given."""
    given, family = " ".join(name.get("given") or []), name.get("family") or ""
    out = [name.get("text") or ""]
    if given and family:
        out += [f"{given} {family}", f"{family} {given}", f"{family}{given}"]
    prefix = " ".join(name.get("prefix") or [])
    if prefix and given and family:
        out.append(f"{prefix} {given} {family}")
    return [s for s in dict.fromkeys(out) if s.strip()]


class FhirDeidentifier:
    def __init__(self, pseudo: Pseudonymizer | None = None, *, on: date | None = None) -> None:
        self.pseudo = pseudo or Pseudonymizer()
        self.on = on or date.today()

    # ----- learning the identifiers in a set of resources -----

    def _person(self, kind: str, names: list[dict]) -> str | None:
        """One token for all of a person's names; each spelling becomes a known value for free text."""
        spellings = [s for name in names for s in _name_spellings(name)]
        if not spellings:
            return None
        token = self.pseudo.token(kind, spellings[0])
        for spelling in spellings:
            self.pseudo.token(kind, spelling, same_as=spellings[0])
            self.pseudo.add_known(spelling, kind)
        return token

    def _contact_point(self, value: str | None, system: str | None) -> None:
        if value:
            self.pseudo.add_known(value, {"phone": "PHONE", "email": "EMAIL"}.get(system or "", "CONTACT"))

    def learn(self, resources: Iterable[dict]) -> None:
        for resource in resources:
            rtype = resource.get("resourceType")
            if rtype == "Patient":
                self._person("PERSON", resource.get("name") or [])
                for ident in resource.get("identifier") or []:
                    if not ident.get("value"):
                        continue
                    if ident.get("system") == MRN_SYSTEM:
                        self.pseudo.register(ident["value"], mrn_token(ident["value"]))
                        self.pseudo.add_known(ident["value"], "MRN")
                    else:
                        self.pseudo.add_known(ident["value"], "HEALTH_CARD" if ident.get("system") == HCN_SYSTEM else "ID")
                for contact in resource.get("contact") or []:
                    if contact.get("name"):
                        self._person("PERSON", [contact["name"]])
                    for point in contact.get("telecom") or []:
                        self._contact_point(point.get("value"), point.get("system"))
            if rtype in ("Patient", "Practitioner"):
                for point in resource.get("telecom") or []:
                    self._contact_point(point.get("value"), point.get("system"))
                for address in resource.get("address") or []:
                    for line in address.get("line") or []:
                        self.pseudo.add_known(line, "ADDRESS")
            if rtype == "Practitioner":
                self._person("STAFF", resource.get("name") or [])
            for reference in self._references(resource):
                display, target = reference.get("display"), reference.get("reference") or ""
                if display and target.startswith(("Practitioner/", "PractitionerRole/")):
                    self._person("STAFF", [{"text": display}])
                elif display and target.startswith(("Patient/", "RelatedPerson/")):
                    self._person("PERSON", [{"text": display}])

    def _references(self, node) -> Iterator[dict]:
        if isinstance(node, list):
            for item in node:
                yield from self._references(item)
        elif isinstance(node, dict):
            if "reference" in node or ("display" in node and set(node) <= {"display", "identifier", "type"}):
                yield node
            for value in node.values():
                yield from self._references(value)

    # ----- redaction -----

    def _token(self, kind: str, value: str) -> str:
        return self.pseudo.token(kind, value)

    def _text(self, value: str) -> str:
        return self.pseudo.redact(value)

    def _attachment(self, attachment: dict) -> None:
        if attachment.get("title"):
            attachment["title"] = self._text(attachment["title"])
        if "data" in attachment:
            if (attachment.get("contentType") or "text/plain").startswith("text/"):
                text = base64.b64decode(attachment["data"]).decode("utf-8", errors="replace")
                attachment["data"] = base64.b64encode(self._text(text).encode()).decode()
            else:
                del attachment["data"]  # binary content cannot be checked, so it is not passed on
        if "url" in attachment:
            del attachment["url"]

    def _structured(self, resource: dict, element: str) -> None:
        rtype = resource["resourceType"]
        value = resource.get(element)
        if value is None:
            return
        if element == "name":
            token = self._person("STAFF" if rtype == "Practitioner" else "PERSON", value)
            resource["name"] = [{**({"use": n["use"]} if n.get("use") else {}), "text": token} for n in value]
        elif element == "birthDate":
            del resource["birthDate"]
            age = age_from_dob(parse(value).date(), self.on)  # a partial date (YYYY or YYYY-MM) counts from its start
            resource.setdefault("extension", []).append({"url": EXT + "age-years", "valueInteger": age})
        elif element == "address":
            resource["address"] = [{**({"use": a["use"]} if a.get("use") else {}),
                                    "text": self._token("ADDRESS", ", ".join(a.get("line") or []) or a.get("text") or "address")}
                                   for a in value]
        elif element == "telecom":
            for point in value:
                if point.get("value"):
                    point["value"] = self._token({"phone": "PHONE", "email": "EMAIL"}.get(point.get("system"), "CONTACT"),
                                                 point["value"])
        elif element == "identifier":
            for ident in value:
                if not ident.get("value"):
                    continue
                if ident.get("system") == MRN_SYSTEM:
                    self.pseudo.register(ident["value"], mrn_token(ident["value"]))
                    ident["value"] = mrn_token(ident["value"])
                else:
                    ident["value"] = self._token("HEALTH_CARD" if ident.get("system") == HCN_SYSTEM else "ID",
                                                 ident["value"])
                    ident.pop("extension", None)  # e.g. the health card version code
        elif element == "contact":
            for contact in value:
                if contact.get("name"):
                    contact["name"] = {"text": self._person("PERSON", [contact["name"]])}
                for point in contact.get("telecom") or []:
                    if point.get("value"):
                        point["value"] = self._token("PHONE" if point.get("system") == "phone" else "CONTACT",
                                                     point["value"])
                if contact.get("address"):
                    contact["address"] = {"text": self._token("ADDRESS", ", ".join(contact["address"].get("line") or [])
                                                              or "address")}

    def redact(self, resource: dict) -> dict:
        """A de-identified copy of one resource (call learn() on the whole set first)."""
        out = copy.deepcopy(resource)
        rtype = out.get("resourceType")
        if rtype not in FREE_TEXT:
            raise KeyError(f"No de-identification rules for {rtype}")
        out.pop("text", None)  # narrative
        for element in STRUCTURED.get(rtype, ()):
            self._structured(out, element)
        for path in FREE_TEXT[rtype]:
            parts = path.split(".")
            for container, key in list(_targets(out, parts)):
                value = container[key]
                if key in ("presentedForm", "attachment", "contentAttachment"):
                    for attachment in value if isinstance(value, list) else [value]:
                        self._attachment(attachment)
                elif isinstance(value, str):
                    container[key] = self._text(value)
        for reference in self._references(out):
            display, target = reference.get("display"), reference.get("reference") or ""
            if display and (not target or target.startswith(PERSON_REFERENCES)):
                kind = "STAFF" if target.startswith(("Practitioner/", "PractitionerRole/")) else "PERSON"
                reference["display"] = self._person(kind, [{"text": display}])
        return out

    def redact_all(self, resources: Iterable[dict]) -> list[dict]:
        resources = list(resources)
        self.learn(resources)
        return [self.redact(r) for r in resources]

    def restore(self, value):
        return self.pseudo.restore(value)
