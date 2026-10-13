"""Documentation (spec 7.3) in the terminal: discharge summary, the validator's block, bilingual instructions, SBAR.

    cd apps/api && uv run python scripts/documentation_demo.py                       # the whole story, rolled back
    cd apps/api && uv run python scripts/documentation_demo.py --prepare --commit    # demo setup, kept

--prepare signs the discharge medication reconciliations (WP6) of the planted documentation cases as the demo
pharmacist and physician, so the discharge summaries have their signed discharge medication table: what the
pharmacist would do in Medication safety before the physician writes the summary (demo/med_safety.md). With
--commit it is kept in the database of DATABASE_URL (run it after a demo reset, before demo/documentation.md).

The story (without --prepare):
1. The Chinese-speaking warfarin patient's summary before the discharge medication table is signed: refused,
   "insufficient information, missing ..." (no model call).
2. After the reconciliation: the AI draft (one segment call per chunk of resources, then the synthesis), every item
   citing its sources; the validator finds nothing untraceable; the physician signs.
3. The stay whose note tempts the model: the mock takes the discussed medicine for a prescription, the validator
   marks it red and the signing service refuses the draft (also from the chart's Sign button); the physician
   removes it and signs.
4. The bilingual patient instructions from the signed summary only: one structure, three renderings (English,
   Simplified, Traditional Chinese), the readability checks; the nurse signs; delivery mocks.
5. The SBAR handoff of the shift; the nurse confirms it (final).
Uses the mock model unless LLM_PROVIDER selects a configured one.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)


def cases(store) -> dict[str, str]:
    from app.fhir.dt import ref_id

    out = {"warfarin_zh": ref_id(store.fhir.read("Procedure", "proc-doc-ablation")["encounter"]),
           "hallucination": ref_id(store.fhir.read("Condition", "cond-doc-htn-note")["encounter"])}
    pending = [s for s in store.fhir.search("ServiceRequest", status="active") if s["id"].startswith("ord-doc-pending-")]
    if pending:
        out["discharged"] = ref_id(pending[0]["encounter"])
    return out


def sign_discharge_medrec(store, encounter_id: str, pharmacist, physician) -> str:
    from app.modules.med_safety import service as S
    from app.modules.med_safety.reconcile import RecItem

    session = S.reconcile(store, encounter_id, "discharge")
    if session.status == "final":
        return session.id
    session = S.settle(store, session.id)
    if session.status == "open":
        for item in [RecItem.model_validate(i) for i in session.items]:
            if (item.discrepancy and item.action is None) or item.indication == "pending":
                session = S.handle_item(store, pharmacist, session.id, item.id, "accept",
                                        "Discussed with the attending (demo preparation)")
        session = S.sign(store, pharmacist, session.id)
    if session.status == "pharmacist_signed":
        session = S.sign(store, physician, session.id)
    return session.id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--prepare", action="store_true", help="only sign the cases' discharge reconciliations")
    args = parser.parse_args()

    from app.core.models import Role
    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store
    from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway
    from app.llm.gateway import LlmGateway, get_gateway, set_gateway
    from app.llm.providers import MockProvider
    from app.modules.documentation import service as docs

    if get_gateway().mode == "mock":
        set_gateway(LlmGateway(MockProvider(latency_s=0)))
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        staff = {r: next(u for u in store.staff.values() if u.role == r and u.demo_login)
                 for r in (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST)}
        physician, nurse, pharmacist = staff[Role.PHYSICIAN], staff[Role.NURSE], staff[Role.PHARMACIST]
        found = cases(store)
        print(f"AI mode: {get_gateway().mode}. Physician {physician.name}, nurse {nurse.name}.")
        print("Planted cases: " + ", ".join(f"{k} {v}" for k, v in found.items()) + "\n")
        if args.prepare:
            for name, stay in found.items():
                session = sign_discharge_medrec(store, stay, pharmacist, physician)
                print(f"   {name}: discharge reconciliation {session} signed by {pharmacist.name}, co-signed by "
                      f"{physician.name}")
        else:
            stay = found["warfarin_zh"]
            out = docs.draft_summary(store, physician, stay)
            print(f"1. {stay}: draft before the discharge medication table is signed -> {out['status']}:")
            for m in out["missing"]:
                print(f"   missing {m}")
            sign_discharge_medrec(store, stay, pharmacist, physician)
            started = time.monotonic()
            view = docs.draft_summary(store, physician, stay)
            took = time.monotonic() - started
            content = view["content"]
            print(f"\n2. AI draft {view['id']} in {took:.2f} s ({view['model_calls']} model calls: "
                  f"{len(view['inputs']['chunks'])} chunks + synthesis; run {view['run_id']}):")
            print(f"   Reason: {content['reason_for_admission']['text']} {content['reason_for_admission']['source_refs']}")
            for seg in content["hospital_course"][:3]:
                print(f"   {seg['date']}: {seg['text'][:110]} ({len(seg['source_refs'])} sources)")
            print("   Diagnoses: " + "; ".join(f"{d['name']} {d['source_refs']}" for d in content["discharge_diagnoses"]))
            for cat in ("new", "changed", "stopped", "continued"):
                rows = content["medications"][cat]
                if rows:
                    print(f"   {cat}: " + "; ".join(f"{m['name']} {m['dose']}" for m in rows))
            print(f"   Validator: {len([i for i in view['validation']['issues'] if i['severity'] == 'red'])} red, "
                  f"{view['validation']['traceable']} of {view['validation']['checked']} items traceable.")
            signed = docs.sign(store, physician, view["id"])
            print(f"   Signed by {signed['signatures'][0]['name']}: {signed['doc_status']}.")

            bad = found["hallucination"]
            sign_discharge_medrec(store, bad, pharmacist, physician)
            view = docs.draft_summary(store, physician, bad)
            red = [i for i in view["validation"]["issues"] if i["severity"] == "red"]
            print(f"\n3. {bad}: AI draft {view['id']}; the validator marks {len(red)} issue(s) red:")
            for i in red:
                print(f"   - {i['path']}: {i['message']}")
            try:
                FhirGateway(Actor.of(physician), "discharge_summary").sign_document(view["id"])
            except FhirAccessDenied as e:
                print(f"   The chart's Sign button (signing service): refused - {e}")
            paths = sorted({i["path"] for i in red}, reverse=True)
            content = view["content"]
            for path in paths:
                section, _, index = path.rstrip("]").rpartition("[")
                group = content
                for part in section.split("."):
                    group = group[part]
                removed = group.pop(int(index))
                print(f"   The physician removes {path} ({removed['name']}).")
            edited = docs.save(store, physician, view["id"], content)
            print(f"   Re-checked: {len([i for i in edited['validation']['issues'] if i['severity'] == 'red'])} red; "
                  f"signed: {docs.sign(store, physician, view['id'])['doc_status']}. The raw AI draft's issues stay "
                  f"on record ({len(edited['raw_validation']['issues'])}).")

            inst = docs.draft_instructions(store, nurse, stay)
            r = inst["renderings"]
            print(f"\n4. Patient instructions {inst['id']} from the signed summary (default language "
                  f"{inst['default_language']}); the same {len(r['en']['blocks']) - 1} blocks in each language:")
            for lang in ("en", "zh-Hans", "zh-Hant"):
                med = next(b for b in r[lang]["blocks"] if b["key"] == "medicines")["rows"][0]["cells"]
                print(f"   [{lang}] {' | '.join(med)[:150]}")
            print(f"   English Flesch-Kincaid grade {r['en']['readability']['grade']}; Chinese: no abbreviations "
                  f"{r['zh-Hans']['readability']['no_abbreviations']}, terms explained "
                  f"{r['zh-Hans']['readability']['terms_explained']} "
                  f"({r['zh-Hans']['readability']['from_knowledge_graph']} from the knowledge graph).")
            final = docs.sign(store, nurse, inst["id"])
            print(f"   Signed by {final['signatures'][0]['name']}: {final['doc_status']}.")
            for channel in ("print", "sms", "portal"):
                d = docs.deliver(store, nurse, inst["id"], channel)
                print(f"   {channel}: sent {d['sent']}" + (f" ({d.get('reason')})" if d.get("reason") else "")
                      + (f" {d['url']}" if d.get("url") else ""))

            hand = docs.draft_handoff(store, nurse, stay)
            print(f"\n5. Handoff {hand['id']}:")
            print(f"   S: {hand['content']['situation']['text'][:140]}")
            for key in ("background", "assessment", "recommendation"):
                for s in hand["content"][key][:2]:
                    print(f"   {key[0].upper()}: {s['text'][:140]}")
            print(f"   Confirmed: {docs.sign(store, nurse, hand['id'])['doc_status']}.")
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
