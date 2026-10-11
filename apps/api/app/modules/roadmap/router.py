"""Roadmap and safety-tier API (spec 8.1, 8.3, WP10): the architecture pages' data, built from the registries.

- GET /api/roadmap          one card per 8.1 row, with its registry entry and storyline step where there is one
- GET /api/roadmap/safety   the three tiers with their registered agents and modules, the tool side-effect levels with
                            their registered tools, the Level 0-4 mapping, guardrails with their latest eval results,
                            honest boundaries and the 6.7 positioning sentences

Any signed-in user: the pages describe the platform and hold no patient data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import current_user
from app.core.models import StaffUser
from app.modules.roadmap import service

router = APIRouter(prefix="/api/roadmap")


@router.get("")
def roadmap(user: StaffUser = Depends(current_user)):
    return service.cards_view()


@router.get("/safety")
def safety(user: StaffUser = Depends(current_user)):
    return service.safety_view()
