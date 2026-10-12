"""Patient voice services (spec 7.4) in the terminal: a call-centre call and the Chinese follow-up call that escalates.

    cd apps/api && uv run python scripts/voice_demo.py            # rolled back afterwards
    cd apps/api && uv run python scripts/voice_demo.py --commit   # keep the calls, Tasks and Communications

1. The call centre (text-simulated): an FAQ answered from the knowledge base without identity, a reschedule after
   the identity check (name, date of birth, last four card digits), confirmed by the clerk in one click (the new time
   is booked and the old one cancelled in the same step), a clinical question passed to the nurse line.
2. The follow-up sweep: the stays discharged 48 h ago with every condition met get a scripted call; a stay missing a
   condition gets a manual nurse Task instead.
3. The Chinese follow-up call: "the wound keeps draining" hits the red-flag rule at once (no model needed), the
   patient hears that a nurse calls back within 30 minutes, a high-priority nurse Task tops the queue.
4. The nurse queue: a call-back note and a referral to a physician, each a Communication.
Uses the mock model unless LLM_PROVIDER selects a configured one. Synthetic data only.
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

    from app.agents import approvals
    from app.agents.runtime import runtime
    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store
    from app.llm.gateway import LlmGateway, get_gateway, set_gateway
    from app.llm.providers import MockProvider
    from app.modules.voice_services import callcenter, followup, queue, service

    if get_gateway().mode == "mock":
        set_gateway(LlmGateway(MockProvider(latency_s=0)))
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        clerk, nurse = store.staff["U-CLER-01"], store.staff["U-NURS-05"]
        cases = service.demo_cases(store, clerk)
        print(f"AI mode: {get_gateway().mode}. Clerk: {clerk.name}. Nurse: {nurse.name}.\n")

        caller = cases["callers"]["en_fracture"]
        call = callcenter.start(store, clerk)
        print(f"1. Call centre, call {call.id} (text-simulated; nothing is recorded).")
        print(f"   Agent : {call.transcript[-1].text}")
        for text in ("What are the visiting hours?", "I need to reschedule my fracture clinic appointment.",
                     caller["identity_line"], "The second one", "Yes, please"):
            reply, call, _ = callcenter.turn(store, call.id, text)
            print(f"   Caller: {text}\n   Agent : {reply}")
        request = next(r for r in service.requests(store, clerk) if r["kind"] == "reschedule")
        decision = approvals.decide(clerk, request["task_id"], "approve", "Confirmed with the fracture clinic")
        executed = runtime.execute_approved(decision)
        print(f"   Clerk confirms {request['task_id']} in one click: {executed.status}; booked "
              f"{executed.output.get('appointment_id')}, cancelled {executed.output.get('replaced')}.")
        for text in ("Is it normal that my ankle is swollen?", "No, that's all, thanks"):
            reply, call, ended = callcenter.turn(store, call.id, text)
            print(f"   Caller: {text}\n   Agent : {reply}")
        print(f"   Outcome: {call.outcome}. Actions: {', '.join(a['action'] for a in call.actions)}.")
        print(f"   Summary: {call.summary}")

        swept = [followup.get(i, store) for i in followup.sweep(store)]
        manual = [c for c in swept if c.status == "manual"]
        print(f"\n2. Follow-up sweep (stays discharged 48-72 h ago): {len(swept) - len(manual)} calls scheduled, "
              f"{len(manual)} manual follow-ups for a nurse (a condition is missing), for example:")
        for c in sorted(manual, key=lambda c: c.missing == ["instructions"])[:4]:
            print(f"   - {c.id}: {c.reason}")

        zh = cases["followups"]["zh"]
        fu = followup.get(followup.call_id_for(zh["stay"]), store)
        if fu is None or fu.status != "scheduled":
            print(f"\nThe Chinese follow-up call has already been placed ({fu.status if fu else 'not scheduled'}): "
                  f"press Reset demo (or POST /api/demo/reset) and run this again.")
            return
        intro, fu = followup.start_call(store, fu.id, nurse)
        print(f"\n3. Follow-up call {fu.id} in {fu.language} (discharged {fu.discharged_at:%Y-%m-%d %H:%M}).")
        print(f"   Agent  : {intro}")
        y, m, d = store.fhir.read("Patient", fu.patient_id)["birthDate"].split("-")
        for text in (f"{y}年{int(m)}月{int(d)}日", "还行，就是伤口一直在渗液，纱布都湿透了", "没有了"):
            reply, fu, ended = followup.answer(store, fu.id, text)
            print(f"   Patient: {text}\n   Agent  : {reply}")
        flags = ", ".join(f"{h['code']} ({h['how']}: \"{h['quote']}\")" for h in fu.rule_hits)
        print(f"   Escalated by {fu.escalated_by}: {flags}; priority {fu.priority}; nurse Task {fu.task_id}; "
              f"classifier {fu.classifier.get('status') if fu.classifier else '-'}.")
        print(f"   Written back: Communication {fu.communication_id}; Observations {', '.join(fu.observation_ids)}.")

        top = queue.items(store, nurse)[0]
        print(f"\n4. Nurse queue, first item: [{top['priority']}] {top['id']} MRN {top['mrn']} — "
              f"\"{top['patient_words'][0]}\"")
        queue.act(store, nurse, fu.id, "callback", "Called back: dressing soaked twice a day; asked her to come in.")
        item = queue.act(store, nurse, fu.id, "refer", "Wound review by the physician today.")
        for a in item["nurse_actions"]:
            print(f"   {a['kind']}: Communication {a['communication_id']}"
                  + (f", physician Task {a['task_id']}" if a.get("task_id") else ""))
        print(f"   Review status: {item['review_status']}.")
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
    main()
