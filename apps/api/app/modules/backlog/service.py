"""System 11 · Reporting Backlog & Turnaround Tracker.

A study is in the backlog from the moment the exam is completed until its
report is signed. Turnaround = signed time − completion time, measured against
a target per priority. Every unread study is assigned to a radiologist; the
board suggests moving work from overloaded or off-shift readers to on-shift
readers credentialed for the modality."""

import random
import statistics
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import Appointment, AppointmentStatus, ImagingStudy, Modality, Role, StaffUser
from app.core.store import Store
from app.modules.reports import dictation
from app.modules.reports.service import report_for_study
from app.modules.scheduling import service as scheduling

# Estimated reading effort per study, used to balance queues.
READ_MINUTES = {Modality.MRI: 15, Modality.CT: 10, Modality.US: 6, Modality.XR: 3}
HISTORY_DAYS = 14  # seeded studies and reports
AGE_BUCKETS = [(4, "< 4 h"), (12, "4–12 h"), (24, "12–24 h"), (48, "24–48 h"), (float("inf"), "> 48 h")]


class TatTargets(BaseModel):
    hours: dict[str, float] = Field(default_factory=lambda: {"P1": 2, "P2": 8, "P3": 24, "P4": 72})
    at_risk_fraction: float = Field(0.75, gt=0, lt=1)  # flag once this share of the target has elapsed


class Assignment(BaseModel):
    study_id: str
    radiologist_id: str
    assigned_at: datetime
    assigned_by: str  # "auto" or a staff name
    history: list[dict] = []


def targets(store: Store) -> TatTargets:
    return store.module("tat_targets", TatTargets)


def assignments(store: Store) -> dict[str, Assignment]:
    return store.module("reading_assignments", dict)


def roster(store: Store) -> dict[str, bool]:
    """Radiologist id -> on shift today."""
    return store.module("reading_roster", dict)


def radiologists(store: Store) -> list[StaffUser]:
    return [u for u in store.staff.values() if u.role == Role.RADIOLOGIST]


def on_shift(store: Store, rad_id: str) -> bool:
    return roster(store).get(rad_id, True)


def modality(store: Store, study: ImagingStudy) -> Modality:
    return store.exams[study.exam_code].modality


def read_minutes(store: Store, study: ImagingStudy) -> int:
    return READ_MINUTES[modality(store, study)]


def is_unread(store: Store, study: ImagingStudy) -> bool:
    if study.source_facility:  # outside priors are not ours to report
        return False
    report = report_for_study(store, study.id)
    return report is None or report.status != "signed"


def unread(store: Store) -> list[ImagingStudy]:
    return [s for s in store.studies.values() if is_unread(store, s)]


def queue_minutes(store: Store, studies: list[ImagingStudy] | None = None) -> dict[str, int]:
    load = {r.id: 0 for r in radiologists(store)}
    for study in studies if studies is not None else unread(store):
        a = assignments(store).get(study.id)
        if a:
            load[a.radiologist_id] = load.get(a.radiologist_id, 0) + read_minutes(store, study)
    return load


def credentialed(store: Store, rad: StaffUser, study: ImagingStudy) -> bool:
    return modality(store, study) in rad.reading_modalities


def pick_reader(store: Store, study: ImagingStudy, load: dict[str, int] | None = None) -> str | None:
    """Least-loaded on-shift radiologist credentialed for the study's modality."""
    load = load if load is not None else queue_minutes(store)
    candidates = [r for r in radiologists(store) if on_shift(store, r.id) and credentialed(store, r, study)]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (load.get(r.id, 0), r.id)).id


