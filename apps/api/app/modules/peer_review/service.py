"""System 13 · Peer Review / QA.

Every night (and on demand) a configurable share of the reports signed since
the last run is sampled and assigned, blinded, to another radiologist
credentialed for the modality. A report is never assigned back to the
radiologist who signed it. Reviewers grade agreement (RADPEER-style concur /
minor / significant) and name the discrepancy type; the QA lead (the medical
director) sees aggregate results by radiologist and exam type."""

import csv
import io
import random
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import Role, StaffUser
from app.core.store import Store
from app.modules.reports.service import Report, reports

SCORES = {
    "concur": "Concur with interpretation",
    "minor": "Minor discrepancy, unlikely to be clinically significant",
    "significant": "Significant discrepancy, likely clinically significant",
}
DISCREPANCY_TYPES = {
    "perception": "Finding missed (perception)",
    "interpretation": "Finding misinterpreted",
    "communication": "Recommendation or communication",
    "technique": "Technique or protocol",
    "clarity": "Report clarity",
    "other": "Other",
}


class QaConfig(BaseModel):
    sample_rate: float = Field(0.05, gt=0, le=0.5)
    run_hour: int = Field(2, ge=0, le=23)  # nightly run time
    enabled: bool = True
    updated_by: str | None = None
    updated_at: datetime | None = None


class SamplingRun(BaseModel):
    id: str
    ts: datetime
    trigger: str  # schedule, manual
    by: str
    window_start: datetime
    candidates: int
    sampled: int
    assigned: int
    unassigned: int


class PeerReview(BaseModel):
    id: str
    run_id: str
    report_id: str
    study_id: str
    original_reader_id: str
    reviewer_id: str | None
    assigned_at: datetime
    status: str = "assigned"  # assigned, completed, unassigned
    score: str | None = None
    discrepancy_type: str | None = None
    comment: str = ""
    completed_at: datetime | None = None


class ReviewError(Exception):
    pass


def config(store: Store) -> QaConfig:
    return store.module("qa_config", QaConfig)


def reviews(store: Store) -> dict[str, PeerReview]:
    return store.module("peer_reviews", dict)


def runs(store: Store) -> list[SamplingRun]:
    return store.module("qa_runs", list)


def state(store: Store) -> dict:
    return store.module("qa_state", dict)  # last_run_at, last_scheduled_date


def _modality(store: Store, report: Report):
    return store.exams[store.studies[report.study_id].exam_code].modality


def pick_reviewer(store: Store, report: Report, load: dict[str, int]) -> str | None:
    """Least-loaded radiologist credentialed for the modality, never the original reader."""
    mod = _modality(store, report)
    options = [u for u in store.staff.values() if u.role == Role.RADIOLOGIST and u.id != report.signed_by_id
               and mod in u.reading_modalities]
    if not options:
        return None
    return min(options, key=lambda u: (load.get(u.id, 0), u.id)).id


def open_load(store: Store) -> dict[str, int]:
    load: dict[str, int] = {}
    for r in reviews(store).values():
        if r.status == "assigned" and r.reviewer_id:
            load[r.reviewer_id] = load.get(r.reviewer_id, 0) + 1
    return load


def run_sampling(store: Store, *, now: datetime, trigger: str, by: str, rng: random.Random | None = None,
                 rate: float | None = None) -> SamplingRun:
    rng = rng or random.Random()
    st = state(store)
    since = st.get("last_run_at") or now - timedelta(days=1)
    sampled_reports = {r.report_id for r in reviews(store).values()}
    candidates = [r for r in list(reports(store).values()) if r.status == "signed" and r.signed_by_id and r.signed_at
                  and since < r.signed_at <= now and r.id not in sampled_reports]
    candidates.sort(key=lambda r: r.id)
    k = min(len(candidates), max(1, round(len(candidates) * (rate or config(store).sample_rate)))) if candidates else 0
    chosen = rng.sample(candidates, k)
    run = SamplingRun(id=store.next_id("QARUN"), ts=now, trigger=trigger, by=by, window_start=since,
                      candidates=len(candidates), sampled=k, assigned=0, unassigned=0)
    load = open_load(store)
    for report in chosen:
        reviewer = pick_reviewer(store, report, load)
        if reviewer == report.signed_by_id:  # defence in depth; pick_reviewer already excludes it
            reviewer = None
        review = PeerReview(id=store.next_id("PR"), run_id=run.id, report_id=report.id, study_id=report.study_id,
                            original_reader_id=report.signed_by_id, reviewer_id=reviewer, assigned_at=now,
                            status="assigned" if reviewer else "unassigned")
        reviews(store)[review.id] = review
        if reviewer:
            load[reviewer] = load.get(reviewer, 0) + 1
            run.assigned += 1
        else:
            run.unassigned += 1
    st["last_run_at"] = now
    runs(store).append(run)
    store.touch()
    return run


