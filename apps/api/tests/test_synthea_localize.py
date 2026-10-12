"""WP1: localizing Synthea output and exporting bundles for HAPI (spec 6.2 steps 3-5)."""

import json
from collections import Counter
from pathlib import Path

from app.ehr import codes as C
from app.ehr.bundles import patient_bundles, shared_bundles
from app.ehr.synthea import localize_bundle, localize_patient

SAMPLE = Path(__file__).resolve().parents[1] / "scripts" / "samples" / "synthea" / "Ann_Example_sample.json"


def _sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_localized_patient_has_no_us_identifiers_and_a_synthetic_card():
    bundle = localize_bundle(_sample(), beds=["MEDA-01-A"])
    patient = bundle["entry"][0]["resource"]
    systems = [i["system"] for i in patient["identifier"]]
    assert systems == [C.MRN_SYSTEM, C.HCN_SYSTEM]
    assert "999-00-0000" not in json.dumps(bundle) and "S99900000" not in json.dumps(bundle)
    hcn = patient["identifier"][1]
    assert len(hcn["value"]) == 10 and {"url": C.EXT + "synthetic", "valueBoolean": True} in hcn["extension"]
    address = patient["address"][0]
    assert address["state"] == "ON" and address["country"] == "CA" and len(address["postalCode"]) == 7
    assert "extension" not in patient  # US race / birthplace extensions removed
    assert patient["telecom"][0]["value"].startswith("+1-416-555-01")


def test_codes_are_kept_and_inpatient_encounters_get_a_bed():
    original = _sample()
    bundle = localize_bundle(original, beds=["MEDA-01-A"])
    encounter = bundle["entry"][1]["resource"]
    assert encounter["location"][0]["location"]["reference"] == "Location/MEDA-01-A"
    assert encounter["hospitalization"]["admitSource"]["coding"][0]["code"] == "emd"
    for before, after in zip(original["entry"][2:], bundle["entry"][2:], strict=True):
        assert before == after  # Condition (SNOMED), MedicationRequest (RxNorm), Observation (LOINC) unchanged


def test_language_injection_is_about_thirty_percent_and_repeatable():
    patient = _sample()["entry"][0]["resource"]
    langs = Counter(localize_patient({**patient, "id": f"p{i}"})["communication"][0]["language"]["coding"][0]["code"]
                    for i in range(2000))
    assert 0.26 <= (langs["zh-CN"] + langs["zh-TW"]) / 2000 <= 0.34
    assert localize_patient(patient) == localize_patient(patient)


def test_export_bundles_keep_ids_and_put(fresh_state):
    shared = dict(shared_bundles(fresh_state))
    locations = shared["locations"]["entry"]
    kinds = [e["resource"]["physicalType"]["coding"][0]["code"] for e in locations]
    assert kinds == sorted(kinds, key=["wa", "ro", "bd"].index)  # parents first
    pid, bundle = next(patient_bundles(fresh_state, ["pat-0007"]))
    assert bundle["type"] == "transaction" and bundle["entry"][0]["resource"]["id"] == "pat-0007"
    assert all(e["request"]["method"] == "PUT" and "meta" not in e["resource"] for e in bundle["entry"])
    assert len(bundle["entry"]) > 3
