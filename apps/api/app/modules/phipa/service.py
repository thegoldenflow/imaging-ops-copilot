"""System 20 · PHIPA Access Monitoring.

Rules run over the hash-chained audit log. Each PHI access is resolved to the
patient it touched; then:

- cross_site: a site-based user opened a visit record of a patient never seen at their sites
- after_hours: access between 22:00 and 06:00
- bulk: one user opened many different patients' records within minutes
- same_family_name: the user's family name matches the patient's and there is no visit
  at the user's sites within three days (a possible relative)
- own_record: the user opened their own patient record
- repeated_denials: several refused requests in a short time

Each alert carries a risk score and the audit sequence numbers as evidence. An
alert's investigation (assignment, notes, outcome) is kept with a full trail,
and every investigation step is itself written to the audit log.

Detection is recomputed from the log (cached by log length), so the log stays
the single source of truth; investigation state is keyed by the alert id."""

import csv
import io
import random
from collections import defaultdict, deque
from datetime import datetime, time, timedelta

from pydantic import BaseModel

from app.core.models import AppointmentStatus, AuditEvent, Patient, Role, StaffUser
from app.core.store import Store

RULES = {
    "cross_site": ("Patient not seen at the user's sites", 50),
    "after_hours": ("Access outside 06:00-22:00", 35),
    "bulk": ("Many patients opened in a short time", 70),
    "same_family_name": ("Same family name, no visit nearby (possible relative)", 55),
    "own_record": ("User opened their own record", 80),
    "repeated_denials": ("Repeated refused requests", 45),
}
OUTCOMES = {
    "justified": "Access justified (work reason documented)",
    "education": "Minor; staff member coached",
    "breach_confirmed": "Privacy breach confirmed",
    "false_positive": "False positive",
    "referred": "Referred to the privacy officer",
}
AFTER_HOURS = (time(6, 0), time(22, 0))
BULK_PATIENTS, BULK_MINUTES = 25, 15
DENIALS, DENIAL_MINUTES = 3, 10
FAMILY_DAYS = 3
# Opening a visit record requires a visit at one of your sites; central intake (requisitions) is exempt.
VISIT_RESOURCES = {"appointment", "patient", "imaging_study", "study_image", "report", "report_draft",
                   "ct_dose_record", "patient_feedback", "mri_screening"}
PHI_ACTIONS = {"read", "update", "create", "export", "override", "delete"}


class Investigation(BaseModel):
    alert_id: str
    status: str = "new"  # new, investigating, closed
    assignee: str | None = None
    outcome: str | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    trail: list[dict] = []


def investigations(store: Store) -> dict[str, Investigation]:
    return store.module("phipa_investigations", dict)


# ---------- Resolving audit events to patients ----------

def resolve_patient(store: Store, resource_type: str, resource_id: str | None) -> str | None:
    if not resource_id:
        return None
    try:
        if resource_type == "patient":
            return resource_id if resource_id in store.patients else None
        if resource_type in ("appointment", "waitlist_offer"):
            return store.appointments[resource_id].patient_id
        if resource_type in ("imaging_study", "study_image", "study_assignment"):
            return store.studies[resource_id].patient_id
        if resource_type in ("requisition", "requisition_extraction", "triage", "protocol"):
            return store.requisitions[resource_id].patient_id
        if resource_type == "waitlist_entry":
            return store.waitlist[resource_id].patient_id
        module_maps = {"report": "reports", "report_draft": "reports", "critical_result": "critical_cases",
                       "ct_dose_record": "dose_records", "patient_feedback": "feedback_responses",
                       "mri_screening": "mri_screenings"}
        if resource_type in module_maps:
            return store.modules.get(module_maps[resource_type], {})[resource_id].patient_id
        if resource_type == "billing_discrepancy":
            return store.appointments[resource_id.split(".", 1)[1]].patient_id
    except (KeyError, AttributeError, IndexError):
        return None
    return None


class _Index:
    """Patient -> sites and visit dates, built once per scan."""

    def __init__(self, store: Store) -> None:
        self.sites: dict[str, set[str]] = defaultdict(set)
        self.visits: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for a in list(store.appointments.values()):  # a cancelled booking still ties the patient to the site
            self.sites[a.patient_id].add(a.site_id)
            self.visits[a.patient_id].append((a.start, a.site_id))

    def visit_near(self, pid: str, when: datetime, sites: list[str], days: int) -> bool:
        return any(abs((start - when).total_seconds()) <= days * 86400 and (not sites or site in sites)
                   for start, site in self.visits.get(pid, []))


