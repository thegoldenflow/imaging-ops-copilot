"""WP2 demo: the FHIR gateway, its write allow-list, de-identification and the imaging exam mapping.

    cd apps/api && uv run python scripts/gateway_demo.py [--backend local|hapi] [--unit MEDA]

Runs against the database in DATABASE_URL (the API's demo data). Reads go through
FhirGateway as the operations manager; one write the AI layer may make (a Task) is
kept, one it may not (a lab result) is refused, and both land in the audit log.
See demo/fhir_gateway.md for the talk track.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.store import unit_of_work  # noqa: E402
from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway, LocalBackend  # noqa: E402
from app.ehr.imaging import exam_from_fhir, exam_names, exam_to_fhir, load_exam  # noqa: E402
from app.llm.fhir_deid import FhirDeidentifier  # noqa: E402


def heading(text: str) -> None:
    print(f"\n== {text} ==")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backend", choices=["local", "hapi"], default="local")
    parser.add_argument("--unit", default="MEDA")
    args = parser.parse_args()
    with unit_of_work() as store:
        if args.backend == "hapi":
            from app.ehr.hapi import HapiBackend

            backend = HapiBackend()
        else:
            backend = LocalBackend(store)
        ops = next(u for u in store.staff.values() if u.role == "operations_manager")
        fhir = FhirGateway(Actor.of(ops), module="gateway_demo", backend=backend)

        heading(f"Bed board {args.unit} ({backend.name} backend), as {ops.name}")
        board = fhir.get_bed_board(args.unit)
        print(f"{board.unit_name}: {len(board.beds)} beds, by status {board.counts()}")
        bed = next(b for b in board.beds if b.encounter_id)
        print(f"bed {bed.id}: encounter {bed.encounter_id}, patient {bed.patient_id}, since {bed.since:%a %H:%M}")

        # Clinical content is not the bed manager's (6.3, WP4): the stay is read as a physician of the unit.
        doctor = next((u for u in store.staff.values() if u.role == "physician" and args.unit in u.unit_ids), None)
        clinical = FhirGateway(Actor.of(doctor) if doctor else Actor.system("gateway-demo"),
                               module="discharge_summary", backend=backend)
        heading(f"The patient's stay through typed reads, as {doctor.name if doctor else 'a system job'}")
        patient = clinical.read("Patient", bed.patient_id)
        mrn = next(i.value for i in patient.identifier if i.system.endswith(":mrn"))
        stay = clinical.get_active_encounter(mrn)
        orders, meds = clinical.get_orders(stay.id), clinical.get_medications(stay.id)
        vitals = clinical.get_observations(stay.id, codes=["8867-4"])
        print(f"active encounter {stay.id} ({stay.class_.code}, since {stay.period.start[:16]})")
        print(f"{len(orders)} orders, {len(meds)} medication orders, {len(clinical.get_home_meds(mrn))} home medications")
        if vitals:
            hr = next(c.valueQuantity.value for c in vitals[-1].component if c.code.coding[0].code == "8867-4")
            print(f"latest heart rate {hr}/min at {vitals[-1].effectiveDateTime[:16]}")

        heading("What a model would be given: de-identified")
        deid = FhirDeidentifier()
        p, e = deid.redact_all([patient.to_fhir(), stay.to_fhir()])
        print(json.dumps({k: p[k] for k in ("id", "identifier", "name", "extension") if k in p}, ensure_ascii=False)[:400])
        print("encounter subject stays a reference:", e["subject"])

        heading("Writes: the allow-list")
        task = clinical.create({"resourceType": "Task", "status": "requested", "intent": "proposal", "priority": "routine",
                            "description": "Demo: review discharge readiness", "for": stay.subject.to_fhir(),
                            "encounter": {"reference": f"Encounter/{stay.id}"}})
        print(f"created Task/{task.id} (allowed: the AI layer writes work items)")
        try:
            clinical.create({"resourceType": "Observation", "status": "final", "code": {"text": "Potassium"},
                         "subject": stay.subject.to_fhir()})
        except FhirAccessDenied as refusal:
            print(f"refused Observation: {refusal}")
        for event in store.audit.events()[-3:]:
            print(f"audit #{event.seq}: {event.user_name} {event.action} {event.resource_type}/{event.resource_id} "
                  f"{event.outcome} (module {event.module}{'; ' + event.reason if event.reason else ''})")

        heading("An imaging exam as FHIR, and back")
        appt = next(a for a in store.appointments.values() if a.status == "completed")
        exam = load_exam(store, appointment_id=appt.id)
        resources = exam_to_fhir(exam, exam_names(store))
        print(" + ".join(f"{r['resourceType']}/{r['id']}" for r in resources.all()))
        back = exam_from_fhir(resources.service_request, resources.encounter, resources.diagnostic_report)
        print("lossless round trip:", back == exam)
    return 0


if __name__ == "__main__":
    sys.exit(main())
