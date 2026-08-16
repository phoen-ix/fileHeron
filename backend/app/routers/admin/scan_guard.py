"""/api/admin/scan-guard - settings + the blocked-source list.

Its own module rather than another 150 lines in `routers/admin/settings.py`,
which is already ~1500 lines. Same shape as `admin/imap.py` and `admin/backup.py`.
"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.ip_block import IpBlock
from ...models.user import User
from ...schemas.scan_guard import (
    AllowBlockResponse,
    AllowlistAddRequest,
    AllowlistResponse,
    CreateIpBlockRequest,
    IpBlockListResponse,
    IpBlockRow,
    ReleaseAllResponse,
    ScanGuardSettingsResponse,
    UpdateScanGuardSettingsRequest,
    WatchlistResponse,
)
from ...services import scan_guard as guard_svc
from ...utils.client_ip import get_client_ip, is_blockable, normalize_ip
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
        released_by_id=row.released_by_id,
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
    status_filter: str | None = Query(None, alias="status"),
    reason: str | None = Query(None, max_length=32),
    source: str | None = Query(None, pattern="^(auto|manual)$"),
    is_network: bool | None = Query(None),
    q: str | None = Query(None, max_length=64),
    covers: str | None = Query(None, max_length=64),
    # Superseded by `status`, kept one release so the shipped SPA and any
    # scripted caller keep working: active=true meant "live only", false meant
    # "everything". `status` wins when both are sent.
    active: bool | None = Query(None),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> IpBlockListResponse:
    if status_filter is None:
        status_filter = "active" if active in (None, True) else "all"
    if status_filter not in guard_svc.BLOCK_STATUSES:
        raise AppError(
            400,
            "BLOCK_STATUS_INVALID",
            f"status must be one of {', '.join(guard_svc.BLOCK_STATUSES)}.",
        )
    if covers:
        try:
            ipaddress.ip_address(covers.strip())
        except ValueError:
            raise AppError(
                400, "SUBJECT_INVALID", "`covers` must be a single IP address."
            ) from None

    rows, total = guard_svc.list_blocks(
        db,
        status=status_filter,
        reason=reason,
        source=source,
        is_network=is_network,
        q=q,
        covers=covers,
        page=page,
        page_size=page_size,
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
    # Normalised first, so a mapped-IPv6 subject an admin copied out of an old
    # log entry becomes the form every request IP is compared in. Stored raw, it
    # would list as an active block and match nothing.
    subject = normalize_ip(payload.subject.strip()) or payload.subject.strip()
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
    # Refuse a block that would lock the admin out of the page they are on.
    # There is no self-service recovery: the guard refuses ahead of routing, so
    # a blocked admin cannot reach the release endpoint, and the only way back
    # is `scripts/unblock_ip.py` on the host. That asymmetry is why this is a
    # refusal and not a confirmation - an admin who genuinely means it can block
    # the range from a different network.
    caller = get_client_ip(request)
    if caller:
        try:
            if ipaddress.ip_address(caller) in net:
                raise AppError(
                    400,
                    "SUBJECT_COVERS_SELF",
                    "That block would include the address you are connecting "
                    "from, and a blocked admin cannot reach this page to undo it.",
                )
        except ValueError:
            # Not an address (Starlette's TestClient sends "testclient", and a
            # scope can carry no client at all). Nothing to compare; the check
            # is a safety net, not a gate, so an unparseable peer skips it
            # rather than 500ing the endpoint.
            pass

    row = guard_svc.apply_block(
        db,
        subject=str(net) if is_network else str(net.network_address),
        reason="manual",
        source="manual",
        is_network=is_network,
        minutes=payload.minutes,
        note=payload.note,
        actor_id=admin.id,
        request=request,
    )
    db.commit()
    guard_svc._reset_cache()
    return _to_row(row)


@router.delete("/scan-guard/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def release_block(
    block_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    row = guard_svc.release(
        db, block_id=block_id, actor_id=admin.id, request=request
    )
    if row is None:
        raise AppError(404, "IP_BLOCK_NOT_FOUND", "No such active block.")
    db.commit()
    # So the admin's own release is effective immediately rather than after the
    # cache TTL - which matters most when they are unblocking themselves.
    guard_svc._reset_cache()


@router.post("/scan-guard/blocks/release-all", response_model=ReleaseAllResponse)
def release_all_blocks(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ReleaseAllResponse:
    released = guard_svc.release_all(db, actor_id=admin.id, request=request)
    db.commit()
    guard_svc._reset_cache()
    return ReleaseAllResponse(released=released)


@router.post("/scan-guard/blocks/{block_id}/allow", response_model=AllowBlockResponse)
def allow_block(
    block_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AllowBlockResponse:
    """Release a block and allowlist its subject in one decision.

    One transaction on purpose: releasing without allowlisting leaves a source
    that the guard will simply block again, and the admin would have to notice
    the second half failed.
    """
    row, entries = guard_svc.release_and_allow(
        db, block_id=block_id, actor=admin, request=request
    )
    db.commit()
    guard_svc._reset_cache()
    return AllowBlockResponse(block=_to_row(row), allowlist=entries["entries"])


@router.get("/scan-guard/allowlist", response_model=AllowlistResponse)
def get_allowlist(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AllowlistResponse:
    return AllowlistResponse(**guard_svc.allowlist_entries(db))


@router.post("/scan-guard/allowlist", response_model=AllowlistResponse)
def add_allowlist_entry(
    payload: AllowlistAddRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AllowlistResponse:
    try:
        guard_svc.allowlist_add(
            db, entry=payload.entry, actor=admin, request=request
        )
        db.commit()
    except IntegrityError:
        # Two admins added the first-ever entry at the same moment; there was no
        # row yet, so there was nothing to lock and the unique key on
        # `app_settings.key` decided it. One retry succeeds against the row the
        # winner created.
        db.rollback()
        raise AppError(
            409, "CONFLICT_RETRY", "The allowlist changed at the same time. Retry."
        ) from None
    guard_svc._reset_cache()
    return AllowlistResponse(**guard_svc.allowlist_entries(db))


@router.delete("/scan-guard/allowlist", response_model=AllowlistResponse)
def remove_allowlist_entry(
    request: Request,
    # A query parameter, not a path segment: a CIDR contains `/`, and a path
    # segment would need it percent-encoded, which proxies routinely normalise
    # back into a segment separator.
    entry: str = Query(..., min_length=3, max_length=64),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AllowlistResponse:
    guard_svc.allowlist_remove(db, entry=entry, actor=admin, request=request)
    db.commit()
    guard_svc._reset_cache()
    return AllowlistResponse(**guard_svc.allowlist_entries(db))


@router.get("/scan-guard/watchlist", response_model=WatchlistResponse)
def get_watchlist(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> WatchlistResponse:
    return WatchlistResponse(**guard_svc.watchlist(db, limit=limit))
