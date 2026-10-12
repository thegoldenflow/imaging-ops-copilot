"""FHIR transaction bundles for loading the hospital into an external FHIR server (HAPI).

Resources keep their ids (PUT), so loading twice gives the same result.
Order: organization and practitioners, then the Location tree, then one bundle
per patient with everything that refers to that patient.
"""

from __future__ import annotations

from collections.abc import Iterator

from app.core.store import Store

SHARED_TYPES = ("Organization", "Practitioner", "PractitionerRole")
PATIENT_TYPES = ("Patient", "Encounter", "Condition", "AllergyIntolerance", "MedicationStatement", "MedicationRequest",
                 "ServiceRequest", "Observation", "DiagnosticReport", "Procedure", "Appointment", "Task", "Flag",
                 "Consent", "Communication", "DocumentReference", "CarePlan", "EpisodeOfCare")


def transaction(resources: list[dict]) -> dict:
    entries = []
    for resource in resources:
        body = {k: v for k, v in resource.items() if k != "meta"}
        entries.append({"fullUrl": f"{resource['resourceType']}/{resource['id']}", "resource": body,
                        "request": {"method": "PUT", "url": f"{resource['resourceType']}/{resource['id']}"}})
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def shared_bundles(store: Store) -> Iterator[tuple[str, dict]]:
    yield "organization-and-practitioners", transaction([r for t in SHARED_TYPES for r in store.fhir.search(t)])
    # Parents before children: units, rooms, beds
    locations = store.fhir.search("Location")
    order = {"wa": 0, "ro": 1, "bd": 2}
    locations.sort(key=lambda loc: order.get(loc["physicalType"]["coding"][0]["code"], 3))
    yield "locations", transaction(locations)


def patient_bundles(store: Store, patient_ids: list[str] | None = None) -> Iterator[tuple[str, dict]]:
    ids = patient_ids if patient_ids is not None else store.fhir.ids("Patient")
    for pid in ids:
        resources = [store.fhir.read("Patient", pid)]
        for rtype in PATIENT_TYPES[1:]:
            resources += store.fhir.search(rtype, patient=pid)
        yield pid, transaction([r for r in resources if r])