def maybe_run_scheduled(store: Store, now: datetime | None = None) -> SamplingRun | None:
    """Called by the worker loop: one scheduled run per day once the run hour has passed."""
    now = now or datetime.now()
    cfg, st = config(store), state(store)
    if not cfg.enabled or now.hour < cfg.run_hour or st.get("last_scheduled_date") == now.date():
        return None
    st["last_scheduled_date"] = now.date()
    return run_sampling(store, now=now, trigger="schedule", by="scheduler")


def submit(store: Store, review: PeerReview, *, reviewer: StaffUser, score: str, discrepancy_type: str | None,
           comment: str, now: datetime | None = None) -> PeerReview:
    if review.reviewer_id != reviewer.id:
        raise ReviewError("Only the assigned reviewer can submit this review")
    if review.status == "completed":
        raise ReviewError("Review already submitted")
    if score not in SCORES:
        raise ReviewError(f"Score must be one of {', '.join(SCORES)}")
    if score != "concur" and discrepancy_type not in DISCREPANCY_TYPES:
        raise ReviewError("Choose the discrepancy type")
    review.score = score
    review.discrepancy_type = None if score == "concur" else discrepancy_type
    review.comment = comment.strip()
    review.status, review.completed_at = "completed", now or datetime.now()
    store.touch()
    return review

# ---------- QA report ----------


def _summary(items: list[PeerReview]) -> dict:
    n = len(items)
    counts = {s: sum(r.score == s for r in items) for s in SCORES}
    return {"reviews": n, **counts, "concur_rate": round(counts["concur"] / n, 3) if n else None,
            "significant_rate": round(counts["significant"] / n, 3) if n else None}


def qa_report(store: Store) -> dict:
    done = [r for r in reviews(store).values() if r.status == "completed"]
    by_reader = []
    for u in store.staff.values():
        if u.role != Role.RADIOLOGIST:
            continue
        mine = [r for r in done if r.original_reader_id == u.id]
        by_reader.append({"radiologist_id": u.id, "name": u.name, **_summary(mine),
                          "by_modality": {m: _summary([r for r in mine if store.exams[store.studies[r.study_id].exam_code].modality == m])
                                          for m in u.reading_modalities}})
    by_exam = []
    for code, exam in store.exams.items():
        items = [r for r in done if store.studies[r.study_id].exam_code == code]
        if items:
            by_exam.append({"exam_code": code, "exam_name": exam.name, "modality": exam.modality, **_summary(items)})
    types: dict[str, int] = {}
    for r in done:
        if r.discrepancy_type:
            types[r.discrepancy_type] = types.get(r.discrepancy_type, 0) + 1
    return {"overall": _summary(done), "by_radiologist": by_reader,
            "by_exam": sorted(by_exam, key=lambda x: -x["reviews"]), "discrepancy_types": types}


def export_csv(store: Store) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["review_id", "completed_at", "exam", "modality", "original_reader", "reviewer", "score",
                "discrepancy_type", "comment", "report_id"])
    for r in sorted(reviews(store).values(), key=lambda r: r.completed_at or datetime.max):
        if r.status != "completed":
            continue
        study = store.studies[r.study_id]
        exam = store.exams[study.exam_code]
        w.writerow([r.id, r.completed_at.isoformat(timespec="minutes"), exam.name, exam.modality.value,
                    store.staff[r.original_reader_id].name, store.staff[r.reviewer_id].name, r.score,
                    r.discrepancy_type or "", r.comment, r.report_id])
    return out.getvalue()

# ---------- Seed ----------

# Synthetic quality profile: Dr. Webb's MRI reads disagree more often.
DISAGREE = {("U-RAD2", "MRI"): (0.2, 0.15)}
SEED_RATE = 0.2  # the seeded history is a larger audit sample so the QA report has signal
COMMENTS = {
    "perception": "Small finding not mentioned in the report.",
    "interpretation": "Would have characterised the finding differently.",
    "communication": "Recommendation for follow-up missing.",
    "clarity": "Impression does not match the findings section.",
}


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Nightly runs over the seeded history: older samples are reviewed, the last
    two nights' samples are still waiting for their reviewers."""
    cfg = config(s)
    st = state(s)
    first = (now - timedelta(days=13)).replace(hour=cfg.run_hour, minute=0)
    st["last_run_at"] = first - timedelta(days=1)
    day = first
    while day <= now:
        run = run_sampling(s, now=day, trigger="schedule", by="scheduler", rng=rng, rate=SEED_RATE)
        for review in [r for r in reviews(s).values() if r.run_id == run.id and r.reviewer_id]:
            if day.date() >= now.date() - timedelta(days=1):
                continue  # still open
            study = s.studies[review.study_id]
            minor, significant = DISAGREE.get((review.original_reader_id, s.exams[study.exam_code].modality.value), (0.07, 0.02))
            roll = rng.random()
            score = "significant" if roll < significant else "minor" if roll < significant + minor else "concur"
            dtype = None if score == "concur" else rng.choice(list(COMMENTS))
            review.score, review.discrepancy_type = score, dtype
            review.comment = COMMENTS[dtype] if dtype else ""
            review.status, review.completed_at = "completed", min(now, day + timedelta(hours=rng.randint(6, 40)))
        st["last_scheduled_date"] = day.date()
        day += timedelta(days=1)
