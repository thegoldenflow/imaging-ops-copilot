"""Medication safety (spec 7.2) in the terminal: the 8.2 case from order to signed reconciliation.

    cd apps/api && uv run python scripts/med_safety_demo.py            # rolled back afterwards
    cd apps/api && uv run python scripts/med_safety_demo.py --commit   # keep the decisions and signatures

1. The intake sweep reviews the new orders (rules) and opens the reconciliations.
2. The 8.2 case: naproxen added for a hip-fracture patient on warfarin (INR 3.8). The interaction rule fires
   (major); the AI commentary adds context with its confidence and evidence; the pharmacist asks the prescriber
   to change the order (a Task), the order itself is untouched.
3. The same patient's admission reconciliation: the antihypertensive missed at admission (omission), warfarin with
   enoxaparin (duplicate therapy), naproxen's indication judged by the AI. The pharmacist handles every item and
   signs; a physician of the unit co-signs; the MedRec record is final, authenticated by the pharmacist.
4. A discharge reconciliation on Medicine A and its discharge medication table (the 7.3 interface).
Uses the mock model unless LLM_PROVIDER selects a configured one.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    from app.core.models import Role
    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store
    from app.ehr.gateway import Actor, FhirGateway
    from app.fhir.dt import ref_id
    from app.llm.gateway import LlmGateway, get_gateway, set_gateway
    from app.llm.providers import MockProvider
    from app.modules.med_safety import discharge
    from app.modules.med_safety import service as S
    from app.modules.med_safety.models import reviews
    from app.modules.med_safety.reconcile import RecItem

    if get_gateway().mode == "mock":
        set_gateway(LlmGateway(MockProvider(latency_s=0)))
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        pharmacist = next(u for u in store.staff.values() if u.role == Role.PHARMACIST and u.demo_login)
        print(f"AI mode: {get_gateway().mode}. Pharmacist: {pharmacist.name}.\n")
        counts = S.intake(store, force=True)
        print(f"1. Intake: {counts['reviews']} new orders reviewed by the rules, {counts['admission']} admission and "
              f"{counts['discharge']} discharge reconciliations opened.")
        queue = S.queue(store, pharmacist)
        print(f"   Pharmacist queue: {queue['counts']['blocking']} blocking, {queue['counts']['pending']} to review. "
              f"Top 3:")
        for item in queue["items"][:3]:
            print(f"   - [{'BLOCKING' if item['blocking'] else item['severity']}] {item['title']} "
                  f"(MRN {item['mrn']}, {item['unit_id']}, waiting {item['wait_minutes']} min)")

        naproxen = reviews(store).get("MedicationRequest/medrx-ms-82-naproxen")
        header = S.patient_header(pharmacist, naproxen.patient_id, naproxen.encounter_id)
        print(f"\n2. The 8.2 case: {header['age']}-year-old {header['gender']}, {header['reason']}, "
              f"INR {header['inr']['value'] if header['inr'] else '-'}; order: naproxen {naproxen.order['text']}.")
        for f in naproxen.findings:
            print(f"   Rule {f['rule_name']} [{f['severity']}]: {f['title']}. {f['detail'][:160]}")
        naproxen = S.comment(store, naproxen.id)
        c = naproxen.commentary
        print(f"   AI commentary ({naproxen.commentary_status}, run {c.get('run_id')}, review Task "
              f"{naproxen.review_task_id}):")
        for concern in c["concerns"]:
            print(f"   - AI {concern['type']} [{concern['severity']}, confidence {concern['confidence']:.2f}]: "
                  f"{concern['explanation'][:140]} (evidence: {', '.join(concern['evidence_refs'])})")
        for alt in c["alternatives"]:
            print(f"   - Alternative: {alt['suggestion']}")
        print(f"   Guidelines: {', '.join(c['guideline_refs'])}")
        decided = S.decide_review(store, pharmacist, naproxen.id, "request_change",
                                  "Major interaction with warfarin and INR 3.8: stop naproxen, use acetaminophen")
        order = FhirGateway(Actor.system("demo"), "order_review").read("MedicationRequest", "medrx-ms-82-naproxen")
        print(f"   Pharmacist: request_change -> prescriber Task {decided.decision['change_task_id']}; "
              f"the order is still {order.status} (the AI layer never edits orders).")

        session = S.settle(store, S.reconcile(store, naproxen.encounter_id, "admission").id)
        items = [RecItem.model_validate(i) for i in session.items]
        print(f"\n3. Admission reconciliation {session.id} ({session.ai_status}; draft record "
              f"DocumentReference/{session.document_id}):")
        for i in items:
            source = f"AI {i.confidence:.2f}" if i.source == "ai" and i.confidence is not None else i.source
            home = f"{i.home.name} {i.home.text}" if i.home else "-"
            order_line = f"{i.order.name} {i.order.text}" if i.order else "-"
            print(f"   - {i.type:<23} [{source:<8}] home: {home:<36} order: {order_line}")
        for i in items:
            if i.discrepancy:
                if i.type == "omission":
                    S.handle_item(store, pharmacist, session.id, i.id, "modify", "Held by mistake at admission",
                                  change=f"Resume {i.home.name} {i.home.text}")
                else:
                    S.handle_item(store, pharmacist, session.id, i.id, "accept",
                                  "Bridging with enoxaparin until warfarin restarts" if i.type == "duplicate_therapy"
                                  else "Intended by the team")
        signed = S.sign(store, pharmacist, session.id)
        print(f"   Pharmacist handled {sum(i.discrepancy for i in items)} discrepancies and signed: {signed.status}; "
              f"prescriber Tasks {', '.join(signed.change_tasks) or '-'}.")
        unit = signed.unit_id
        physician = next(u for u in store.staff.values() if u.role == Role.PHYSICIAN and unit in u.unit_ids)
        final = S.sign(store, physician, session.id)
        doc = FhirGateway(Actor.system("demo"), "medication_reconciliation").read("DocumentReference",
                                                                                   final.document_id).to_fhir()
        print(f"   {physician.name} ({unit}) co-signed: {final.status}; DocumentReference/{doc['id']} docStatus "
              f"{doc['docStatus']}, authenticator {doc['authenticator'].get('display')}.")

        plans = FhirGateway(Actor.system("demo"), "medication_reconciliation").search("MedicationRequest",
                                                                                      category="plan")
        stays = sorted({ref_id(p["encounter"]) for p in plans})
        meda = next(s for s in stays if S._unit_of_encounter(
            FhirGateway(Actor.system("demo"), "medication_reconciliation").read("Encounter", s).to_fhir()) == "MEDA")
        dsession = S.settle(store, S.reconcile(store, meda, "discharge").id)
        S.accept_hospital_use(store, pharmacist, dsession.id, "Hospital use only; stops at discharge")
        for i in [RecItem.model_validate(x) for x in S._medrec_session(store, dsession.id).items]:
            if i.discrepancy and i.action is None:
                S.handle_item(store, pharmacist, dsession.id, i.id, "accept", "Discussed with the attending")
        S.sign(store, pharmacist, dsession.id)
        cosigner = next(u for u in store.staff.values() if u.role == Role.PHYSICIAN and u.demo_login)
        S.sign(store, cosigner, dsession.id)
        table = discharge.discharge_medication_table(FhirGateway(Actor.of(cosigner), "discharge_summary"), meda)
        print(f"\n4. Discharge reconciliation {dsession.id} on Medicine A ({meda}), signed and co-signed. "
              f"Discharge medication table ({table.status}, {table.document}):")
        for category in ("new", "changed", "stopped", "continued"):
            rows = getattr(table, category)
            print(f"   {category:<9}: " + ("; ".join(f"{m.name} {m.dose_text or ''}".strip()
                                                 + (f" (was {m.previous_text})" if category == "changed" else "")
                                                 for m in rows) or "-"))
        if args.commit:
            outer.commit()
            print("\nCommitted.")
        else:
            print("\nRolled back (use --commit to keep it).")
    finally:
        set_ambient_store(None)
        if outer.is_active:
            outer.rollback()
        conn.close()


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
