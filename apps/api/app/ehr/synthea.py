"""Localization of Synthea output for the demo hospital (spec 6.2 step 3).

Synthea writes US patients. This changes only formats, never adds real data:
- addresses become synthetic GTA addresses (city, ON, postal code A1A 1A1); phone numbers 555-01xx;
- US identifiers (SSN, driver's licence, passport) are removed; the Synthea medical record number
  moves to `urn:demo-hospital:mrn`; a synthetic Ontario health card (10 digits + version code)
  is added and marked synthetic;
- 30% of patients get `communication.language` zh-CN or zh-TW (seeded, so it is repeatable);
- every inpatient Encounter gets `hospitalization` (if missing) and a bed Location;
- SNOMED CT, LOINC and RxNorm codes are kept as they are.
"""

from __future__ import annotations

import hashlib
import random

from app.ehr import codes as C
from app.ehr.seed.builder import ext
from app.ehr.seed.people import CITIES, LANGUAGE_DISPLAY, STREETS, _postal

US_IDENTIFIER_SYSTEMS = {
    "http://hl7.org/fhir/sid/us-ssn",
    "urn:oid:2.16.840.1.113883.4.3.25",  # driver's licence (Synthea)
    "http://standardhealthrecord.org/fhir/StructureDefinition/passportNumber",
}
SYNTHEA_MRN_TYPE = "MR"


def _rng(seed: int, key: str) -> random.Random:
    return random.Random(f"localize:{seed}:{key}")


def localize_patient(patient: dict, seed: int = 42) -> dict:
    rng = _rng(seed, patient["id"])
    out = dict(patient)
    # All source identifiers are replaced: US ones (SSN, licence, passport) are dropped, the
    # medical record number is re-issued in the hospital's MRN system.
    mrn = next((i.get("value") for i in patient.get("identifier", [])
                if i.get("system") not in US_IDENTIFIER_SYSTEMS
                and any(c.get("code") == SYNTHEA_MRN_TYPE for c in (i.get("type") or {}).get("coding") or [])), None)
    identifiers = []
    digest = hashlib.sha256((mrn or patient["id"]).encode()).hexdigest()
    identifiers.append({"use": "usual", "type": {"text": "Medical record number"}, "system": C.MRN_SYSTEM,
                        "value": "5" + str(int(digest[:12], 16))[:7].zfill(7)})
    identifiers.append({"use": "official", "type": {"text": "Ontario health card (synthetic)"}, "system": C.HCN_SYSTEM,
                        "value": "".join(str(rng.randint(0, 9)) for _ in range(10)),
                        "extension": [ext("synthetic", valueBoolean=True),
                                      ext("hcn-version", valueString=rng.choice(["AB", "CD", "EF", "GH"]))]})
    out["identifier"] = identifiers
    city, letter = rng.choice(CITIES)
    out["address"] = [{"use": "home", "line": [f"{rng.randint(10, 999)} {rng.choice(STREETS)}"], "city": city,
                       "state": "ON", "postalCode": _postal(rng, letter), "country": "CA"}]
    if patient.get("telecom"):
        out["telecom"] = [{"system": "phone", "value": f"+1-416-555-01{rng.randint(0, 99):02d}", "use": "home"}]
    roll = rng.random()
    lang = "zh-CN" if roll < 0.2 else ("zh-TW" if roll < 0.3 else "en")
    out["communication"] = [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": lang,
                                                      "display": LANGUAGE_DISPLAY[lang]}]}, "preferred": True}]
    out.pop("extension", None)  # Synthea's US race/ethnicity/birthplace extensions
    return out


def localize_bundle(bundle: dict, beds: list[str], seed: int = 42) -> dict:
    """A Synthea patient bundle, localized. `beds` are bed Location ids handed out to inpatient stays."""
    out = {**bundle, "entry": []}
    bed_rng = _rng(seed, "beds")
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType")
        if rtype == "Patient":
            resource = localize_patient(resource, seed)
        elif rtype == "Encounter" and (resource.get("class") or {}).get("code") == "IMP":
            resource = dict(resource)
            resource.setdefault("hospitalization", {"admitSource": C.concept(C.ADMIT_SOURCE, "emd"),
                                                    "dischargeDisposition": C.concept(C.DISCHARGE_DISPOSITION, "home")})
            if beds:
                period = resource.get("period", {})
                resource["location"] = [{"location": {"reference": f"Location/{bed_rng.choice(beds)}"},
                                         "status": "completed" if period.get("end") else "active", "period": period}]
        elif rtype in ("Organization", "Location") and resource.get("address"):
            resource = dict(resource)
            rng = _rng(seed, resource.get("id", ""))
            city, letter = rng.choice(CITIES)
            resource["address"] = {"line": [f"{rng.randint(10, 999)} {rng.choice(STREETS)}"], "city": city,
                                   "state": "ON", "postalCode": _postal(rng, letter), "country": "CA"}
        out["entry"].append({**entry, "resource": resource})
    return out
