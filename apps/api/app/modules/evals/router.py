"""Evaluation results for the AI features (written by app.modules.evals.run)."""

import json

from fastapi import APIRouter, Depends

from app.core.auth import CLINICAL_STAFF, require_roles
from app.core.models import StaffUser
from app.core.store import get_store
from app.modules.evals.paths import RESULTS
from app.modules.triage import service as triage

router = APIRouter(prefix="/api/evals", tags=["evals"])


@router.get("")
def results(user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    out = {}
    if RESULTS.exists():
        for path in sorted(RESULTS.glob("*.json")):
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return {"results": out, "live_triage_agreement": triage.agreement(get_store()),
            "command": "cd apps/api && uv run python -m app.modules.evals.run"}
