"""Evals of the Control Tower (spec 7.1), run by scripts/control_tower_eval.py and gated in the backend suite.

1. Rule engine: 20 scripted scenarios (evals/control_tower/rules/cases.jsonl). Each
   describes a hospital in a few lines (units, ED boarders, OR cases with booked and
   predicted minutes, open pre-op items); `scenario_snapshot` builds the boards the
   engine reads (the OR schedule through the same code as the live snapshot) and
   the exceptions it finds must match the expected keys and severities exactly.
2. Narrator: 30 exceptions from the seeded hospital, collected while the day
   simulator plays the scripted scenarios (in a rolled-back transaction). Each is
   narrated through the agent runtime; scored: the output validates against the
   schema, every evidence reference resolves in FHIR, the guards accepted the
   model's output (no fallback to the engine's template). With the mock model the
   gate is 100% on all three; `--live` runs the configured model. The spec's human
   rating of narrative accuracy needs a person: a rating sheet is written for it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.ehr.reference import UNIT_BY_ID, UNITS
from app.modules.control_tower import rules
from app.modules.control_tower.agent import NarratorOutput
from app.modules.control_tower.snapshot import (
    BedCell,
    EdRow,
    Explained,
    Kpis,
    OrCase,
    Snapshot,
    UnitRow,
    _rooms,
    _schedule_rooms,
)
from app.modules.evals.paths import EVALS_DIR as BASE_EVALS

EVALS_DIR = BASE_EVALS / "control_tower"
RULE_CASES = EVALS_DIR / "rules" / "cases.jsonl"
NARRATOR_CASES = EVALS_DIR / "narrator" / "cases.jsonl"


# ---------- 1. rule engine ----------


def _at(now: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def _explained(value: float) -> Explained:
    return Explained(value=value, factors=[], sentence=f"{value}", sentence_names=f"{value}")


def scenario_snapshot(case: dict) -> Snapshot:
    now = datetime.fromisoformat(case["now"])
    units, beds = [], {u.id: [] for u in UNITS}
    for spec in case.get("units", []):
        unit = UNIT_BY_ID[spec["id"]]
        occupied, cleaning = spec["occupied"], spec.get("cleaning", 0)
        free = spec.get("free", unit.beds - occupied - cleaning)
        ready = spec.get("discharge_ready", [])
        cells = []
        for i in range(unit.beds):
            bed = f"{unit.id}-{i + 1:02d}-A"
            if i < occupied:
                p = ready[i] if i < len(ready) else 0.2
                cells.append(BedCell(id=bed, room=bed[:-2], unit=unit.id, status="O", encounter_id=f"stay-s{i:04d}",
                                     discharge=_explained(p), alc=i < spec.get("alc", 0)))
            elif i < occupied + cleaning:
                cells.append(BedCell(id=bed, room=bed[:-2], unit=unit.id, status="K"))
            else:
                cells.append(BedCell(id=bed, room=bed[:-2], unit=unit.id, status="U"))
        beds[unit.id] = cells
        exp_adm, exp_dis = spec.get("expected_admissions", 0), spec.get("expected_discharges", 0)
        units.append(UnitRow(id=unit.id, name=unit.name, kind=unit.kind, beds=unit.beds, occupied=occupied, free=free,
                             cleaning=cleaning, closed=0, occupancy=round(occupied / unit.beds, 4),
                             alc=spec.get("alc", 0), expected_discharges=exp_dis,
                             discharge_ready=sum(1 for p in ready if p >= 0.7),
                             ed_boarders=spec.get("ed_boarders", 0), expected_admissions=exp_adm,
                             electives_24h=spec.get("electives_24h", 0), net_gap=exp_adm - exp_dis - free))
    for u in UNITS:  # units the scenario leaves out: half full, no pressure
        if u.kind != "ed" and u.id not in {x.id for x in units}:
            units.append(UnitRow(id=u.id, name=u.name, kind=u.kind, beds=u.beds, occupied=u.beds // 2,
                                 free=u.beds - u.beds // 2, cleaning=0, closed=0, occupancy=0.5, alc=0,
                                 expected_discharges=0, discharge_ready=0, ed_boarders=0, expected_admissions=0,
                                 electives_24h=0, net_gap=-(u.beds - u.beds // 2)))
    ed = []
    for i, b in enumerate(case.get("boarders", [])):
        requested = now - timedelta(minutes=b["minutes"])
        ed.append(EdRow(encounter_id=f"ed-s{i:04d}", patient_id=None, mrn=None, location=f"ED-{i + 1:02d}-A",
                        status="in-progress", arrival=requested - timedelta(hours=3), triaged_at=None, seen_at=None,
                        ctas=3, minutes_in_ed=b["minutes"] + 180, waiting_to_be_seen=False, wait_minutes=None,
                        admit=None, orders_total=2, orders_open=0, bed_request_id=f"bedreq-ed-s{i:04d}",
                        bed_requested_at=requested, boarding_minutes=b["minutes"], target_unit=b.get("unit", "MEDA"),
                        target_unit_source="bed request", lwbs_risk=False))
    cases = []
    for c in case.get("or_cases", []):
        start = _at(now, c["start"])
        status = c.get("status", "booked")
        actual = now - timedelta(minutes=c["elapsed"]) if status == "arrived" else None
        cases.append(OrCase(
            appointment_id=c["id"], room=c["room"], procedure_code=None, procedure=c.get("procedure", "Case"),
            surgeon_id=None, surgeon="Dr. Test", patient_id=None, mrn=None, encounter_id=None, status=status,
            urgency=c.get("urgency", "elective"), asa=2, booked_start=start,
            booked_end=start + timedelta(minutes=c["booked"]), booked_minutes=c["booked"], actual_start=actual,
            predicted=_explained(float(c["predicted"])), preop={i: "open" for i in c.get("preop_open", [])},
            preop_tasks={i: f"task-s{n}" for n, i in enumerate(c.get("preop_open", []))},
            preop_open=list(c.get("preop_open", [])) if status == "booked" else [],
            post_op_unit=c.get("post_op_unit")))
    _schedule_rooms(cases, now)
    kpis = Kpis(ed_census=len(ed), ed_waiting=0, ed_boarders=len(ed), ed_boarders_over_2h=0, ed_predicted_wait=None,
                lwbs_risk=0, occupancy=0, occupied=0, beds=0, free=0, cleaning=0, expected_discharges=0,
                expected_admissions=0, net_gap=0, or_cases=len(cases), or_done=0, or_in_progress=0,
                or_utilization=None, or_booked_utilization=None, or_cancellation_risk=0, or_predicted_end=None)
    return Snapshot(now=now, key=case["id"], built_at=now, build_ms=0, models={}, kpis=kpis, ed=ed, units=units,
                    beds=beds, or_cases=cases, or_rooms=_rooms(cases, now))


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rule_eval(cases: list[dict] | None = None) -> dict:
    cases = cases if cases is not None else load_cases(RULE_CASES)
    results = []
    for case in cases:
        found = rules.evaluate(scenario_snapshot(case))
        got = sorted([d.key, d.severity] for d in found)
        expected = sorted([list(x) for x in case["expected"]])
        problems = [f"{d.key}: menu empty" for d in found if not d.menu] + \
                   [f"{d.key}: no evidence" for d in found if not d.evidence_refs]
        results.append({"id": case["id"], "title": case["title"], "expected": expected, "got": got,
                        "passed": got == expected and not problems, "problems": problems})
    passed = sum(r["passed"] for r in results)
    return {"eval": "control_tower.rules", "cases": len(results), "passed": passed,
            "all_passed": passed == len(results), "results": results}


# ---------- 2. narrator ----------


def score_narration(exc: dict, narration: dict, resolvable: set[str]) -> dict:
    output = {k: narration.get(k) for k in ("exception_id", "severity", "narrative", "recommended_actions",
                                            "evidence_refs")}
    try:
        NarratorOutput.model_validate(output)
        schema_ok = True
    except ValidationError:
        schema_ok = False
    refs = narration.get("evidence_refs") or []
    cited = refs + (narration.get("unresolved_refs") or [])
    grounded = bool(refs) and not narration.get("unresolved_refs") and all(r in resolvable for r in refs)
    return {"id": exc["id"], "rule": exc["rule"], "severity": exc["severity"], "schema_valid": schema_ok,
            "evidence_resolvable": grounded, "evidence": len(cited), "source": narration.get("source"),
            "guard_passed": narration.get("source") == "model", "attempts": narration.get("attempts"),
            "problems": narration.get("problems") or [], "narrative": narration.get("narrative")}


def narrator_summary(scores: list[dict], mode: str) -> dict:
    n = len(scores) or 1
    return {"eval": "control_tower.narrator", "mode": mode, "cases": len(scores),
            "schema_validity": round(sum(s["schema_valid"] for s in scores) / n, 4),
            "grounding_rate": round(sum(s["evidence_resolvable"] for s in scores) / n, 4),
            "guard_pass_rate": round(sum(s["guard_passed"] for s in scores) / n, 4),
            "template_fallbacks": sum(1 for s in scores if s["source"] != "model"),
            "by_rule": {r: sum(1 for s in scores if s["rule"] == r) for r in sorted({s["rule"] for s in scores})},
            "passed": len(scores) >= 30 and all(s["schema_valid"] and s["evidence_resolvable"] for s in scores),
            "human_rating": "pending: score each narrative 1-5 for accuracy in narrator/rating_sheet.md (gate 4/5)",
            "results": scores}
