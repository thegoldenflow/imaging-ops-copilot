"""WP4 demo (spec 6.3): roles and unit scope, break-glass, consent, signing, the registry,
free-text de-identification and the extended audit log, in a terminal.

    cd apps/api && uv run python scripts/platform_demo.py [--commit]

Runs against the database in DATABASE_URL (the API's demo data, migrated first) inside
one transaction that is rolled back at the end (`--commit` keeps the changes). The web
app shows the same through the Patients, Break-glass review and Audit log pages; see
demo/platform_governance.md for the talk track.
"""

import argparse
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.migrate import upgrade  # noqa: E402
from app.core.models import Role  # noqa: E402
from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store  # noqa: E402
from app.ehr import breakglass, consent  # noqa: E402
from app.ehr.codes import MRN_SYSTEM  # noqa: E402
from app.ehr.events import InProcessEventBus  # noqa: E402
from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway  # noqa: E402
from app.fhir.dt import ref_id  # noqa: E402
from app.llm.freetext_deid import FreeTextDeidentifier, deidentify  # noqa: E402
from app.llm.gateway import LlmGateway, set_gateway  # noqa: E402
from app.llm.providers import MockProvider  # noqa: E402

NOTE = ("Nursing note. Mrs. {family} (MRN {mrn}) seen by Dr. {doctor} on Oct 6, 2026; follow-up 10/20. Daughter "
        "Emily Santos at (416) 555-0123 ext. 234, lives at 45 Sample Ave, Toronto, ON M4C 1A1. Hannah Novak will "
        "drive her home. Discharge to Lakeview Long-Term Care. Pain 3/10, BP 128/76, Foley removed.")


def heading(text: str) -> None:
    print(f"\n== {text} ==")


@contextmanager
def demo_store(commit: bool):
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        yield store
        store.save()
        (outer.commit if commit else outer.rollback)()
    finally:
        set_ambient_store(None)
        conn.close()


def mrn_of(store, pid: str) -> str:
    return next(i["value"] for i in store.fhir.read("Patient", pid)["identifier"] if i["system"] == MRN_SYSTEM)


def inpatient(store, unit: str) -> tuple[str, str]:
    enc = store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=unit, limit=1)[0]
    return enc["id"], ref_id(enc["subject"])


