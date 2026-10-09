"""The Control Tower's rule engine (spec 7.1): exceptions from the boards, with facts, evidence and an action menu.

| Rule | Fires when | Severity |
| --- | --- | --- |
| `unit_occupancy` (per inpatient unit) | occupied beds >= 95% | high: net gap >= 2, or no free bed while an ED patient waits for the unit; med: net gap >= 1 or no free bed; low: otherwise (expected discharges cover the demand) |
| `ed_boarding` (the ED) | >= 3 admitted patients waiting for a bed for more than 2 h | high: >= 5 such boarders, or one waiting >= 4 h; med: otherwise |
| `or_overrun` (per OR case not finished) | predicted duration >= booked duration + 30 min | high: >= 60 min over, or the room's list predicted to end >= 30 min after the block (16:00); med: otherwise |
| `preop_gap` (per booked OR case) | a pre-op check (consent, fasting, labs, blood group) still open with < 4 h to the booked start | high: < 2 h to go, or the consent is open; med: two or more items open; low: one item open |

The engine is the source of truth for everything the narrator may say: each
exception carries `facts` (every number the narrative may use), `evidence_refs`
(real FHIR references from the boards) and a `menu` of actions with their owner
role. The narrator (agent.py) only chooses from the menu and words it; a person
approves; nothing here executes anything. Exception keys are stable while the
condition lasts (`unit_occupancy:MEDA`, `or_overrun:appt-000123`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.ehr.codes import PREOP_CHECKS
from app.ehr.reference import OR_DAY, OVERFLOW, UNIT_BY_ID
from app.modules.control_tower.snapshot import DISCHARGE_READY, Snapshot

Severity = Literal["low", "med", "high"]
OwnerRole = Literal["physician", "nurse", "operations_manager"]
SEVERITY_RANK = {"low": 0, "med": 1, "high": 2}
PREOP_LABEL = {"preop-consent": "surgical consent", "preop-npo": "fasting confirmation", "preop-labs": "pre-op labs",
               "preop-blood-type": "blood group and screen"}
assert set(PREOP_LABEL) == set(PREOP_CHECKS)


@dataclass(frozen=True)
class RuleConfig:
    occupancy: float = 0.95
    boarders: int = 3
    boarding_minutes: int = 120
    boarding_high_count: int = 5
    boarding_high_minutes: int = 240
    or_overrun_minutes: int = 30
    or_high_minutes: int = 60
    preop_hours: float = 4.0
    preop_high_hours: float = 2.0


CONFIG = RuleConfig()


class MenuItem(BaseModel):
    action_id: str
    label: str  # the action, worded by the engine (with the engine's numbers)
    owner_role: OwnerRole
    why: str  # the engine's reason (the template rationale when the model is not used)
    effect: str  # the engine's expected effect
    encounter_id: str | None = None  # a patient-level action: its encounter


class Detected(BaseModel):
    key: str
    rule: str
    severity: Severity
    title: str
    summary: str  # one or two sentences from the engine (the template narrative)
    unit_id: str  # the unit (Location) it belongs to: a ward, ED or OR
    subject_ref: str  # Location/MEDA, Appointment/appt-000123, ...
    facts: dict
    evidence_refs: list[str] = Field(default_factory=list)
    menu: list[MenuItem] = Field(default_factory=list)


def _hhmm(t: datetime | None) -> str | None:
    return t.strftime("%H:%M") if t else None


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


# ---------- unit occupancy ----------


def unit_occupancy(snap: Snapshot, cfg: RuleConfig = CONFIG) -> list[Detected]:
    out = []
    units = {u.id: u for u in snap.units}
    for u in snap.units:
        if u.beds == 0 or u.occupancy < cfg.occupancy:
            continue
        cells = snap.beds.get(u.id, [])
        ready = sorted((c for c in cells if c.discharge and c.discharge.value >= DISCHARGE_READY),
                       key=lambda c: -c.discharge.value)
        boarders = [e for e in snap.ed if e.bed_requested_at is not None and e.target_unit == u.id]
        pct = round(100 * u.occupancy)
        if u.net_gap >= 2 or (u.free == 0 and u.ed_boarders >= 1):
            severity: Severity = "high"
        elif u.net_gap >= 1 or u.free == 0:
            severity = "med"
        else:
            severity = "low"
        overflow = {o: units[o].free for o in OVERFLOW.get(u.id, []) if o in units}
        facts = {
            "unit": u.id, "unit_name": u.name, "beds": u.beds, "occupied": u.occupied, "occupancy_pct": pct,
            "free_beds": u.free, "cleaning_beds": u.cleaning, "alc_patients": u.alc, "ed_boarders": u.ed_boarders,
            "expected_admissions_24h": u.expected_admissions, "expected_discharges_24h": u.expected_discharges,
            "net_gap": u.net_gap, "elective_admissions_24h": u.electives_24h,
            "discharge_ready": len(ready), "discharge_ready_threshold": DISCHARGE_READY, "window_hours": 24,
            "discharge_ready_patients": [{"bed": c.id, "encounter": c.encounter_id,
                                          "probability": round(c.discharge.value, 2),
                                          "explanation": c.discharge.sentence} for c in ready[:5]],
            "overflow_free_beds": overflow, "threshold_pct": round(100 * cfg.occupancy),
        }
        gap = f"short of {_plural(u.net_gap, 'bed')}" if u.net_gap > 0 else "expected discharges cover the demand"
        summary = (f"{u.name} is at {pct}% ({u.occupied} of {u.beds} beds) with {_plural(u.free, 'free bed')}. "
                   f"{_plural(u.expected_admissions, 'admission')} expected and "
                   f"{_plural(u.expected_discharges, 'discharge')} expected in 24 h: {gap}.")
        menu: list[MenuItem] = []
        if ready:
            beds = ", ".join(c.id for c in ready[:5])
            menu.append(MenuItem(
                action_id="early_discharge_rounds", owner_role="physician",
                label=f"Early discharge rounds for the {_plural(len(ready[:5]), 'patient')} on {u.name} likely to "
                      f"leave within 24 h ({beds})",
                why=f"{_plural(len(ready), 'patient')} with a discharge probability of at least {DISCHARGE_READY}",
                effect=f"up to {_plural(len(ready[:5]), 'bed')} free earlier today"))
        if u.cleaning:
            menu.append(MenuItem(
                action_id="expedite_cleaning", owner_role="operations_manager",
                label=f"Expedite housekeeping for the {_plural(u.cleaning, 'bed')} waiting to be cleaned on {u.name}",
                why=f"{_plural(u.cleaning, 'bed')} out of service until cleaned",
                effect=f"{_plural(u.cleaning, 'bed')} back in service"))
        best = max(overflow.items(), key=lambda kv: kv[1], default=None)
        if best and best[1] > 0:
            name = UNIT_BY_ID[best[0]].name
            menu.append(MenuItem(
                action_id="open_overflow", owner_role="operations_manager",
                label=f"Use the {_plural(best[1], 'free bed')} on {name} as overflow for {u.name}",
                why=f"{name} is {u.name}'s overflow unit and has {_plural(best[1], 'free bed')}",
                effect=f"room for up to {_plural(best[1], 'patient')} from {u.name}"))
        if u.electives_24h:
            menu.append(MenuItem(
                action_id="review_electives", owner_role="operations_manager",
                label=f"Ask the OR coordinator to review the {_plural(u.electives_24h, 'elective admission')} to "
                      f"{u.name} in the next 24 h",
                why=f"{_plural(u.electives_24h, 'elective patient')} will need a {u.name} bed after surgery",
                effect="no elective case cancelled at the last minute for want of a bed"))
        if u.alc:
            menu.append(MenuItem(
                action_id="alc_escalation", owner_role="nurse",
                label=f"Escalate placement for the {_plural(u.alc, 'ALC patient')} on {u.name}",
                why=f"{_plural(u.alc, 'patient')} waiting for a placement elsewhere occupy acute beds",
                effect="ALC patients placed sooner"))
        if u.kind == "icu":
            menu.append(MenuItem(
                action_id="step_down_review", owner_role="physician",
                label="Review the ICU patients who could step down to a ward bed today",
                why=f"ICU is at {pct}% with no overflow unit", effect="ICU beds kept for the sickest patients"))
        menu.append(MenuItem(
            action_id="confirm_discharges", owner_role="nurse",
            label=f"Ask the {u.name} charge nurse to confirm today's discharges and their times",
            why=f"{_plural(u.expected_discharges, 'discharge')} expected in 24 h",
            effect="bed availability known earlier"))
        evidence = [f"Location/{u.id}"] + [f"Encounter/{c.encounter_id}" for c in ready[:5] if c.encounter_id]
        evidence += [f"Encounter/{e.encounter_id}" for e in boarders[:5]]
        evidence += [f"ServiceRequest/{e.bed_request_id}" for e in boarders[:5] if e.bed_request_id]
        out.append(Detected(
            key=f"unit_occupancy:{u.id}", rule="unit_occupancy", severity=severity,
            title=f"{u.name} at {pct}% occupancy", summary=summary, unit_id=u.id, subject_ref=f"Location/{u.id}",
            facts=facts, evidence_refs=evidence, menu=menu))
    return out


# ---------- ED boarders ----------


def ed_boarding(snap: Snapshot, cfg: RuleConfig = CONFIG) -> list[Detected]:
    boarders = sorted((e for e in snap.ed if e.bed_requested_at is not None), key=lambda e: -(e.boarding_minutes or 0))
    long = [e for e in boarders if (e.boarding_minutes or 0) > cfg.boarding_minutes]
    if len(long) < cfg.boarders:
        return []
    longest = long[0].boarding_minutes or 0
    severity: Severity = "high" if len(long) >= cfg.boarding_high_count or longest >= cfg.boarding_high_minutes \
        else "med"
    units = {u.id: u for u in snap.units}
    targets = sorted({e.target_unit for e in long if e.target_unit})
    free = {t: units[t].free for t in targets if t in units}
    overflow = {o: units[o].free for t in targets for o in OVERFLOW.get(t, []) if o in units and o not in targets}
    ready = {t: units[t].discharge_ready for t in targets if t in units}
    facts = {"boarders": len(boarders), "boarders_over_2h": len(long), "longest_minutes": longest,
             "threshold_minutes": cfg.boarding_minutes, "threshold_count": cfg.boarders,
             "boarding": [{"encounter": e.encounter_id, "location": e.location, "target_unit": e.target_unit,
                           "minutes": e.boarding_minutes} for e in long[:8]],
             "free_beds_target_units": free, "free_beds_overflow_units": overflow,
             "discharge_ready_target_units": ready, "ed_census": snap.kpis.ed_census}
    summary = (f"{_plural(len(long), 'admitted patient')} {'have' if len(long) != 1 else 'has'} waited in the ED for a "
               f"bed for more than 2 hours (longest {longest} minutes), for {', '.join(targets) or 'unassigned units'}.")
    menu: list[MenuItem] = []
    free_total = sum(free.values()) + sum(overflow.values())
    if free_total:
        where = ", ".join(f"{UNIT_BY_ID[u].name} {n}" for u, n in {**free, **overflow}.items() if n)
        menu.append(MenuItem(
            action_id="assign_free_beds", owner_role="operations_manager",
            label=f"Assign the longest-waiting boarders to the {_plural(free_total, 'free bed')} ({where})",
            why=f"{_plural(free_total, 'bed')} free on the target or overflow units",
            effect=f"up to {_plural(min(free_total, len(long)), 'boarder')} out of the ED"))
    menu.append(MenuItem(
        action_id="ed_surge_plan", owner_role="operations_manager",
        label=f"Activate the ED surge plan: {_plural(len(long), 'patient')} boarding for more than 2 hours",
        why=f"the longest has waited {longest} minutes", effect="beds and staff found across units"))
    if any(ready.values()):
        n = sum(ready.values())
        menu.append(MenuItem(
            action_id="discharge_rounds_targets", owner_role="physician",
            label=f"Early discharge rounds on {', '.join(UNIT_BY_ID[t].name for t, k in ready.items() if k)} "
                  f"({_plural(n, 'discharge-ready patient')})",
            why=f"{_plural(n, 'patient')} likely to leave within 24 h on the units the boarders need",
            effect="beds free earlier for the boarders"))
    menu.append(MenuItem(
        action_id="boarder_care", owner_role="nurse",
        label="Assign an inpatient nurse to the boarders' care while they wait in the ED",
        why="admitted patients in the ED still need ward-level care", effect="safer care while boarding"))
    evidence = ["Location/ED"] + [f"Encounter/{e.encounter_id}" for e in long[:8]]
    evidence += [f"ServiceRequest/{e.bed_request_id}" for e in long[:8] if e.bed_request_id]
    return [Detected(key="ed_boarding:ED", rule="ed_boarding", severity=severity,
                     title=f"{_plural(len(long), 'ED boarder')} waiting over 2 h", summary=summary, unit_id="ED",
                     subject_ref="Location/ED", facts=facts, evidence_refs=evidence, menu=menu)]


# ---------- OR overrun ----------


def or_overrun(snap: Snapshot, cfg: RuleConfig = CONFIG) -> list[Detected]:
    out = []
    block_end_hour = OR_DAY[1]
    for case in snap.or_cases:
        if case.status in ("fulfilled", "cancelled") or case.overrun_minutes is None:
            continue
        if case.overrun_minutes < cfg.or_overrun_minutes:
            continue
        room = next((r for r in snap.or_rooms if r.id == case.room), None)
        # the room's elective list against its block (emergency cases after hours are not part of the block)
        room_end = max((c.predicted_end for c in snap.or_cases if c.room == case.room and c.predicted_end
                        and c.status != "cancelled" and c.urgency != "emergent"), default=case.predicted_end)
        block_end = case.booked_start.replace(hour=block_end_hour, minute=0, second=0, microsecond=0)
        past_block = max(0, int((room_end - block_end).total_seconds() // 60)) \
            if room_end and room and room.block and case.booked_start < block_end else 0
        severity: Severity = "high" if case.overrun_minutes >= cfg.or_high_minutes or past_block >= 30 else "med"
        following = sorted((c for c in snap.or_cases if c.room == case.room and c.status == "booked"
                            and c.booked_start > case.booked_start), key=lambda c: c.booked_start)
        nxt = following[0] if following else None
        facts = {"case": case.appointment_id, "room": case.room, "procedure": case.procedure,
                 "surgeon": case.surgeon, "status": "in the OR" if case.status == "arrived" else "booked",
                 "booked_start": _hhmm(case.booked_start), "booked_minutes": case.booked_minutes,
                 "predicted_minutes": case.predicted_minutes, "overrun_minutes": case.overrun_minutes,
                 "predicted_end": _hhmm(case.predicted_end), "room_predicted_end": _hhmm(room_end),
                 "block_end": f"{block_end_hour:02d}:00", "minutes_past_block": past_block,
                 "prediction": case.predicted.sentence if case.predicted else None,
                 "next_case": {"case": nxt.appointment_id, "procedure": nxt.procedure,
                               "booked_start": _hhmm(nxt.booked_start)} if nxt else None,
                 "post_op_unit": case.post_op_unit, "threshold_minutes": cfg.or_overrun_minutes}
        summary = (f"{case.procedure} in {case.room} (booked {case.booked_start:%H:%M}, {case.booked_minutes} min) is "
                   f"predicted to take {case.predicted_minutes} min, {case.overrun_minutes} min over its booking; "
                   f"the room's list is predicted to end at {_hhmm(room_end)}.")
        menu = [MenuItem(action_id="resequence_room", owner_role="operations_manager",
                         label=f"Ask the OR coordinator to re-sequence {case.room}'s list after this case",
                         why=f"the case is predicted to run {case.overrun_minutes} min over",
                         effect="later cases start on a realistic time")]
        if nxt:
            other = [r for r in snap.or_rooms if r.id != case.room and r.predicted_end and nxt.predicted_start
                     and r.predicted_end + timedelta(minutes=30) <= nxt.predicted_start]
            if other:
                best = min(other, key=lambda r: r.predicted_end)
                menu.append(MenuItem(
                    action_id="move_next_case", owner_role="operations_manager",
                    label=f"Move the next case ({nxt.procedure}, booked {nxt.booked_start:%H:%M}) to {best.id}, "
                          f"free from {_hhmm(best.predicted_end)}",
                    why=f"{best.id} is predicted to be free from {_hhmm(best.predicted_end)}",
                    effect=f"the {nxt.booked_start:%H:%M} case keeps its time"))
        if case.post_op_unit:
            menu.append(MenuItem(
                action_id="notify_ward", owner_role="nurse",
                label=f"Tell {UNIT_BY_ID[case.post_op_unit].name} the patient will come about "
                      f"{case.overrun_minutes} min later than booked",
                why=f"the case is predicted to end at {_hhmm(case.predicted_end)}",
                effect="the ward plans the bed and the handover on time"))
        menu.append(MenuItem(
            action_id="confirm_duration", owner_role="physician",
            label=f"Confirm the expected duration with {case.surgeon or 'the surgeon'}",
            why=f"the model predicts {case.predicted_minutes} min against {case.booked_minutes} booked",
            effect="the list is planned on the surgeon's estimate"))
        evidence = [f"Appointment/{case.appointment_id}", f"Location/{case.room}"]
        if case.encounter_id:
            evidence.append(f"Encounter/{case.encounter_id}")
        if case.procedure_id:
            evidence.append(f"Procedure/{case.procedure_id}")
        if nxt:
            evidence.append(f"Appointment/{nxt.appointment_id}")
        out.append(Detected(
            key=f"or_overrun:{case.appointment_id}", rule="or_overrun", severity=severity,
            title=f"{case.room}: {case.procedure} predicted {case.overrun_minutes} min over", summary=summary,
            unit_id="OR", subject_ref=f"Appointment/{case.appointment_id}", facts=facts, evidence_refs=evidence,
            menu=menu))
    return out


# ---------- pre-op checklist gaps ----------


def preop_gap(snap: Snapshot, cfg: RuleConfig = CONFIG) -> list[Detected]:
    out = []
    for case in snap.or_cases:
        if case.status != "booked" or not case.preop_open:
            continue
        minutes = int((case.booked_start - snap.now).total_seconds() // 60)
        if minutes >= cfg.preop_hours * 60:
            continue
        items = [PREOP_LABEL[i] for i in case.preop_open]
        if minutes < cfg.preop_high_hours * 60 or "preop-consent" in case.preop_open:
            severity: Severity = "high"
        elif len(items) >= 2:
            severity = "med"
        else:
            severity = "low"
        following = sorted((c for c in snap.or_cases if c.room == case.room and c.status == "booked"
                            and c.booked_start > case.booked_start), key=lambda c: c.booked_start)
        nxt = following[0] if following else None
        facts = {"case": case.appointment_id, "room": case.room, "procedure": case.procedure,
                 "surgeon": case.surgeon, "booked_start": _hhmm(case.booked_start), "minutes_to_start": minutes,
                 "open_items": items, "open_count": len(items), "threshold_hours": cfg.preop_hours,
                 "next_case": {"case": nxt.appointment_id, "booked_start": _hhmm(nxt.booked_start)} if nxt else None}
        summary = (f"{case.procedure} in {case.room} starts at {case.booked_start:%H:%M}, in {minutes} minutes, with "
                   f"{_plural(len(items), 'pre-op item')} still open: {', '.join(items)}.")
        menu = [MenuItem(action_id="complete_preop", owner_role="nurse",
                         label=f"Complete the open pre-op items ({', '.join(items)}) before {case.booked_start:%H:%M}",
                         why=f"{_plural(len(items), 'item')} open with {minutes} minutes to go",
                         effect="the case starts on time"),
                MenuItem(action_id="notify_surgeon", owner_role="physician",
                         label=f"Tell {case.surgeon or 'the surgeon'} the {case.booked_start:%H:%M} case may start late",
                         why=f"{', '.join(items)} not done yet", effect="the surgeon can plan around a late start")]
        if nxt:
            menu.append(MenuItem(
                action_id="standby_next_case", owner_role="operations_manager",
                label=f"Put the next case in {case.room} (booked {nxt.booked_start:%H:%M}) on standby to go first",
                why="the room stays busy if this case cannot start", effect="no idle OR time"))
        evidence = [f"Appointment/{case.appointment_id}"] + [f"Task/{case.preop_tasks[i]}" for i in case.preop_open
                                                             if i in case.preop_tasks]
        if case.encounter_id:
            evidence.append(f"Encounter/{case.encounter_id}")
        out.append(Detected(
            key=f"preop_gap:{case.appointment_id}", rule="preop_gap", severity=severity,
            title=f"{case.room} {case.booked_start:%H:%M}: pre-op incomplete ({len(items)})", summary=summary,
            unit_id="OR", subject_ref=f"Appointment/{case.appointment_id}", facts=facts, evidence_refs=evidence,
            menu=menu))
    return out


RULES = (unit_occupancy, ed_boarding, or_overrun, preop_gap)


def evaluate(snap: Snapshot, cfg: RuleConfig = CONFIG) -> list[Detected]:
    """Every exception the rules find, most severe first."""
    found = [d for rule in RULES for d in rule(snap, cfg)]
    return sorted(found, key=lambda d: (-SEVERITY_RANK[d.severity], d.rule, d.key))
