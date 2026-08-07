"""/api/admin/scan-guard - settings + the blocked-source list.

Its own module rather than another 150 lines in `routers/admin/settings.py`,
which is already ~1500 lines. Same shape as `admin/imap.py` and `admin/backup.py`.
"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.ip_block import IpBlock
from ...models.user import User
from ...schemas.scan_guard import (
    CreateIpBlockRequest,
    IpBlockListResponse,
    IpBlockRow,
    ScanGuardSettingsResponse,
    UpdateScanGuardSettingsRequest,
)
from ...services import scan_guard as guard_svc
from ...utils.client_ip import is_blockable
from ...utils.timeutil import utc_now

router = APIRouter()


def _to_row(row: IpBlock) -> IpBlockRow:
    return IpBlockRow(
        id=row.id,
        subject=row.subject,
        network=row.network,
        is_network=row.is_network,
        reason=row.reason,
        source=row.source,
        hit_count=row.hit_count,
        strikes=row.strikes,
        last_path=row.last_path,
        created_at=row.created_at,
        expires_at=row.expires_at,
        released_at=row.released_at,
        note=row.note,
    )


def _settings_response(db: Session) -> ScanGuardSettingsResponse:
    data = guard_svc.get_settings(db)
    now = utc_now()
    live = db.query(IpBlock).filter(
        IpBlock.released_at.is_(None), IpBlock.expires_at > now
    )
    data["active_ip_blocks"] = live.filter(IpBlock.is_network.is_(False)).count()
    data["active_network_blocks"] = live.filter(IpBlock.is_network.is_(True)).count()
    data.pop("_extra_prefixes", None)
    data.pop("_ignore_prefixes", None)
    return ScanGuardSettingsResponse(**data)


@router.get("/scan-guard", response_model=ScanGuardSettingsResponse)
def get_scan_guard(
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)
) -> ScanGuardSettingsResponse:
    return _settings_response(db)


@router.put("/scan-guard", response_model=ScanGuardSettingsResponse)
def update_scan_guard(
    payload: UpdateScanGuardSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ScanGuardSettingsResponse:
    guard_svc.update_settings(
        db, values=payload.model_dump(), actor=admin, request=request
    )
    db.commit()
    return _settings_response(db)


@router.get("/scan-guard/blocks", response_model=IpBlockListResponse)
def list_blocks(
    active: bool = Query(True),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> IpBlockListResponse:
    rows, total = guard_svc.list_blocks(
        db, active_only=active, page=page, page_size=page_size
    )
    return IpBlockListResponse(
        items=[_to_row(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.post(
    "/scan-guard/blocks",
    response_model=IpBlockRow,
    status_code=status.HTTP_201_CREATED,
)
def create_block(
    payload: CreateIpBlockRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> IpBlockRow:
    subject = payload.subject.strip()
    try:
        net = ipaddress.ip_network(subject, strict=False)
    except ValueError:
        raise AppError(
            400, "SUBJECT_INVALID", "Enter an IP address or a CIDR network."
        ) from None
    is_network = net.num_addresses > 1
    # The same refusal the automatic path applies. A manual block must not be a
    # way around it: banning loopback or the compose bridge would take out the
    # frontend, tusd and the healthcheck.
    if not is_network and not is_blockable(str(net.network_address)):
        raise AppError(
            400,
            "SUBJECT_NOT_BLOCKABLE",
            "Only globally routable addresses can be blocked.",
        )
    if is_network and not net.is_global:
        raise AppError(
            400,
            "SUBJECT_NOT_BLOCKABLE",
            "Only globally routable networks can be blocked.",
        )

    row = guard_svc.apply_block(
        db,
        subject=str(net) if is_network else str(net.network_address),
        reason="manual",
        source="manual",
        is_network=is_network,
        minutes=payload.minutes,
        note=payload.note,
        actor_id=admin.id,
    )
    db.commit()
    guard_svc._reset_cache()
    return _to_row(row)


@router.delete("/scan-guard/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def release_block(
    block_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    row = guard_svc.release(db, block_id=block_id, actor_id=admin.id)
    if row is None:
        raise AppError(404, "IP_BLOCK_NOT_FOUND", "No such active block.")
    db.commit()
    # So the admin's own release is effective immediately rather than after the
    # cache TTL - which matters most when they are unblocking themselves.
    guard_svc._reset_cache()
