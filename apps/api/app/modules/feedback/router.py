"""System 19 · Patient Feedback API, plus the public survey page."""

import statistics
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.modules.feedback import service
from app.modules.feedback.lexicon import THEMES

router = APIRouter(tags=["feedback"])

VIEWERS = require_roles(Role.FRONT_DESK, Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)
MANAGERS = require_roles(Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)
WEEKS = 12


def response_view(store: Store, r: service.FeedbackResponse) -> dict:
    return {**r.model_dump(mode="json"), "patient_name": store.patients[r.patient_id].full_name,
            "site_name": store.sites[r.site_id].name, "exam_name": store.exams[r.exam_code].name}


def _sites(user: StaffUser, site_id: str | None) -> list[str] | None:
    if user.site_ids:
        return [site_id] if site_id in user.site_ids else user.site_ids
    return [site_id] if site_id else None


@router.get("/api/feedback/overview")
def overview(request: Request, site_id: str | None = None, sentiment: str | None = None, theme: str | None = None,
             user: StaffUser = Depends(VIEWERS)):
    store, now = get_store(), datetime.now()
    sites = _sites(user, site_id)
    rows = [r for r in list(service.responses(store).values()) if not sites or r.site_id in sites]
    start = (now - timedelta(weeks=WEEKS - 1)).date()
    start -= timedelta(days=start.weekday())
    labels = [start + timedelta(weeks=i) for i in range(WEEKS)]
    by_site = []
    for sid in sorted({r.site_id for r in rows}):
        mine = [r for r in rows if r.site_id == sid]
        last30 = [r for r in mine if r.submitted_at >= now - timedelta(days=30)]
        themes = {t: sum(t in r.themes for r in last30 if r.sentiment == "negative") for t in THEMES}
        by_site.append({
            "site_id": sid, "name": store.sites[sid].name, "responses": len(mine), "responses_30d": len(last30),
            "avg_rating_30d": round(statistics.mean(r.rating for r in last30), 2) if last30 else None,
            "negative_share_30d": round(sum(r.sentiment == "negative" for r in last30) / len(last30), 3) if last30 else None,
            "top_complaint": max(themes, key=themes.get) if any(themes.values()) else None,
        })

    def week_avg(items):
        out = []
        for i, d in enumerate(labels):
            w = [r.rating for r in items if 0 <= (r.submitted_at.date() - d).days < 7]
            out.append(round(statistics.mean(w), 2) if w else None)
        return out

    theme_weeks = {t: [0] * WEEKS for t in THEMES}
    for r in rows:
        i = (r.submitted_at.date() - start).days // 7
        if 0 <= i < WEEKS:
            for t in r.themes:
                theme_weeks[t][i] += 1
    theme_counts = {t: {"positive": 0, "neutral": 0, "negative": 0} for t in THEMES}
    for r in rows:
        if r.submitted_at >= now - timedelta(days=30):
            for t in r.themes:
                theme_counts[t][r.sentiment or "neutral"] += 1
    listed = [r for r in rows if (not sentiment or r.sentiment == sentiment) and (not theme or theme in r.themes)]
    listed.sort(key=lambda r: r.submitted_at, reverse=True)
    open_alerts = sorted((a for a in service.alerts(store).values() if not sites or a.site_id in sites),
                         key=lambda a: (a.status != "open", -a.created_at.timestamp()))
    audit_phi(request, user, action="read", resource_type="patient_feedback", resource_id=None)
    return {
        "kpis": {
            "responses_30d": sum(r.submitted_at >= now - timedelta(days=30) for r in rows),
            "avg_rating_30d": round(statistics.mean(r.rating for r in rows if r.submitted_at >= now - timedelta(days=30)), 2)
            if any(r.submitted_at >= now - timedelta(days=30) for r in rows) else None,
            "open_alerts": sum(a.status == "open" for a in open_alerts),
            "to_confirm": sum(1 for r in rows if not r.confirmed_by and r.ai_status in ("ok", "seeded")),
        },
        "by_site": by_site,
        "labels": [d.strftime("%b %d") for d in labels],
        "rating_trend": [{"name": s["site_id"], "values": week_avg([r for r in rows if r.site_id == s["site_id"]])} for s in by_site],
        "theme_trend": [{"name": THEMES[t], "values": v} for t, v in theme_weeks.items() if sum(v)],
        "theme_counts": [{"theme": t, "label": THEMES[t], **c} for t, c in theme_counts.items()],
        "responses": [response_view(store, r) for r in listed[:80]],
        "alerts": [{**a.model_dump(mode="json"), "response": response_view(store, service.responses(store)[a.response_id])}
                   for a in open_alerts[:40]],
        "themes": THEMES,
        "sites": [{"id": s.id, "name": s.name} for s in store.sites.values() if not user.site_ids or s.id in user.site_ids],
    }


