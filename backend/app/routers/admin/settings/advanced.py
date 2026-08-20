"""Registry-driven tunables (settings_registry.TUNABLES).

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....middleware.errors import AppError
from ....models.user import User
from ....schemas.advanced_settings import (
    AdvancedSettingItem,
    AdvancedSettingsResponse,
    UpdateAdvancedSettingsRequest,
)
from ....services import settings as settings_svc
from ....services import settings_registry

router = APIRouter()


# ---------------------------------------------------------------------------
# Generic registry-driven "Advanced settings" - one GET/PUT for every
# runtime-tunable knob in services/settings_registry.py. Only keys present
# in the registry are ever exposed or accepted (secrets/infra stay env-only).
#
# Registry groups whose keys have a DEDICATED page that does more than store a
# number, and which this generic surface therefore must not write.
#
# `scan_guard` is the case that forced the rule. Its own PUT refuses an
# enabled-but-signal-less configuration, releases every live network block when
# the IPv6 prefix changes, and resets the process cache. This route did none of
# that, so changing `scan_guard.network_prefix_v6` here left the live /64 rows
# no longer matching `network_of()` output: their accumulated escalation
# evidence stopped counting, a later escalation inserted an overlapping /56, and
# releasing the visible block left the orphaned /64 enforcing invisibly until it
# expired. `ip_blocks.network` is a denormalised string cache, so it can only be
# maintained by the writer that knows it exists.
_MANAGED_ELSEWHERE_GROUPS = frozenset({"scan_guard"})
# ---------------------------------------------------------------------------


@router.get("/settings/advanced", response_model=AdvancedSettingsResponse)
def get_advanced_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdvancedSettingsResponse:
    items: list[AdvancedSettingItem] = []
    for spec in settings_registry.TUNABLES:
        if spec.group in _MANAGED_ELSEWHERE_GROUPS:
            continue
        items.append(
            AdvancedSettingItem(
                key=spec.key,
                group=spec.group,
                kind=spec.kind,
                value=settings_registry.effective(db, spec.key),
                default=settings_registry.env_default(spec),
                is_overridden=settings_svc.get(db, spec.key) is not None,
                min=spec.min,
                max=spec.max,
            )
        )
    return AdvancedSettingsResponse(items=items)


@router.put("/settings/advanced", response_model=AdvancedSettingsResponse)
def update_advanced_settings(
    payload: UpdateAdvancedSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdvancedSettingsResponse:
    """Set or reset registry knobs. `null` resets a key to its env default.
    Unknown keys and out-of-bounds/typed-wrong values are rejected (400)
    before any write, so the PUT is all-or-nothing."""
    # Validate everything first (atomic - reject the whole PUT on any error).
    to_set: dict[str, str | None] = {}
    for key, value in payload.updates.items():
        spec = settings_registry.BY_KEY.get(key)
        if spec is None:
            raise AppError(400, "UNKNOWN_SETTING", f"Unknown setting: {key}")
        if spec.group in _MANAGED_ELSEWHERE_GROUPS:
            raise AppError(
                400,
                "SETTING_MANAGED_ELSEWHERE",
                f"{key} is managed on its own settings page, which applies the "
                "side effects this generic route cannot.",
            )
        if value is None:
            to_set[key] = None  # reset to env default
            continue
        try:
            to_set[key] = settings_registry.coerce_for_store(spec, value)
        except ValueError as e:
            raise AppError(400, "INVALID_SETTING", str(e)) from None

    if not to_set:
        return get_advanced_settings(db=db, _admin=admin)

    # Capture the pre-write refresh-TTL so we can detect a *shortening* and
    # apply it to existing sessions (clamp down; revoke only ones already
    # expired under the new value).
    refresh_ttl_old = settings_registry.effective(
        db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
    )

    for key, stored in to_set.items():
        settings_svc.set_value(db, key=key, value=stored, actor=admin, request=request)
    settings_svc.audit_settings_change(
        db, actor=admin, changed_keys=to_set.keys(), request=request
    )

    if settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS in to_set:
        refresh_ttl_new = settings_registry.effective(
            db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
        )
        if refresh_ttl_new < refresh_ttl_old:
            from ....services import jwt_session
            jwt_session.reclamp_refresh_expiry(
                db, new_days=refresh_ttl_new, actor=admin, request=request
            )

    db.commit()
    return get_advanced_settings(db=db, _admin=admin)