def _family(name: str) -> str:
    return name.replace("Dr. ", "").split()[-1].lower()


def detect(store: Store) -> list[dict]:
    events = store.audit.events()
    cache = store.modules.get("phipa_cache")
    if cache and cache[0] == len(events):
        return cache[1]
    idx = _Index(store)
    staff = store.staff
    found: dict[str, dict] = {}

    def add(rule: str, key: str, ev: AuditEvent, user: StaffUser | None, pid: str | None, why: str):
        aid = f"{rule}:{key}"
        alert = found.get(aid)
        if alert is None:
            patient = store.patients.get(pid) if pid else None
            alert = found[aid] = {
                "id": aid, "rule": rule, "rule_label": RULES[rule][0], "user_id": ev.user_id, "user_name": ev.user_name,
                "role": ev.role, "user_sites": user.site_ids if user else [], "patient_id": pid,
                "patient_name": patient.full_name if patient else None, "first_at": ev.ts, "last_at": ev.ts,
                "evidence": [], "patients": set(), "why": why,
            }
        alert["last_at"] = ev.ts
        if pid:
            alert["patients"].add(pid)
        if len(alert["evidence"]) < 60:
            alert["evidence"].append({"seq": ev.seq, "ts": ev.ts.isoformat(timespec="seconds"), "action": ev.action,
                                      "resource_type": ev.resource_type, "resource_id": ev.resource_id,
                                      "outcome": ev.outcome, "patient_id": pid, "source_ip": ev.source_ip})

    recent: dict[str, deque] = defaultdict(deque)  # user -> (ts, pid, event) within the bulk window
    denials: dict[str, deque] = defaultdict(deque)
    for ev in events:
        user = staff.get(ev.user_id)
        if ev.outcome == "denied":
            q = denials[ev.user_id]
            q.append(ev)
            while q and (ev.ts - q[0].ts) > timedelta(minutes=DENIAL_MINUTES):
                q.popleft()
            if len(q) >= DENIALS:
                key = f"{ev.user_id}:{q[0].ts:%Y%m%d%H%M}"
                for d in list(q):
                    if not any(e["seq"] == d.seq for e in found.get(f"repeated_denials:{key}", {}).get("evidence", [])):
                        add("repeated_denials", key, d, user, resolve_patient(store, d.resource_type, d.resource_id),
                            f"{len(q)} refused requests within {DENIAL_MINUTES} minutes")
            continue
        if ev.action not in PHI_ACTIONS:
            continue
        pid = resolve_patient(store, ev.resource_type, ev.resource_id)
        if pid is None:
            continue
        patient: Patient = store.patients[pid]
        day = f"{ev.ts:%Y%m%d}"
        if not (AFTER_HOURS[0] <= ev.ts.time() < AFTER_HOURS[1]):
            add("after_hours", f"{ev.user_id}:{day}", ev, user, pid, f"Access at {ev.ts:%H:%M}")
        if user and user.role != Role.REFERRER:
            if user.site_ids and ev.resource_type in VISIT_RESOURCES and not (idx.sites.get(pid, set()) & set(user.site_ids)):
                add("cross_site", f"{ev.user_id}:{day}", ev, user, pid,
                    f"Patients with no visit at {', '.join(user.site_ids)}")
            if user.name.lower() == patient.full_name.lower():
                add("own_record", f"{ev.user_id}:{pid}", ev, user, pid, "Staff name matches the patient record")
            elif _family(user.name) == patient.family_name.lower() and \
                    not idx.visit_near(pid, ev.ts, user.site_ids, FAMILY_DAYS):
                add("same_family_name", f"{ev.user_id}:{pid}", ev, user, pid,
                    f"Both named {patient.family_name}; no visit within {FAMILY_DAYS} days")
        q = recent[ev.user_id]
        q.append((ev.ts, pid, ev))
        while q and (ev.ts - q[0][0]) > timedelta(minutes=BULK_MINUTES):
            q.popleft()
        distinct = {p for _, p, _ in q}
        if len(distinct) >= BULK_PATIENTS:
            start = q[0][0]
            key = f"{ev.user_id}:{start:%Y%m%d%H}"
            existing = found.get(f"bulk:{key}")
            seen = {e["seq"] for e in existing["evidence"]} if existing else set()
            for _, p, e in q:
                if e.seq not in seen:
                    add("bulk", key, e, user, p, f"{len(distinct)} patients within {BULK_MINUTES} minutes")
            found[f"bulk:{key}"]["why"] = f"{len(found[f'bulk:{key}']['patients'])} patients within {BULK_MINUTES} minutes"
    alerts = []
    late = {(a["user_id"], a["first_at"].date()) for a in found.values() if a["rule"] == "after_hours"}
    for a in found.values():
        score = RULES[a["rule"]][1] + min(20, 4 * (len(a["evidence"]) - 1))
        if a["rule"] != "after_hours" and (a["user_id"], a["first_at"].date()) in late:
            score += 15  # combined with after-hours access on the same day
        a["risk"] = min(100, score)
        a["patients"] = sorted(a["patients"])
        if a["rule"] == "repeated_denials":
            a["why"] = f"{len(a['evidence'])} refused requests within {DENIAL_MINUTES} minutes"
        elif a["rule"] == "cross_site":
            a["why"] = f"{len(a['patients'])} patient(s) with no visit at {', '.join(a['user_sites'])}"
        if len(a["patients"]) > 1:
            a["patient_id"], a["patient_name"] = None, f"{len(a['patients'])} patients"
        a["first_at"], a["last_at"] = a["first_at"].isoformat(timespec="seconds"), a["last_at"].isoformat(timespec="seconds")
        alerts.append(a)
    alerts.sort(key=lambda a: (-a["risk"], a["first_at"]))
    store.modules["phipa_cache"] = (len(events), alerts)
    return alerts


