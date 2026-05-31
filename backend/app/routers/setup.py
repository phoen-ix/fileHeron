"""/api/setup — first-admin bootstrap wizard.

Anonymous-accessible until the first admin exists. After that,
GET returns required=false and POST returns 409. SPA renders the
wizard view when required=true and 404s the route when false.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..schemas.setup import (
    CompleteSetupRequest,
    CompleteSetupResponse,
    SetupStatusResponse,
)
from ..services import setup as setup_svc

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatusResponse)
def setup_status(db: Session = Depends(get_db)) -> SetupStatusResponse:
    """Anonymous. SPA hits this on app bootstrap to decide whether to
    redirect to /setup."""
    return SetupStatusResponse(required=not setup_svc.is_setup_complete(db))


@router.post("/admin", response_model=CompleteSetupResponse)
async def complete_setup(
    payload: CompleteSetupRequest,
    db: Session = Depends(get_db),
) -> CompleteSetupResponse:
    """Anonymous one-shot. Creates the first admin; subsequent calls
    get 409 SETUP_ALREADY_COMPLETE. Caller is expected to follow up
    with a normal POST /api/auth/login to obtain a session."""
    user = await setup_svc.complete_setup(
        db,
        email=str(payload.email),
        password=payload.password,
        display_name=payload.display_name,
    )
    db.commit()
    return CompleteSetupResponse(user_id=user.id, email=user.email)