def attempt(fn) -> str:
    try:
        out = fn()
    except FhirAccessDenied as e:
        return "refused" + (" (break-glass)" if type(e).__name__ == "BreakGlassRequired" else "")
    return "ok" if out is None or out is not False else "refused"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    upgrade()
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    with demo_store(args.commit) as store:
        start = len(store.audit.events())
        users = {u.role: u for u in store.staff.values() if u.demo_login}
        users[Role.OPERATIONS_MANAGER], users[Role.ADMIN] = store.staff["U-OPS"], store.staff["U-ADMIN"]
        if Role.PHYSICIAN not in users:
            sys.exit("No hospital staff in this database: reset the demo data first (Reset demo, or python -m app.seed).")

        def gw(role, module="patient_chart"):
            return FhirGateway(Actor.of(users[role]), module)

        heading("Hospital staff from the generated practitioners (one demo login per role)")
        for role in (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST, Role.CLERK, Role.OPERATIONS_MANAGER, Role.ADMIN):
            u = users[role]
            print(f"  {role:<19} {u.id:<10} {u.name:<24} units: {', '.join(u.unit_ids) or '(hospital-wide or none)'}")

        own_enc, own = inpatient(store, "MEDA")
        icu_enc, icu = inpatient(store, "ICU")
        heading(f"Who sees what (FhirGateway decides, not the UI): MEDA patient {own}, ICU patient {icu}")
        checks = {
            "MEDA patient": lambda f: f.get_patient(mrn_of(store, own)),
            "ICU patient": lambda f: f.get_patient(mrn_of(store, icu)),
            "MEDA vitals": lambda f: f.get_observations(own_enc),
            "MEDA med orders": lambda f: f.get_medications(own_enc),
            "ICU bed board": lambda f: f.get_bed_board("ICU"),
        }
        print("  " + " " * 19 + "".join(f"{c:<20}" for c in checks))
        for role in (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST, Role.CLERK, Role.OPERATIONS_MANAGER, Role.ADMIN):
            row = [attempt(lambda fn=fn: (fn(gw(role)), None)[1]) for fn in checks.values()]
            print(f"  {role:<19}" + "".join(f"{c:<20}" for c in row))
        ops_view = gw(Role.OPERATIONS_MANAGER).get_patient(mrn_of(store, icu))
        print(f"  the operations manager's view of a patient: {ops_view.to_fhir()}")

        heading("Break-glass: the physician needs the ICU patient")
        doctor = users[Role.PHYSICIAN]
        print("  read:", attempt(lambda: gw(Role.PHYSICIAN).get_patient(mrn_of(store, icu))))
        try:
            breakglass.request_access(store, user_id=doctor.id, user_name=doctor.name, role="physician",
                                      patient_id=icu, mrn=mrn_of(store, icu), reason="ICU call")
        except breakglass.BreakGlassError as e:
            print("  reason 'ICU call':", e)
        grant = breakglass.request_access(store, user_id=doctor.id, user_name=doctor.name, role="physician",
                                          patient_id=icu, mrn=mrn_of(store, icu),
                                          reason="Rapid response call on the ICU, covering for the attending")
        print(f"  {grant.id}: open until {grant.expires_at:%H:%M}, review due {grant.review_due:%a %H:%M}")
        print("  read again:", attempt(lambda: gw(Role.PHYSICIAN).get_observations(icu_enc)))
        item = next(g for g in breakglass.queue(store) if g["id"] == grant.id)
        print(f"  admin queue: {item['user_name']} opened {item['patient_id']}, {item['accessed_count']} reads "
              f"{item['accessed']}")
        breakglass.review(store, grant.id, decision="justified", note="Confirmed with the ICU charge nurse",
                          reviewer_id="U-ADMIN", reviewer_name=users[Role.ADMIN].name, reviewer_role="admin")
        print("  reviewed: justified (written back to the audit log)")

        heading("Signing service: drafts become final only through it")
        for role, doc in ((Role.NURSE, "doc-demo-discharge"), (Role.PHYSICIAN, "doc-demo-discharge"),
                          (Role.PHARMACIST, "doc-demo-medrec"), (Role.PHYSICIAN, "doc-demo-medrec")):
            try:
                r = gw(role, "signing").sign_document(doc)
                print(f"  {role:<11} signs {doc:<19} -> {r.doc_status}"
                      + (f", waiting for {', '.join(r.missing_roles)}" if r.missing_roles else ""))
            except FhirAccessDenied as e:
                print(f"  {role:<11} signs {doc:<19} -> refused: {e}")

        heading("Safety-tier registry: a module without an entry cannot write")
        try:
            gw(Role.NURSE, "shadow_module").create({"resourceType": "Flag", "status": "active",
                                                    "code": {"text": "Fall risk"},
                                                    "subject": {"reference": f"Patient/{own}"}})
        except FhirAccessDenied as e:
            print("  shadow_module creates a Flag ->", e)

        heading("Consent: missing or declined consent degrades, a change goes on the event bus")
        system = lambda module: FhirGateway(Actor.system(module), module)  # noqa: E731
        states = {}
        for c in store.fhir.search("Consent"):
            states.setdefault(c["category"][0]["coding"][0]["code"], {})[ref_id(c["patient"])] = c["provision"]["type"]
        no_call = next(p["id"] for p in store.fhir.search("Patient") if p["id"] not in states["followup_call"])
        print(f"  {no_call} has no follow-up call consent:",
              consent.plan_followup_call(system("followup_calls"), no_call).mode)
        no_ai = next(p["id"] for p in store.fhir.search("Patient") if states["ai_processing"].get(p["id"]) != "permit")
        print(f"  {no_ai} without AI consent: documentation", consent.documentation_mode(system("discharge_summary"),
                                                                                           no_ai).mode)
        no_sms = next(p["id"] for p in store.fhir.search("Patient") if states["sms"].get(p["id"]) != "permit")
        sms = consent.send_sms(system("patient_messaging"), no_sms, "Your follow-up visit is on Monday at 10:00.")
        print(f"  {no_sms} SMS: sent={sms.sent} ({sms.reason}); desk task {sms.task_id}")
        listener = InProcessEventBus()
        seen = []
        listener.subscribe("consent.revoked", seen.append, consumer="demo.consent")
        with_sms = next(p for p, s in states["sms"].items() if s == "permit")
        clerk = FhirGateway(Actor.of(users[Role.CLERK]), "consent_management")
        consent.change_consent(clerk, with_sms, "sms", False)
        listener.drain()
        print(f"  clerk records SMS withdrawal for {with_sms}: subscriber got {[(e.type, e.patient) for e in seen]}")

        heading("Free-text de-identification (rule layer + second pass through the LLM gateway, mock provider)")
        patient = store.fhir.read("Patient", own)
        family = patient["name"][0].get("family", "Patient")
        deid = FreeTextDeidentifier(reference=date(2026, 10, 8))
        deid.learn_fhir([patient] + [store.fhir.read("Practitioner", "prac-doc-09")])
        doctor_name = store.fhir.read("Practitioner", "prac-doc-09")["name"][0]["text"].removeprefix("Dr. ")
        note = NOTE.format(family=family, mrn=mrn_of(store, own), doctor=doctor_name)
        result = deidentify(note, deid)
        print("  in: ", note)
        print("  out:", result.text)
        print(f"  second pass: {result.second_pass}; caught after the rules: {[m.kind for m in result.misses]}")

        heading("Audit log of this demo (6.3 event types; the hash chain still verifies)")
        events = store.audit.events()[start:]
        print("  " + ", ".join(f"{k} {v}" for k, v in Counter(e.event_type for e in events).most_common()))
        print("  chain intact:", store.audit.verify()[0])
    print("\n(rolled back)" if not args.commit else "\n(committed)")


if __name__ == "__main__":
    main()
