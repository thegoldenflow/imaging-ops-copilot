"""System 16 · Referral Analytics Dashboard.

A referral is an ordered exam (an appointment), dated by its exam date, so the
90-day history gives complete weeks. Volume is broken down by referrer,
specialty, modality, site and week. Referrers whose recent weekly volume fell
well below their own baseline form a visit list.

The weekly summary is drafted by Claude from query results only. The model
never writes a number: it places facts by key ({last_week_total}) and the
server fills in the values. A draft containing any digit of its own, or an
unknown fact key, fails validation (one retry, then needs human review). Every
fact names the dashboard tile that shows the same value."""

import json
import random
import re
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from pydantic import BaseModel, model_validator

from app.core.models import Appointment
from app.core.store import Store
from app.llm.gateway import get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture

WEEKS = 12
RECENT_WEEKS = 4
DECLINE_THRESHOLD = 0.4  # recent weekly average at least 40% below baseline
MIN_BASELINE_PER_WEEK = 3.0
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weeks(now: datetime) -> list[date]:
    """Start dates of the last WEEKS full weeks (oldest first)."""
    current = week_start(now.date())
    return [current - timedelta(weeks=WEEKS - i) for i in range(WEEKS)]


def pct_label(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def referrals(store: Store, now: datetime, *, specialty: str | None = None, modality: str | None = None,
              site_id: str | None = None, referrer_id: str | None = None) -> list[Appointment]:
    """Referred exams in the analysis window (last WEEKS full weeks), filtered."""
    start = datetime.combine(weeks(now)[0], datetime.min.time())
    end = datetime.combine(week_start(now.date()), datetime.min.time())
    out = []
    for a in list(store.appointments.values()):
        if not (start <= a.start < end):
            continue
        if site_id and a.site_id != site_id:
            continue
        if referrer_id and a.referrer_id != referrer_id:
            continue
        if modality and store.exams[a.exam_code].modality != modality:
            continue
        if specialty and store.referrers[a.referrer_id].specialty != specialty:
            continue
        out.append(a)
    return out


def weekly(rows: list[Appointment], labels: list[date]) -> list[int]:
    index = {d: i for i, d in enumerate(labels)}
    counts = [0] * len(labels)
    for a in rows:
        i = index.get(week_start(a.start.date()))
        if i is not None:
            counts[i] += 1
    return counts


def referrer_trends(store: Store, rows: list[Appointment], labels: list[date]) -> list[dict]:
    by_ref: dict[str, list[Appointment]] = {}
    for a in rows:
        by_ref.setdefault(a.referrer_id, []).append(a)
    out = []
    for rid, mine in by_ref.items():
        ref = store.referrers[rid]
        series = weekly(mine, labels)
        baseline = sum(series[:-RECENT_WEEKS]) / (WEEKS - RECENT_WEEKS)
        recent = sum(series[-RECENT_WEEKS:]) / RECENT_WEEKS
        change = (recent - baseline) / baseline if baseline else None
        out.append({
            "referrer_id": rid, "name": ref.name, "specialty": ref.specialty, "clinic": ref.clinic, "phone": ref.phone,
            "is_key": ref.is_key, "total": len(mine), "last_week": series[-1], "series": series,
            "baseline_per_week": round(baseline, 1), "recent_per_week": round(recent, 1),
            "change": round(change, 3) if change is not None else None, "change_label": pct_label(change),
            "last_referral": max(a.start for a in mine).date().isoformat(),
            "declining": baseline >= MIN_BASELINE_PER_WEEK and change is not None and change <= -DECLINE_THRESHOLD,
        })
    out.sort(key=lambda r: -r["total"])
    return out


def _breakdown(rows: list[Appointment], key, last_week: date) -> list[dict]:
    counts: dict[str, list[int]] = {}
    for a in rows:
        c = counts.setdefault(key(a), [0, 0])
        c[0] += 1
        c[1] += week_start(a.start.date()) == last_week
    return [{"key": k, "total": t, "last_week": w} for k, (t, w) in sorted(counts.items(), key=lambda kv: -kv[1][0])]


def overview(store: Store, now: datetime, **filters) -> dict:
    labels = weeks(now)
    rows = referrals(store, now, **filters)
    series = weekly(rows, labels)
    last, prior = series[-1], series[-2]
    trends = referrer_trends(store, rows, labels)
    by_modality: dict[str, list[Appointment]] = {}
    for a in rows:
        by_modality.setdefault(str(store.exams[a.exam_code].modality), []).append(a)
    return {
        "weeks": [d.isoformat() for d in labels],
        "labels": [d.strftime("%b %d") for d in labels],
        "kpis": {
            "total": len(rows), "last_week": last, "week_label": f"the week of {labels[-1]:%B} {labels[-1].day}", "prior_week": prior,
            "week_change": round((last - prior) / prior, 3) if prior else None,
            "week_change_label": pct_label((last - prior) / prior if prior else None),
            "avg_per_week": round(len(rows) / WEEKS, 1), "active_referrers": len(trends),
            "declining": sum(r["declining"] for r in trends),
        },
        "series": [{"name": "All referrals", "values": series}],
        "series_by_modality": [{"name": m, "values": weekly(v, labels)} for m, v in sorted(by_modality.items())],
        "by_specialty": _breakdown(rows, lambda a: store.referrers[a.referrer_id].specialty, labels[-1]),
        "by_modality": _breakdown(rows, lambda a: str(store.exams[a.exam_code].modality), labels[-1]),
        "by_site": [{**b, "name": store.sites[b["key"]].name} for b in _breakdown(rows, lambda a: a.site_id, labels[-1])],
        "by_exam": [{**b, "name": store.exams[b["key"]].name} for b in _breakdown(rows, lambda a: a.exam_code, labels[-1])],
        "referrers": trends,
        "visit_list": sorted((r for r in trends if r["declining"]), key=lambda r: r["change"]),
    }


# ---------- Visit list follow-up ----------

class VisitPlan(BaseModel):
    referrer_id: str
    status: str  # planned, visited
    note: str = ""
    by: str
    at: datetime


def visits(store: Store) -> dict[str, VisitPlan]:
    return store.module("referral_visits", dict)


# ---------- Weekly summary ----------

class Fact(BaseModel):
    key: str
    label: str
    value: str
    tile: str  # data-testid of the dashboard element showing the same value


def facts(store: Store, now: datetime) -> dict[str, Fact]:
    """Everything the summary may state, straight from the dashboard queries."""
    data = overview(store, now)
    k, labels = data["kpis"], data["weeks"]
    out = [
        Fact(key="week_label", label="Week", value=k["week_label"], tile="kpi-week"),
        Fact(key="last_week_total", label="Referrals last week", value=str(k["last_week"]), tile="kpi-last-week"),
        Fact(key="prior_week_total", label="Referrals the week before", value=str(k["prior_week"]), tile="kpi-prior-week"),
        Fact(key="week_change", label="Change from the week before", value=k["week_change_label"], tile="kpi-change"),
        Fact(key="avg_per_week", label=f"Weekly average over {WEEKS} weeks", value=str(k["avg_per_week"]), tile="kpi-avg"),
        Fact(key="declining_count", label="Referrers with a marked drop", value=str(k["declining"]), tile="kpi-declining"),
    ]
    if data["by_modality"]:
        top = max(data["by_modality"], key=lambda b: b["last_week"])
        out += [Fact(key="top_modality", label="Busiest modality last week", value=top["key"], tile=f"mod-{top['key']}"),
                Fact(key="top_modality_count", label="Its referrals last week", value=str(top["last_week"]), tile=f"mod-{top['key']}-week")]
    if data["by_site"]:
        top = max(data["by_site"], key=lambda b: b["last_week"])
        out += [Fact(key="top_site", label="Busiest site last week", value=top["name"], tile=f"site-{top['key']}"),
                Fact(key="top_site_count", label="Its referrals last week", value=str(top["last_week"]), tile=f"site-{top['key']}-week")]
    if data["referrers"]:
        top = max(data["referrers"], key=lambda r: (r["last_week"], r["total"]))
        out += [Fact(key="top_referrer", label="Top referrer last week", value=top["name"], tile=f"ref-{top['referrer_id']}"),
                Fact(key="top_referrer_count", label="Their referrals last week", value=str(top["last_week"]),
                     tile=f"ref-{top['referrer_id']}-week")]
    if data["visit_list"]:
        worst = data["visit_list"][0]
        out += [Fact(key="largest_drop_referrer", label="Largest drop", value=worst["name"], tile=f"visit-{worst['referrer_id']}"),
                Fact(key="largest_drop_change", label="Their change vs baseline", value=worst["change_label"],
                     tile=f"visit-{worst['referrer_id']}-change")]
    return {f.key: f for f in out}


# Fact keys allowed for the summary being validated (set per call).
_ALLOWED: ContextVar[set[str]] = ContextVar("referral_summary_facts", default=set())


class SummaryOutput(BaseModel):
    headline: str
    sentences: list[str]

    @model_validator(mode="after")
    def numbers_only_from_facts(self):
        allowed = _ALLOWED.get()
        if not 2 <= len(self.sentences) <= 6:
            raise ValueError("Write between 2 and 6 sentences")
        for text in [self.headline, *self.sentences]:
            unknown = set(PLACEHOLDER.findall(text)) - allowed
            if unknown:
                raise ValueError(f"Unknown fact keys: {', '.join(sorted(unknown))}")
            if re.search(r"\d", PLACEHOLDER.sub("", text)):
                raise ValueError(f"Write numbers only as fact placeholders, never as digits: {text!r}")
        return self


SUMMARY_PROMPT = Prompt(
    name="referral_weekly_summary",
    version="referral_weekly_summary@1",
    system=(
        "You write a short weekly referral summary for the operations team of an outpatient imaging group. "
        "You are given named facts computed by database queries. Never write a number, percentage or date yourself: "
        "insert facts only as placeholders in curly braces, e.g. {last_week_total}. Use only the keys provided. "
        "Do not infer causes; you may suggest follow-up such as visiting referrers whose volume dropped. "
        "Return a headline and 2 to 6 plain sentences."
    ),
    template="Facts (key: label = value):\n{facts}",
)


def _facts_text(fs: dict[str, Fact]) -> str:
    return "\n".join(f"{f.key}: {f.label} = {f.value}" for f in fs.values())


@mock_fixture("referral_weekly_summary")
def _mock(text: str, images: list, attempt: int) -> dict:
    keys = set(re.findall(r"^([a-z0-9_]+):", text, re.M))
    sentences = ["In {week_label} the group received {last_week_total} referrals, {week_change} compared with "
                 "{prior_week_total} the week before; the twelve-week average is {avg_per_week} per week."]
    if {"top_modality", "top_site"} <= keys:
        sentences.append("{top_modality} was the busiest modality with {top_modality_count} referrals, and {top_site} "
                         "received the most ({top_site_count}).")
    if "top_referrer" in keys:
        sentences.append("{top_referrer} referred the most patients last week ({top_referrer_count}).")
    if "largest_drop_referrer" in keys:
        sentences.append("{declining_count} referrers are well below their usual volume; the largest drop is "
                         "{largest_drop_referrer} at {largest_drop_change}, so a visit is suggested.")
    else:
        sentences.append("No referrer is markedly below their usual volume.")
    return {"headline": "Referrals for {week_label}: {last_week_total} ({week_change})", "sentences": sentences}


class WeeklySummary(BaseModel):
    week_start: str
    headline: list[dict]
    sentences: list[list[dict]]  # each sentence as segments: {"text"} or {"fact", "value", "label", "tile"}
    facts: dict[str, Fact]
    status: str  # draft, approved
    ai_status: str
    llm_call_id: str | None
    model: str
    prompt_version: str
    generated_at: datetime
    generated_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    error: str | None = None


def summaries(store: Store) -> dict[str, WeeklySummary]:
    return store.module("referral_summaries", dict)


def segments(text: str, fs: dict[str, Fact]) -> list[dict]:
    out, pos = [], 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > pos:
            out.append({"text": text[pos:m.start()]})
        f = fs[m.group(1)]
        out.append({"fact": f.key, "value": f.value, "label": f.label, "tile": f.tile})
        pos = m.end()
    if pos < len(text):
        out.append({"text": text[pos:]})
    return out


def generate_summary(store: Store, now: datetime, by: str) -> WeeklySummary:
    fs = facts(store, now)
    token = _ALLOWED.set(set(fs))
    try:
        outcome = get_gateway().structured(task="referral_weekly_summary", prompt=SUMMARY_PROMPT,
                                           variables={"facts": _facts_text(fs)}, schema_cls=SummaryOutput, tier="fast")
    finally:
        _ALLOWED.reset(token)
    data = outcome.data or {"headline": "", "sentences": []}
    summary = WeeklySummary(
        week_start=weeks(now)[-1].isoformat(), headline=segments(data["headline"], fs),
        sentences=[segments(s, fs) for s in data["sentences"]], facts=fs, status="draft", ai_status=outcome.status,
        llm_call_id=outcome.call_id, model=outcome.model, prompt_version=outcome.prompt_version, generated_at=now,
        generated_by=by, error=outcome.error,
    )
    summaries(store)[summary.week_start] = summary
    store.touch()
    return summary


# ---------- Seed ----------

def seed_declines(s: Store, now: datetime, seed: int) -> list[str]:
    """Six referrers lose most of their referrals over the last four weeks (moved to
    colleagues), so the visit list has real cases. Runs before studies are seeded."""
    rng = random.Random(seed)
    cutoff = datetime.combine(week_start(now.date()) - timedelta(weeks=RECENT_WEEKS), datetime.min.time())
    start = datetime.combine(weeks(now)[0], datetime.min.time())
    counts: dict[str, int] = {}
    for a in s.appointments.values():
        if start <= a.start < cutoff:
            counts[a.referrer_id] = counts.get(a.referrer_id, 0) + 1
    ranked = sorted(counts, key=lambda r: -counts[r])
    chosen = rng.sample(ranked[:40], 6)
    others = [r for r in s.referrers if r not in chosen]
    keep = {rid: rng.uniform(0.15, 0.4) for rid in chosen}
    for a in s.appointments.values():
        if a.referrer_id in keep and a.start >= cutoff and rng.random() > keep[a.referrer_id]:
            a.referrer_id = rng.choice(others)
    s.modules["referral_planted_declines"] = chosen
    return chosen
