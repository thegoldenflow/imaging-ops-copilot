"""WP1: the synthetic hospital in the PostgreSQL FHIR store (spec 6.2)."""

import random
from collections import Counter
from datetime import datetime

from sqlalchemy import select, text

from app.core.store import Store, use_store
from app.ehr import codes as C
from app.ehr.fhirstore import fhir_resources
from app.ehr.reference import UNITS
from app.fhir.dt import parse, ref_id

CLINICAL = ["Observation", "Condition", "MedicationRequest", "MedicationStatement", "AllergyIntolerance",
            "Procedure", "DiagnosticReport", "ServiceRequest"]


def test_location_tree_has_three_levels_and_spec_bed_counts(fresh_state):
    fhir = fresh_state.fhir
    for unit in UNITS:
        beds = fhir.search("Location", unit=unit.id, code="bd")
        assert len(beds) == unit.beds, unit.id
        for bed in beds[:3]:
            room = fhir.read("Location", ref_id(bed["partOf"]))
            assert room["physicalType"]["coding"][0]["code"] == "ro"
            assert ref_id(room["partOf"]) == unit.id
    assert fhir.read("Location", "ORTH")["physicalType"]["coding"][0]["code"] == "wa"


def test_thousand_localized_patients(fresh_state):
    patients = fresh_state.fhir.search("Patient")
    assert len(patients) == 1000
    langs = Counter(p["communication"][0]["language"]["coding"][0]["code"] for p in patients)
    assert set(langs) == {"en", "zh-CN", "zh-TW"}
    assert 0.25 <= (langs["zh-CN"] + langs["zh-TW"]) / 1000 <= 0.35
    for p in patients[:50]:
        systems = {i["system"]: i for i in p["identifier"]}
        assert systems[C.MRN_SYSTEM]["value"].isdigit()
        hcn = systems[C.HCN_SYSTEM]
        assert len(hcn["value"]) == 10 and {"url": C.EXT + "synthetic", "valueBoolean": True} in hcn["extension"]
        address = p["address"][0]
        assert address["state"] == "ON" and len(address["postalCode"]) == 7 and address["postalCode"][3] == " "
        assert "ssn" not in str(p).lower()


def test_lookup_by_mrn_uses_blind_index(fresh_state):
    patient = fresh_state.fhir.read("Patient", "pat-0042")
    mrn = next(i["value"] for i in patient["identifier"] if i["system"] == C.MRN_SYSTEM)
    found = fresh_state.fhir.search("Patient", mrn=mrn)
    assert [p["id"] for p in found] == ["pat-0042"]
    assert fresh_state.fhir.search("Patient", mrn="00000000") == []


def test_resource_bodies_are_encrypted_in_sql(fresh_state):
    patient = fresh_state.fhir.read("Patient", "pat-0001")
    family = patient["name"][0]["family"]
    t = fhir_resources
    row = fresh_state.conn().execute(
        select(t.c.data, t.c.mrn_bidx).where(t.c.resource_type == "Patient", t.c.id == "pat-0001")).one()
    assert row.data.startswith("k1:") and family not in row.data
    assert len(row.mrn_bidx) == 64


def test_every_clinical_resource_references_an_encounter(fresh_state):
    t = fhir_resources
    missing = fresh_state.conn().execute(
        select(t.c.resource_type, t.c.id).where(t.c.resource_type.in_(CLINICAL), t.c.encounter.is_(None)).limit(5)).all()
    assert missing == []
    # ... and the encounter exists
    encounters = set(fresh_state.conn().execute(select(t.c.id).where(t.c.resource_type == "Encounter")).scalars())
    refs = set(fresh_state.conn().execute(
        select(t.c.encounter).where(t.c.resource_type.in_(CLINICAL)).distinct()).scalars())
    assert refs <= encounters


