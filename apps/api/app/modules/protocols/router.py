"""System 6 · protocol library and adoption statistics."""

from fastapi import APIRouter, Depends

from app.core.auth import CLINICAL_STAFF, require_roles
from app.core.models import StaffUser
from app.core.store import get_store
from app.modules.protocols import service
from app.modules.protocols.library import PROTOCOLS

router = APIRouter(prefix="/api/protocols", tags=["protocols"])


@router.get("")
def library(user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    return {"protocols": [p.model_dump() for p in PROTOCOLS]}


@router.get("/stats")
def stats(user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    return service.stats(get_store())