def assign(store: Store, study: ImagingStudy, rad_id: str, by: str, reason: str, now: datetime | None = None) -> Assignment:
    now = now or datetime.now()
    current = assignments(store).get(study.id)
    entry = {"ts": now.isoformat(timespec="seconds"), "to": rad_id, "by": by, "reason": reason,
             "from": current.radiologist_id if current else None}
    if current:
        current.radiologist_id, current.assigned_at, current.assigned_by = rad_id, now, by
        current.history.append(entry)
    else:
        current = Assignment(study_id=study.id, radiologist_id=rad_id, assigned_at=now, assigned_by=by, history=[entry])
        assignments(store)[study.id] = current
    store.touch()
    return current


def on_completed(store: Store, appt: Appointment, study: ImagingStudy) -> None:
    rad_id = pick_reader(store, study)
    if rad_id:
        assign(store, study, rad_id, "auto", "Least-loaded credentialed radiologist on shift")


scheduling.COMPLETION_HOOKS.append(on_completed)

# ---------- Turnaround ----------


def target_hours(store: Store, study: ImagingStudy) -> float:
    return targets(store).hours.get(study.priority, 24)


def study_state(store: Store, study: ImagingStudy, now: datetime) -> dict:
    target = target_hours(store, study)
    age = (now - study.performed_at).total_seconds() / 3600
    due = study.performed_at + timedelta(hours=target)
    if age > target:
        state = "overdue"
    elif age >= target * targets(store).at_risk_fraction:
        state = "at_risk"
    else:
        state = "on_track"
    return {"age_h": round(age, 2), "target_h": target, "due_at": due.isoformat(timespec="minutes"),
            "remaining_h": round(target - age, 2), "state": state}


def turnaround(store: Store, now: datetime, days: int = 7) -> dict:
    """Turnaround of reports signed in the last `days` days, by priority and per day."""
    since = now - timedelta(days=days)
    rows = []  # (signed_at, priority, hours, within)
    for study in store.studies.values():
        report = report_for_study(store, study.id)
        if report is None or report.status != "signed" or report.signed_at is None or report.signed_at < since:
            continue
        hours = (report.signed_at - study.performed_at).total_seconds() / 3600
        rows.append((report.signed_at, study.priority, hours, hours <= target_hours(store, study)))

    def summary(items):
        hrs = sorted(h for _, _, h, _ in items)
        if not hrs:
            return {"count": 0, "median_h": None, "p90_h": None, "within_target": None}
        return {"count": len(hrs), "median_h": round(statistics.median(hrs), 1),
                "p90_h": round(hrs[min(len(hrs) - 1, int(len(hrs) * 0.9))], 1),
                "within_target": round(sum(w for *_, w in items) / len(items), 3)}

    by_priority = [{"priority": p, "target_h": h, **summary([r for r in rows if r[1] == p])}
                   for p, h in sorted(targets(store).hours.items())]
    daily = []
    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        daily.append({"date": day.isoformat(), **summary([r for r in rows if r[0].date() == day])})
    return {"window_days": days, "overall": summary(rows), "by_priority": by_priority, "daily": daily}

# ---------- Rebalancing ----------


def suggestions(store: Store, now: datetime, limit: int = 40) -> list[dict]:
    """Greedy rebalancing: move the most urgent studies off readers who are off
    shift or well above the team average, to the least-loaded credentialed
    reader on shift, as long as the move narrows the gap."""
    rads = {r.id: r for r in radiologists(store)}
    working = [r for r in rads.values() if on_shift(store, r.id)]
    if not working:
        return []
    studies = [s for s in unread(store) if s.id in assignments(store)]
    load = queue_minutes(store, studies)
    average = sum(load.values()) / len(working)
    order = {"overdue": 0, "at_risk": 1, "on_track": 2}
    studies.sort(key=lambda s: (on_shift(store, assignments(store)[s.id].radiologist_id),
                                order[study_state(store, s, now)["state"]],
                                s.performed_at + timedelta(hours=target_hours(store, s))))
    out = []
    for study in studies:
        owner = assignments(store)[study.id].radiologist_id
        minutes = read_minutes(store, study)
        off = not on_shift(store, owner)
        if not off and load[owner] <= average * 1.2:
            continue
        cands = [r for r in working if r.id != owner and credentialed(store, r, study)]
        if not cands:
            continue
        to = min(cands, key=lambda r: (load[r.id], r.id))
        if not off and load[to.id] + minutes >= load[owner] - minutes:
            continue
        mod = modality(store, study).value
        why = (f"{rads[owner].name} is off shift" if off else
               f"{rads[owner].name} has {load[owner] / 60:.1f} h queued (team average {average / 60:.1f} h)")
        out.append({"study_id": study.id, "from_id": owner, "from_name": rads[owner].name, "to_id": to.id,
                    "to_name": to.name, "minutes": minutes,
                    "reason": f"{why}; {to.name} reads {mod} and has {load[to.id] / 60:.1f} h queued"})
        load[owner] -= minutes
        load[to.id] += minutes
        if len(out) >= limit:
            break
    return out

