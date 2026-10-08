"""WP2: de-identification covers every hospital resource (spec 6.1): one test per resource type.

Each resource's example gets the identifiers of a patient (English and Chinese name,
MRN, health card, phone, street address, a relative) and of a staff member planted in
every free-text field and reference display the field list names; none may survive.
"""

import base64
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from app.ehr.codes import EXT, MRN_SYSTEM
from app.fhir.types import RESOURCE_TYPES, validate
from app.llm.fhir_deid import FREE_TEXT, STRUCTURED, FhirDeidentifier, mrn_token

EXAMPLES = Path(__file__).resolve().parents[1] / "app" / "fhir" / "examples"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


PATIENT = _example("Patient")
PATIENT["name"].append({"use": "usual", "family": "王", "given": ["芳"], "text": "王芳",
                        "extension": [{"url": EXT + "name-language", "valueCode": "zh-CN"}]})
PATIENT["contact"] = [{"relationship": [{"text": "daughter"}], "name": {"text": "Grace Brennan"},
                       "telecom": [{"system": "phone", "value": "+1-416-555-0177"}]}]
STAFF = _example("Practitioner")

SECRETS = ["Paul Brennan", "Brennan Paul", "王芳", "40000003", "5960899638", "+1-647-555-0148", "865 Example St",
           "Grace Brennan", "+1-416-555-0177", "Dmitri Salazar", "1944-05-14"]
NOTE = ("Spoke with Paul Brennan (王芳) and his daughter Grace Brennan on +1-416-555-0177. MRN 40000003, "
        "card 5960899638, home 865 Example St, phone +1-647-555-0148. Plan agreed with Dr. Dmitri Salazar.")
LIST_KEYS = {"note", "presentedForm", "content", "payload", "reaction", "activity"}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _plant(resource: dict, path: str) -> None:
    """Put the note into the element at `path`, creating it if needed."""
    parts = path.split(".")
    if parts[-1] in ("presentedForm", "attachment", "contentAttachment"):
        value = {"contentType": "text/plain", "data": _b64(NOTE), "title": NOTE}
    else:
        value = NOTE
    if parts == ["valueString"]:
        for key in [k for k in resource if k.startswith("value")]:
            del resource[key]
    node = resource
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part in LIST_KEYS and part != parts[-1]:
            items = node.setdefault(part, [{}])
            node = items[0]
        elif last:
            node[part] = [value] if part == "presentedForm" else value
        else:
            node = node.setdefault(part, {})


def _references(node):
    if isinstance(node, list):
        for item in node:
            yield from _references(item)
    elif isinstance(node, dict):
        if "reference" in node:
            yield node
        for value in node.values():
            yield from _references(value)


def _plant_displays(resource: dict) -> None:
    for reference in _references(resource):
        target = reference["reference"]
        if target.startswith("Patient/"):
            reference["display"] = "Paul Brennan"
        elif target.startswith(("Practitioner/", "PractitionerRole/")):
            reference["display"] = "Dr. Dmitri Salazar"


def _text(resource: dict) -> str:
    """Everything a model would see, attachments decoded."""
    out = json.dumps(resource, ensure_ascii=False)
    for node in [resource.get("presentedForm") or [], *[c.get("attachment") for c in resource.get("content") or []],
                 *[p.get("contentAttachment") for p in resource.get("payload") or []]]:
        for att in node if isinstance(node, list) else [node]:
            if att and att.get("data"):
                out += base64.b64decode(att["data"]).decode()
    return out


def test_every_resource_type_has_rules():
    assert set(FREE_TEXT) == set(RESOURCE_TYPES)
    assert set(STRUCTURED) <= set(RESOURCE_TYPES)


@pytest.mark.parametrize("name", sorted(RESOURCE_TYPES))
def test_resource_is_deidentified(name):
    resource = copy.deepcopy(PATIENT if name == "Patient" else STAFF if name == "Practitioner" else _example(name))
    for path in FREE_TEXT[name]:
        _plant(resource, path)
    _plant_displays(resource)
    resource["text"] = {"status": "generated", "div": f"<div>{NOTE}</div>"}  # a server-written narrative
    deid = FhirDeidentifier()
    redacted = deid.redact_all([PATIENT, STAFF, resource])[2]
    seen = _text(redacted)
    for secret in SECRETS:
        assert secret.lower() not in seen.lower(), f"{name} leaks {secret!r}"
    validate(redacted)  # still a valid resource of its type
    assert resource["id"] == redacted["id"]  # ids and references stay for reasoning


def test_patient_structured_fields():
    deid = FhirDeidentifier(on=date(2024, 5, 14))  # his 80th birthday
    patient = deid.redact_all([PATIENT])[0]
    names = {n["text"] for n in patient["name"]}
    assert names == {"[PERSON_1]"}  # one person, one token, whichever spelling
    assert "birthDate" not in patient
    assert {"url": EXT + "age-years", "valueInteger": 80} in patient["extension"]
    mrn = next(i for i in patient["identifier"] if i["system"] == MRN_SYSTEM)
    assert mrn["value"] == mrn_token("40000003") and mrn["value"].startswith("[MRN_")
    hcn = next(i for i in patient["identifier"] if i["system"] != MRN_SYSTEM)
    assert hcn["value"] == "[HEALTH_CARD_1]" and "extension" not in hcn  # the version code goes too
    assert patient["address"] == [{"use": "home", "text": "[ADDRESS_1]"}]
    assert patient["telecom"][0]["value"] == "[PHONE_1]"
    assert patient["contact"][0]["name"] == {"text": "[PERSON_2]"}
    assert patient["gender"] == "male" and patient["communication"] == PATIENT["communication"]
    # The token map stays on the server: model output can be re-identified
    assert deid.restore("[PERSON_1] needs a follow-up; MRN " + mrn["value"]) == "Paul Brennan needs a follow-up; MRN 40000003"



def test_binary_attachments_are_not_passed_on():
    doc = _example("DocumentReference")
    doc["content"] = [{"attachment": {"contentType": "application/pdf", "data": _b64("%PDF Paul Brennan"),
                                      "url": "http://example.org/doc.pdf"}}]
    redacted = FhirDeidentifier().redact_all([PATIENT, doc])[1]
    assert redacted["content"][0]["attachment"] == {"contentType": "application/pdf"}


def test_clinical_content_is_kept():
    obs = _example("Observation")
    redacted = FhirDeidentifier().redact_all([PATIENT, obs])[1]
    assert redacted["code"] == obs["code"] and redacted.get("component") == obs.get("component")
    assert redacted["effectiveDateTime"] == obs["effectiveDateTime"]  # clinical times stay; only identifiers go
