"""Factories for the FHIR resources the hospital generator writes.

Each factory returns the resource dict and stores it (`store.fhir.create`).
Ids are deterministic: one counter per prefix, and sections of the generator
run in a fixed order with their own random streams, so adding a section at the
end never changes the ids or values of the earlier ones.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime

from app.core.store import Store
from app.ehr import codes as C
from app.fhir.dt import fhir_date, fhir_datetime, ref


def ext(name: str, **value) -> dict:
    """An extension `urn:demo-hospital:ext:<name>` with one value[x] (e.g. valueCode="x")."""
    return {"url": C.EXT + name, **value}


class Builder:
    def __init__(self, store: Store, seed: int, now: datetime) -> None:
        self.store = store
        self.seed = seed
        self.now = now
        self._counters: dict[str, int] = defaultdict(int)
        self.written = 0

    def rng(self, section: str) -> random.Random:
        return random.Random(f"hospital:{self.seed}:{section}")

    def next_id(self, prefix: str, width: int = 6) -> str:
        self._counters[prefix] += 1
        return f"{prefix}-{self._counters[prefix]:0{width}d}"

    def add(self, resource: dict) -> dict:
        self.written += 1
        return self.store.fhir.create(resource)

    # ---------- clinical resources ----------

    def encounter(self, pid: str, cls: str, start: datetime, *, end: datetime | None = None,
                  status: str | None = None, service: str | None = None, reason: dict | None = None,
                  locations: list[tuple[str, datetime, datetime | None]] | None = None,
                  status_history: list[tuple[str, datetime, datetime | None]] | None = None,
                  admit_source: str | None = None, disposition: str | None = None,
                  based_on: str | None = None, practitioner: str | None = None,
                  extensions: list[dict] | None = None, rid: str | None = None) -> dict:
        display = {"EMER": "emergency", "IMP": "inpatient encounter", "AMB": "ambulatory"}[cls]
        resource: dict = {
            "resourceType": "Encounter", "id": rid or self.next_id("enc"),
            "status": status or ("finished" if end else "in-progress"),
            "class": {"system": C.ACT_CODE, "code": cls, "display": display},
            "subject": ref("Patient", pid),
            "period": {"start": fhir_datetime(start), **({"end": fhir_datetime(end)} if end else {})},
        }
        if service:
            resource["serviceType"] = C.concept(C.LOCAL, service, service)
        if reason:
            resource["reasonCode"] = [reason]
        if status_history:
            resource["statusHistory"] = [
                {"status": s, "period": {"start": fhir_datetime(a), **({"end": fhir_datetime(b)} if b else {})}}
                for s, a, b in status_history]
        if practitioner:
            resource["participant"] = [{"individual": ref("Practitioner", practitioner)}]
        if locations:
            resource["location"] = [
                {"location": ref("Location", loc), "status": "completed" if until else "active",
                 "period": {"start": fhir_datetime(since), **({"end": fhir_datetime(until)} if until else {})}}
                for loc, since, until in locations]
        if admit_source or disposition:
            hosp: dict = {}
            if admit_source:
                hosp["admitSource"] = C.concept(C.ADMIT_SOURCE, admit_source)
            if disposition:
                hosp["dischargeDisposition"] = C.concept(C.DISCHARGE_DISPOSITION, disposition)
            resource["hospitalization"] = hosp
        if based_on:
            resource["basedOn"] = [ref("ServiceRequest", based_on)]
        if extensions:
            resource["extension"] = extensions
        resource["serviceProvider"] = ref("Organization", "org-demo-hospital")
        return self.add(resource)

    def observation(self, pid: str, enc: str | None, code: tuple[str, str], when: datetime, *,
                    value: float | int | str | None = None, unit: str | None = None,
                    category: str = "vital-signs", system: str = C.LOINC,
                    components: list[tuple[str, float, str]] | None = None, status: str = "final",
                    interpretation: str | None = None, rid: str | None = None) -> dict:
        resource: dict = {
            "resourceType": "Observation", "id": rid or self.next_id("obs", 7), "status": status,
            "category": [C.concept(C.OBS_CATEGORY, category)],
            "code": C.concept(system, code[0], code[1]),
            "subject": ref("Patient", pid), "effectiveDateTime": fhir_datetime(when),
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if isinstance(value, str):
            resource["valueString"] = value
        elif isinstance(value, int) and not unit:
            resource["valueInteger"] = value
        elif value is not None:
            resource["valueQuantity"] = {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}
        if components:
            resource["component"] = [
                {"code": C.concept(C.LOINC, c, C.VITALS[c][0]),
                 "valueQuantity": {"value": v, "unit": u, "system": "http://unitsofmeasure.org", "code": u}}
                for c, v, u in components]
        if interpretation:
            resource["interpretation"] = [C.concept("http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", interpretation)]
        return self.add(resource)

    def condition(self, pid: str, code: str, onset: datetime | date, *, enc: str | None = None,
                  category: str = "problem-list-item", clinical: str = "active",
                  abated: datetime | None = None) -> dict:
        resource: dict = {
            "resourceType": "Condition", "id": self.next_id("cond"),
            "clinicalStatus": C.concept(C.CONDITION_CLINICAL, clinical),
            "category": [C.concept(C.CONDITION_CATEGORY, category)],
            "code": C.snomed(code), "subject": ref("Patient", pid),
            "onsetDateTime": fhir_datetime(onset) if isinstance(onset, datetime) else fhir_date(onset),
            "recordedDate": fhir_datetime(onset) if isinstance(onset, datetime) else fhir_date(onset),
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if abated:
            resource["abatementDateTime"] = fhir_datetime(abated)
        return self.add(resource)

    @staticmethod
    def dosage(rxcui: str, *, dose: float | None = None, per_day: int | None = None, prn: bool = False,
               route: str | None = None, text: str | None = None) -> dict:
        name, _cls, typical, unit, freq, default_route = C.MEDICATIONS[rxcui]
        dose = typical if dose is None else dose
        per_day = freq if per_day is None else per_day
        route = route or default_route
        label = {1: "once daily", 2: "twice daily", 3: "three times daily", 4: "four times daily"}.get(per_day, f"{per_day} times daily")
        dose_text = f"{dose:g} {unit.strip('[]')}"
        return {
            "text": text or f"{dose_text} {route} {label}{' as needed' if prn else ''}",
            "timing": {"repeat": {"frequency": per_day, "period": 1, "periodUnit": "d"}},
            "route": {"text": route},
            "asNeededBoolean": prn,
            "doseAndRate": [{"doseQuantity": {"value": dose, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}}],
        }

    def medication_request(self, pid: str, enc: str | None, rxcui: str, authored: datetime, *,
                           intent: str = "order", status: str = "active", requester: str | None = None,
                           dose: float | None = None, per_day: int | None = None, prn: bool = False,
                           route: str | None = None, reason: str | None = None, note: str | None = None,
                           category: str = "inpatient") -> dict:
        resource: dict = {
            "resourceType": "MedicationRequest", "id": self.next_id("medrx"), "status": status, "intent": intent,
            "category": [C.concept("http://terminology.hl7.org/CodeSystem/medicationrequest-category", category)],
            "medicationCodeableConcept": C.rxnorm(rxcui), "subject": ref("Patient", pid),
            "authoredOn": fhir_datetime(authored),
            "dosageInstruction": [self.dosage(rxcui, dose=dose, per_day=per_day, prn=prn, route=route)],
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if requester:
            resource["requester"] = ref("Practitioner", requester)
        if reason:
            resource["reasonCode"] = [C.snomed(reason)]
        if note:
            resource["note"] = [{"text": note}]
        return self.add(resource)

    def medication_statement(self, pid: str, rxcui: str, start: date, *, status: str = "active", enc: str | None = None,
                             dose: float | None = None, per_day: int | None = None, reason: str | None = None) -> dict:
        resource: dict = {
            "resourceType": "MedicationStatement", "id": self.next_id("medst"), "status": status,
            "medicationCodeableConcept": C.rxnorm(rxcui), "subject": ref("Patient", pid),
            "effectivePeriod": {"start": fhir_date(start)},
            "dateAsserted": fhir_date(start),
            "informationSource": {"display": "Provincial drug history (synthetic DHDR stand-in)"},
            "dosage": [self.dosage(rxcui, dose=dose, per_day=per_day)],
        }
        if reason:
            resource["reasonCode"] = [C.snomed(reason)]
        if enc:
            resource["context"] = ref("Encounter", enc)
        return self.add(resource)

    def allergy(self, pid: str, allergen: tuple[str, str, str, str], recorded: date, *, criticality: str,
                reaction: str, severity: str, enc: str | None = None) -> dict:
        system, code, display, drug_class = allergen
        resource = {
            "resourceType": "AllergyIntolerance", "id": self.next_id("alg"),
            "clinicalStatus": C.concept(C.ALLERGY_CLINICAL, "active"),
            "category": ["medication" if drug_class else ("food" if code == "91935009" else "environment")],
            "criticality": criticality, "code": C.concept(system, code, display),
            "patient": ref("Patient", pid), "recordedDate": fhir_date(recorded),
            "reaction": [{"description": reaction, "severity": severity}],
        }
        if drug_class:
            resource["extension"] = [ext("drug-class", valueCode=drug_class)]
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        return self.add(resource)

    def procedure(self, pid: str, enc: str | None, code: str, start: datetime, end: datetime | None, *,
                  status: str = "completed", performer: str | None = None, location: str | None = None,
                  asa: int | None = None, urgency: str | None = None, based_on: str | None = None,
                  rid: str | None = None) -> dict:
        resource: dict = {
            "resourceType": "Procedure", "id": rid or self.next_id("proc"), "status": status,
            "code": C.snomed(code), "subject": ref("Patient", pid),
            "performedPeriod": {"start": fhir_datetime(start), **({"end": fhir_datetime(end)} if end else {})},
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if performer:
            resource["performer"] = [{"actor": ref("Practitioner", performer), "function": C.concept(C.SNOMED, "304292004", "Surgeon")}]
        if location:
            resource["location"] = ref("Location", location)
        if based_on:
            resource["basedOn"] = [ref("ServiceRequest", based_on)]
        exts = []
        if asa:
            exts.append(ext("asa-class", valueInteger=asa))
        if urgency:
            exts.append(ext("surgical-urgency", valueCode=urgency))
        if exts:
            resource["extension"] = exts
        return self.add(resource)

    def service_request(self, pid: str, enc: str | None, code: dict, authored: datetime, *,
                        category: str, status: str = "active", intent: str = "order", priority: str = "routine",
                        occurrence: datetime | None = None, requester: str | None = None,
                        location: str | None = None, reason: str | None = None, note: str | None = None,
                        rid: str | None = None) -> dict:
        resource: dict = {
            "resourceType": "ServiceRequest", "id": rid or self.next_id("sr"), "status": status, "intent": intent,
            "priority": priority, "category": [C.concept(C.LOCAL, category, category)], "code": code,
            "subject": ref("Patient", pid), "authoredOn": fhir_datetime(authored),
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if occurrence:
            resource["occurrenceDateTime"] = fhir_datetime(occurrence)
        if requester:
            resource["requester"] = ref("Practitioner", requester)
        if location:
            resource["locationReference"] = [ref("Location", location)]
        if reason:
            resource["reasonCode"] = [C.snomed(reason)]
        if note:
            resource["note"] = [{"text": note}]
        return self.add(resource)

    def diagnostic_report(self, pid: str, enc: str | None, code: dict, when: datetime, *, conclusion: str,
                          results: list[str] | None = None, category: str = "LAB", based_on: str | None = None,
                          status: str = "final") -> dict:
        resource: dict = {
            "resourceType": "DiagnosticReport", "id": self.next_id("dr"), "status": status,
            "category": [C.concept("http://terminology.hl7.org/CodeSystem/v2-0074", category)],
            "code": code, "subject": ref("Patient", pid), "effectiveDateTime": fhir_datetime(when),
            "issued": fhir_datetime(when), "conclusion": conclusion,
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if results:
            resource["result"] = [ref("Observation", r) for r in results]
        if based_on:
            resource["basedOn"] = [ref("ServiceRequest", based_on)]
        return self.add(resource)

    def appointment(self, pid: str, start: datetime, end: datetime, *, status: str, service: dict,
                    location: str | None = None, practitioner: str | None = None, based_on: str | None = None,
                    encounter: str | None = None, minutes: int | None = None, extensions: list[dict] | None = None,
                    description: str | None = None) -> dict:
        participants = [{"actor": ref("Patient", pid), "status": "accepted"}]
        if location:
            participants.append({"actor": ref("Location", location), "status": "accepted"})
        if practitioner:
            participants.append({"actor": ref("Practitioner", practitioner), "status": "accepted"})
        resource: dict = {
            "resourceType": "Appointment", "id": self.next_id("appt"), "status": status,
            "serviceType": [service], "start": fhir_datetime(start), "end": fhir_datetime(end),
            "minutesDuration": minutes or int((end - start).total_seconds() // 60), "participant": participants,
        }
        if description:
            resource["description"] = description
        if based_on:
            resource["basedOn"] = [ref("ServiceRequest", based_on)]
        exts = list(extensions or [])
        if encounter:
            exts.append(ext("encounter", valueReference=ref("Encounter", encounter)))
        if exts:
            resource["extension"] = exts
        return self.add(resource)

    def task(self, pid: str | None, code: tuple[str, str], authored: datetime, *, status: str = "requested",
             priority: str = "routine", enc: str | None = None, focus: str | None = None,
             owner_role: str | None = None, owner: str | None = None, description: str | None = None,
             due: datetime | None = None, intent: str = "order") -> dict:
        resource: dict = {
            "resourceType": "Task", "id": self.next_id("task"), "status": status, "intent": intent,
            "priority": priority, "code": C.concept(C.TASK_CODE, code[0], code[1]),
            "authoredOn": fhir_datetime(authored), "lastModified": fhir_datetime(authored),
        }
        if pid:
            resource["for"] = ref("Patient", pid)
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        if focus:
            resource["focus"] = {"reference": focus}
        if owner_role:
            resource["performerType"] = [C.concept(C.PRACTITIONER_ROLE, owner_role)]
        if owner:
            resource["owner"] = {"reference": owner}
        if description:
            resource["description"] = description
        if due:
            resource["restriction"] = {"period": {"end": fhir_datetime(due)}}
        return self.add(resource)

    def flag(self, pid: str, code: tuple[str, str], start: datetime, *, enc: str | None = None,
             status: str = "active", end: datetime | None = None) -> dict:
        resource: dict = {
            "resourceType": "Flag", "id": self.next_id("flag"), "status": status,
            "category": [C.concept("http://terminology.hl7.org/CodeSystem/flag-category", "clinical")],
            "code": C.concept(C.FLAG_CODE, code[0], code[1]), "subject": ref("Patient", pid),
            "period": {"start": fhir_datetime(start), **({"end": fhir_datetime(end)} if end else {})},
        }
        if enc:
            resource["encounter"] = ref("Encounter", enc)
        return self.add(resource)

    def consent(self, pid: str, category: str, when: datetime, *, permit: bool) -> dict:
        return self.add({
            "resourceType": "Consent", "id": self.next_id("consent"), "status": "active",
            "scope": C.concept(C.CONSENT_SCOPE, "patient-privacy"),
            "category": [C.concept(C.CONSENT_CATEGORY, category, category)],
            "patient": ref("Patient", pid), "dateTime": fhir_datetime(when),
            "provision": {"type": "permit" if permit else "deny", "purpose": [C.coding(C.CONSENT_CATEGORY, category)]},
        })