@router.get("/api/feedback/surveys")
def recent_surveys(request: Request, user: StaffUser = Depends(VIEWERS)):
    """Recently sent surveys, with their links (demo: open them as the patient)."""
    store = get_store()
    rows = sorted((s for s in service.surveys(store).values() if not user.site_ids or s.site_id in user.site_ids),
                  key=lambda s: s.sent_at, reverse=True)[:15]
    audit_phi(request, user, action="read", resource_type="feedback_surveys", resource_id=None)
    return {"surveys": [{**s.model_dump(mode="json"), "link": f"/feedback/{s.token}",
                         "patient_name": store.patients[s.patient_id].full_name, "exam_name": store.exams[s.exam_code].name}
                        for s in rows]}


class Confirmation(BaseModel):
    sentiment: str
    themes: list[str]


@router.post("/api/feedback/responses/{response_id}/confirm")
def confirm(response_id: str, body: Confirmation, request: Request, user: StaffUser = Depends(MANAGERS)):
    store = get_store()
    resp = service.responses(store).get(response_id)
    if resp is None:
        raise HTTPException(404, "Response not found")
    try:
        service.confirm(resp, body.sentiment, body.themes, user.name, datetime.now())
    except ValueError as e:
        raise HTTPException(422, str(e))
    store.touch()
    audit_phi(request, user, action="update", resource_type="patient_feedback", resource_id=response_id)
    return response_view(store, resp)


class FollowUp(BaseModel):
    note: str = Field(min_length=5)


@router.post("/api/feedback/alerts/{alert_id}/follow-up")
def follow_up(alert_id: str, body: FollowUp, request: Request, user: StaffUser = Depends(MANAGERS)):
    store = get_store()
    alert = service.alerts(store).get(alert_id)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    alert.status, alert.follow_up, alert.followed_up_by, alert.followed_up_at = "followed_up", body.note.strip(), user.name, datetime.now()
    store.touch()
    audit_phi(request, user, action="update", resource_type="feedback_alert", resource_id=alert_id)
    return alert.model_dump(mode="json")


# ---------- Public survey (link in the SMS) ----------

@router.get("/api/public/feedback/{token}")
def public_get(token: str):
    store = get_store()
    survey = service.survey_by_token(store, token)
    if survey is None:
        raise HTTPException(404, "This survey link is not valid")
    return {"first_name": store.patients[survey.patient_id].given_name, "language": survey.language,
            "site_name": store.sites[survey.site_id].name, "exam_name": store.exams[survey.exam_code].name,
            "submitted": survey.status == "answered"}


class Answer(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = ""


@router.post("/api/public/feedback/{token}")
def public_submit(token: str, body: Answer):
    store = get_store()
    survey = service.survey_by_token(store, token)
    if survey is None:
        raise HTTPException(404, "This survey link is not valid")
    if survey.status == "answered":
        raise HTTPException(409, "Thank you, we already have your answer")
    resp = service.submit(store, survey, body.rating, body.comment, datetime.now())
    store.touch()
    # Classification runs in the background worker within a few seconds.
    return {"ok": True, "id": resp.id}