# ---------- Investigation ----------

def investigation(store: Store, alert_id: str) -> Investigation:
    return investigations(store).setdefault(alert_id, Investigation(alert_id=alert_id))


def act(store: Store, alert_id: str, action: str, by: str, now: datetime, *, note: str = "", assignee: str | None = None,
        outcome: str | None = None) -> Investigation:
    inv = investigation(store, alert_id)
    if inv.status == "closed" and action != "reopen":
        raise ValueError("The investigation is closed; reopen it first")
    if action == "assign":
        inv.assignee = assignee or by
        inv.status, inv.opened_at = "investigating", inv.opened_at or now
        text = f"Assigned to {inv.assignee}"
    elif action == "note":
        if len(note.strip()) < 3:
            raise ValueError("Write a note")
        inv.status = "investigating" if inv.status == "new" else inv.status
        inv.opened_at = inv.opened_at or now
        text = "Note added"
    elif action == "close":
        if outcome not in OUTCOMES:
            raise ValueError("Choose an outcome")
        if len(note.strip()) < 5:
            raise ValueError("Record the findings before closing")
        inv.status, inv.outcome, inv.closed_at = "closed", outcome, now
        text = f"Closed: {OUTCOMES[outcome]}"
    elif action == "reopen":
        inv.status, inv.outcome, inv.closed_at = "investigating", None, None
        text = "Reopened"
    else:
        raise ValueError("Unknown action")
    inv.trail.append({"ts": now.isoformat(timespec="seconds"), "by": by, "action": action, "text": text, "note": note.strip()})
    return inv