def test_census_matches_bed_status(fresh_state):
    fhir = fresh_state.fhir
    clock = parse(fresh_state.modules["hospital_clock"]["now"])
    assert clock.hour == 7
    inpatients = fhir.search("Encounter", cls="IMP", status="in-progress")
    beds = {ref_id(e["location"][-1]["location"]) for e in inpatients}
    assert len(beds) == len(inpatients)  # one patient per bed
    occupied = {loc["id"] for loc in fhir.search("Location", code="bd", status="O")}
    assert occupied == beds
    capacity = sum(u.beds for u in UNITS if u.kind != "ed")
    assert 0.7 * capacity <= len(occupied) <= capacity
    ed = fhir.search("Encounter", cls="EMER", status=["arrived", "triaged", "in-progress"])
    assert ed, "the ED is never empty at 07:00 in this data set"


def test_history_supports_the_flow_models(fresh_state):
    fhir = fresh_state.fhir
    assert fhir.count("Encounter", cls="EMER", status="finished") > 2500
    ctas = fhir.search("Observation", code=C.CTAS[0], limit=200)
    assert {o["valueInteger"] for o in ctas} <= {1, 2, 3, 4, 5}
    procedures = fhir.search("Procedure", status="completed", limit=100)
    assert all(p["performer"][0]["actor"]["reference"].startswith("Practitioner/prac-surg") for p in procedures)
    today = parse(fresh_state.modules["hospital_clock"]["now"]).replace(hour=0)
    or_today = fhir.search("Appointment", date_from=today, date_to=today.replace(hour=23, minute=59))
    assert or_today and all(a["participant"][1]["actor"]["reference"].startswith("Location/OR-") for a in or_today)
    preop = fhir.search("Task", code=list(C.PREOP_CHECKS))
    assert preop and any(t["status"] == "requested" for t in preop)


def test_plan_events_are_ordered_and_resolvable(fresh_state):
    plan = fresh_state.modules["hospital_plan"]
    events = plan["events"]
    assert events == sorted(events, key=lambda e: e["at"])
    kinds = Counter(e["kind"] for e in events)
    assert {"ed_arrival", "admit", "discharge", "surgery_start", "result", "bed_clean"} <= set(kinds)
    for e in events:
        for key, table in (("visit", "visits"), ("stay", "stays"), ("surgery", "surgeries"), ("order", "orders")):
            if key in e:
                assert e[key] in plan[table]


def test_write_read_update_and_search(fresh_state):
    fhir = fresh_state.fhir
    created = fhir.create({"resourceType": "Task", "status": "requested", "intent": "order",
                           "for": {"reference": "Patient/pat-0001"}, "authoredOn": "2026-10-08T07:30:00-04:00"})
    assert created["meta"]["versionId"] == "1"
    created["status"] = "completed"
    updated = fhir.update(created)
    assert updated["meta"]["versionId"] == "2"
    assert fhir.read("Task", created["id"])["status"] == "completed"
    assert created["id"] in [t["id"] for t in fhir.search("Task", patient="pat-0001", status="completed")]
    # Returned dicts are copies: changing one does not change the store
    updated["status"] = "cancelled"
    assert fhir.read("Task", created["id"])["status"] == "completed"


def test_generation_is_deterministic(fresh_state):
    """Same seed and day -> same ids and content (eval cases refer to seeded resource ids)."""
    from app.core.config import settings
    from app.ehr.seed import generate

    seed_time = fresh_state.modules["seed_time"]
    again = Store()
    with use_store(again):
        generate(again, settings.seed, seed_time)
    sample = random.Random(1).sample(sorted(again.fhir._mem), 60)
    for rtype, rid in sample:
        stored = fresh_state.fhir.read(rtype, rid)
        fresh = again.fhir.read(rtype, rid)
        assert stored is not None, (rtype, rid)
        stored.pop("meta"), fresh.pop("meta")
        assert stored == fresh, (rtype, rid)


def test_reset_rewrites_the_fhir_store(fresh_state):
    from app.core.store import reset_store

    fresh_state.fhir.create({"resourceType": "Flag", "id": "flag-temp", "status": "active",
                             "code": {"text": "temp"}, "subject": {"reference": "Patient/pat-0001"}})
    reset_store()
    assert fresh_state.fhir.read("Flag", "flag-temp") is None
    count = fresh_state.conn().execute(text("SELECT count(*) FROM fhir_resources WHERE resource_type = 'Patient'")).scalar()
    assert count == 1000
    assert isinstance(fresh_state.modules["seed_time"], datetime)