# ---------- Seed ----------

# Reading speed relative to the group; Dr. Webb is the bottleneck in the demo.
SPEED = {"U-RAD": 1.0, "U-RAD2": 1.7, "U-RAD3": 0.9, "U-RAD4": 1.0, "U-RAD5": 1.1}
MEDIAN_TAT_H = {"P1": 0.8, "P2": 4.0, "P3": 11.0, "P4": 26.0}


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Studies and signed reports for the last HISTORY_DAYS of completed exams.
    Each study gets a reader and a sampled turnaround; studies whose sampled
    sign-off lies in the future are still unread and form today's backlog."""
    roster(s)["U-RAD5"] = False  # off shift today; her queue should be redistributed
    rads = {r.id: r for r in radiologists(s)}
    since = now - timedelta(days=HISTORY_DAYS)
    completed = sorted((a for a in s.appointments.values()
                        if a.status == AppointmentStatus.COMPLETED and a.start >= since), key=lambda a: a.start)
    for appt in completed:
        performed = min(appt.end, now)
        study = scheduling.study_for(s, appt, performed)
        study.study_uid = f"2.25.{rng.getrandbits(100)}"
        s.studies[study.id] = study
        mod = s.exams[appt.exam_code].modality
        readers = [r for r in rads.values() if mod in r.reading_modalities]
        weights = [2.2 if r.id == "U-RAD2" else 1.0 for r in readers]
        reader = rng.choices(readers, weights=weights)[0]
        hours = MEDIAN_TAT_H[study.priority] * SPEED[reader.id] * min(4.0, rng.lognormvariate(0, 0.65))
        signed_at = performed + timedelta(hours=hours)
        if signed_at.date() == now.date() and not on_shift(s, reader.id):
            # Off-shift readers sign nothing today; a colleague on shift read it.
            reader = rng.choice([r for r in readers if on_shift(s, r.id)] or readers)
        if signed_at <= now:
            findings, impression = dictation.template(appt.exam_code)
            dictation.sign_dictated(s, study, findings=findings, impression=impression, signer_id=reader.id,
                                    signer_name=reader.name, signed_at=signed_at)
        else:
            assignments(s)[study.id] = Assignment(
                study_id=study.id, radiologist_id=reader.id, assigned_at=performed, assigned_by="auto",
                history=[{"ts": performed.isoformat(timespec="seconds"), "to": reader.id, "by": "auto",
                          "reason": "Least-loaded credentialed radiologist on shift", "from": None}])
    # Chest X-rays waiting for an AI draft (system 3) sit in Dr. Raman's queue.
    for study in s.studies.values():
        if study.image_key and study.id not in assignments(s) and not study.source_facility:
            study.site_id = study.site_id or "LKS"
            assignments(s)[study.id] = Assignment(
                study_id=study.id, radiologist_id="U-RAD", assigned_at=study.performed_at, assigned_by="auto",
                history=[{"ts": study.performed_at.isoformat(timespec="seconds"), "to": "U-RAD", "by": "auto",
                          "reason": "Chest X-ray AI drafting worklist", "from": None}])
