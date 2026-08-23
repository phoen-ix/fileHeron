"""/api/setup - first-admin bootstrap wizard.

Anonymous-accessible until the first admin exists. After that,
GET returns required=false and POST returns 409. SPA renders the
wizard view when required=true and 404s the route when false.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..middleware.errors import AppError
from ..schemas.setup import (
    CompleteSetupRequest,
    CompleteSetupResponse,
    SetupStatusResponse,
)
from ..services import rate_limit
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
    request: Request,
    db: Session = Depends(get_db),
) -> CompleteSetupResponse:
    """Anonymous one-shot. Creates the first admin; subsequent calls
    get 409 SETUP_ALREADY_COMPLETE. Caller is expected to follow up
    with a normal POST /api/auth/login to obtain a session.

    Rate-limited even though `is_setup_complete` short-circuits it on any
    configured instance: before the first admin exists this is an anonymous
    route that runs an Argon2id hash (64 MiB by default) and an outbound HIBP
    lookup, and it was the only anonymous POST in the app with no limiter at
    all - it is not in `test_gate_wiring_coverage`'s `_ANON_GATED` list because
    there was nothing to assert. The window is small and real: an instance is
    reachable from the moment compose comes up, and the operator has not
    finished the wizard yet."""
    ip = request.client.host if request.client else "unknown"
    if not rate_limit.check_ip_allowed("setup_admin", ip, limit=10, window_sec=900):
        raise AppError(429, "RATE_LIMITED", "Too many requests; slow down.")
    user = await setup_svc.complete_setup(
        db,
        email=str(payload.email),
        password=payload.password,
        display_name=payload.display_name,
    )
    db.commit()
    return CompleteSetupResponse(user_id=user.id, email=user.email)