def report(store: Store, now: datetime, days: int = 30) -> dict:
    since = now - timedelta(days=days)
    alerts = [a for a in detect(store) if datetime.fromisoformat(a["first_at"]) >= since]
    invs = investigations(store)
    by_rule = {r: {"label": label, "alerts": 0, "closed": 0} for r, (label, _) in RULES.items()}
    outcomes = {k: 0 for k in OUTCOMES}
    closed_hours = []
    for a in alerts:
        inv = invs.get(a["id"])
        by_rule[a["rule"]]["alerts"] += 1
        if inv and inv.status == "closed":
            by_rule[a["rule"]]["closed"] += 1
            outcomes[inv.outcome] += 1
            closed_hours.append((inv.closed_at - datetime.fromisoformat(a["first_at"])).total_seconds() / 3600)
    return {
        "period_days": days, "since": since.date().isoformat(), "generated_at": now.isoformat(timespec="minutes"),
        "alerts": len(alerts), "open": sum(1 for a in alerts if (invs.get(a["id"]) or Investigation(alert_id="")).status != "closed"),
        "by_rule": by_rule, "outcomes": {OUTCOMES[k]: v for k, v in outcomes.items()},
        "median_hours_to_close": round(sorted(closed_hours)[len(closed_hours) // 2], 1) if closed_hours else None,
        "audit_chain": dict(zip(("intact", "broken_at_seq"), store.audit.verify())),
        "events_in_log": len(store.audit.events()),
    }


def to_csv(store: Store, alerts: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["alert_id", "rule", "risk", "user", "role", "patient_id", "patients", "first_at", "last_at", "evidence_seqs",
                "status", "assignee", "outcome", "closed_at"])
    invs = investigations(store)
    for a in alerts:
        inv = invs.get(a["id"])
        w.writerow([a["id"], a["rule_label"], a["risk"], a["user_name"], a["role"], a["patient_id"] or "",
                    len(a["patients"]), a["first_at"], a["last_at"], " ".join(str(e["seq"]) for e in a["evidence"]),
                    inv.status if inv else "new", inv.assignee if inv else "", OUTCOMES.get(inv.outcome, "") if inv and inv.outcome else "",
                    inv.closed_at.isoformat(timespec="minutes") if inv and inv.closed_at else ""])
    return buf.getvalue()


# ---------- Seed: 30 days of access history with planted anomalies ----------

EXTRA_STAFF = [
    StaffUser(id="U-FD2", name="Laura Gagnon", role=Role.FRONT_DESK, site_ids=["NGT"], demo_login=False),
    StaffUser(id="U-FD3", name="Dana Kim", role=Role.FRONT_DESK, site_ids=["WBK"], demo_login=False),
    StaffUser(id="U-TECH2", name="Chris Patel", role=Role.TECHNOLOGIST, site_ids=["EVW"], demo_login=False),
]


def seed(s: Store, rng: random.Random, now: datetime) -> list[tuple[str, str]]:
    for u in EXTRA_STAFF:
        s.staff[u.id] = u.model_copy()
    # The technologist is also a patient of the group (for the own-record rule).
    first = next(a for a in sorted(s.appointments.values(), key=lambda a: a.start)
                 if a.site_id == "LKS" and a.status == AppointmentStatus.COMPLETED)
    s.patients["PT-STAFF1"] = Patient(
        id="PT-STAFF1", given_name="Sam", family_name="Rivera", dob=s.patients[first.patient_id].dob.replace(year=1987),
        sex="M", phone="+1-416-555-0155", email="sam.rivera@example.com", address="12 Demo Cres, Demo City, ON",
        health_card="5550123987", health_card_version="SR", preferred_language="en")
    first.patient_id = "PT-STAFF1"

    by_site_day: dict[tuple[str, str], list] = defaultdict(list)
    for a in s.appointments.values():
        if now - timedelta(days=31) <= a.start < now:
            by_site_day[(a.site_id, a.start.date().isoformat())].append(a)
    idx = _Index(s)
    rows: list[tuple[datetime, StaffUser, str, str, str, str]] = []  # ts, user, action, type, id, outcome
    site_users = [u for u in s.staff.values() if u.site_ids and u.role in (Role.FRONT_DESK, Role.TECHNOLOGIST)]

    def at(day: datetime, hour: float) -> datetime:
        return (day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=hour)).replace(microsecond=0)

    def safe(user: StaffUser, pid: str, ts: datetime) -> bool:
        p = s.patients[pid]
        return p.full_name.lower() != user.name.lower() and (
            p.family_name.lower() != _family(user.name) or idx.visit_near(pid, ts, user.site_ids, FAMILY_DAYS))

    for d in range(30, 0, -1):
        day = now - timedelta(days=d)
        for user in site_users:
            appts = by_site_day.get((user.site_ids[0], day.date().isoformat()), [])
            for a in rng.sample(appts, min(len(appts), rng.randint(8, 16))):
                ts = at(day, rng.uniform(7.5, 19))
                if safe(user, a.patient_id, ts):
                    rows.append((ts, user, "read", "appointment", a.id, "allowed"))
        rad = s.staff["U-RAD"]
        studies = [st for st in s.studies.values() if st.performed_at.date() == day.date()]
        for st in rng.sample(studies, min(len(studies), rng.randint(5, 12))):
            rows.append((at(day, rng.uniform(8, 18)), rad, "read", "imaging_study", st.id, "allowed"))

    planted: list[tuple[str, str]] = []
    # 1. Cross-site: Alex Morgan (Lakeshore) opens three patients never seen at Lakeshore.
    alex = s.staff["U-FD"]
    elsewhere = [pid for pid, sites in idx.sites.items() if "LKS" not in sites and pid.startswith("PT-0")
                 and s.patients[pid].family_name != "Morgan"]
    t = at(now - timedelta(days=2), 14.2)
    for i, pid in enumerate(rng.sample(sorted(elsewhere), 3)):
        rows.append((t + timedelta(minutes=3 * i), alex, "read", "patient", pid, "allowed"))
    planted.append(("cross_site", "U-FD"))
    # 2. After hours: Chris Patel (Eastview) reads two Eastview patients at 02:40.
    chris = s.staff["U-TECH2"]
    evw = [a for a in s.appointments.values() if a.site_id == "EVW" and a.start < now - timedelta(days=6)]
    t = at(now - timedelta(days=5), 2.67)
    for i, a in enumerate(rng.sample(sorted(evw, key=lambda a: a.id), 2)):
        rows.append((t + timedelta(minutes=4 * i), chris, "read", "appointment", a.id, "allowed"))
    planted.append(("after_hours", "U-TECH2"))
    # 3. Bulk: Dana Kim (Westbrook) opens 40 Westbrook patients' records in 12 minutes.
    dana = s.staff["U-FD3"]
    wbk = sorted({a.patient_id for a in s.appointments.values() if a.site_id == "WBK" and a.start < now})
    wbk = [p for p in wbk if safe(dana, p, now)]
    t = at(now - timedelta(days=8), 15.0)
    for i, pid in enumerate(rng.sample(wbk, 40)):
        rows.append((t + timedelta(seconds=18 * i), dana, "read", "patient", pid, "allowed"))
    planted.append(("bulk", "U-FD3"))
    # 4. Same family name: Laura Gagnon opens a Gagnon seen at Northgate, but not recently.
    laura = s.staff["U-FD2"]
    t = at(now - timedelta(days=10), 11.5)
    relative = next(pid for pid in sorted(s.patients) if s.patients[pid].family_name == "Gagnon"
                    and "NGT" in idx.sites.get(pid, set()) and not idx.visit_near(pid, t, ["NGT"], FAMILY_DAYS))
    rows.append((t, laura, "read", "patient", relative, "allowed"))
    rows.append((t + timedelta(minutes=2), laura, "read", "patient", relative, "allowed"))
    planted.append(("same_family_name", "U-FD2"))
    # 5. Own record: Sam Rivera opens his own chart.
    rows.append((at(now - timedelta(days=3), 12.4), s.staff["U-TECH"], "read", "patient", "PT-STAFF1", "allowed"))
    planted.append(("own_record", "U-TECH"))
    # 6. Repeated denials: Dana Kim tries four Lakeshore appointments in five minutes.
    lks = sorted((a for a in s.appointments.values() if a.site_id == "LKS" and a.start < now), key=lambda a: a.id)
    t = at(now - timedelta(days=1), 16.1)
    for i, a in enumerate(rng.sample(lks, 4)):
        rows.append((t + timedelta(seconds=70 * i), dana, "get", "appointment", a.id, "denied"))
    planted.append(("repeated_denials", "U-FD3"))

    rows.sort(key=lambda r: r[0])
    for ts, user, action, rtype, rid, outcome in rows:
        s.audit.record(user_id=user.id, user_name=user.name, role=user.role, action=action, resource_type=rtype,
                       resource_id=rid, outcome=outcome, source_ip=f"10.20.{list(s.sites).index(user.site_ids[0]) if user.site_ids else 9}.{rng.randint(10, 60)}",
                       reason="operations", ts=ts)
    s.modules["phipa_planted"] = planted
    # The oldest anomaly has already been investigated and closed.
    alerts = detect(s)
    bulk = next(a for a in alerts if a["rule"] == "bulk")
    closed_at = datetime.fromisoformat(bulk["first_at"]) + timedelta(days=1)
    act(s, bulk["id"], "assign", "Casey Brooks", closed_at - timedelta(hours=20), assignee="Casey Brooks")
    act(s, bulk["id"], "note", "Casey Brooks", closed_at - timedelta(hours=4),
        note="Interviewed Dana Kim: was asked to update phone numbers for a recall list; no written request on file.")
    act(s, bulk["id"], "close", "Casey Brooks", closed_at, outcome="education",
        note="Coached on minimum necessary access; recall lists now run as a report by the site lead.")
    return planted
