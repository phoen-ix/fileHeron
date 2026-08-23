"""Side effects the import's raw `AppSetting` writes used to skip.

`apply_backup` is the THIRD raw writer of `app_settings`, after the admin
settings PUT and `/admin/settings/advanced`. The advanced route was fixed by
REFUSING to write `scan_guard.*` at all (`_MANAGED_ELSEWHERE_GROUPS`), which is
not an option here - an import must restore whatever the backup holds. So the
side effects are replayed after the final commit instead.

Two of them leave PERSISTENT wrong state, which is why this matters more than
the cache reset does:

* `ip_blocks.network` is a denormalised cache of `network_of()` compared by
  string equality, and `config_backup` never exports, wipes or imports that
  table - it contains zero references to `IpBlock`. An import that changes the
  v6 prefix therefore leaves live network blocks stamped under the old one, and
  because `is_blocked` matches by CIDR CONTAINMENT the orphan keeps denying
  service while the admin page shows nothing to release.
* An enabled guard with no signals on is refused by `update_settings`
  (`SCAN_GUARD_NO_SIGNALS`) because it renders as "on" and can never fire. A
  backup can carry exactly that.
"""
from __future__ import annotations

from datetime import timedelta

from app.models.ip_block import IpBlock
from app.services import config_backup as cb
from app.services import scan_guard as sg
from app.services import settings as settings_svc
from app.utils.timeutil import utc_now
from tests.test_config_backup import _admin, _fresh_session

_K = settings_svc.Keys
_CATS = ["settings_branding"]


def _set(db, key, value, actor):
    settings_svc.set_value(db, key=key, value=value, actor=actor)


def _live_network_block(db) -> IpBlock:
    row = IpBlock(
        subject="2001:db8::/64",
        network="2001:db8::/64",
        is_network=True,
        reason="probe_path",
        source="auto",
        expires_at=utc_now() + timedelta(hours=1),
    )
    db.add(row)
    db.commit()
    return row


def _import_with(prefix: str, *, enabled="true", probe="true"):
    """Build a backup carrying `prefix`, then import it into an instance that
    has a live network block and (usually) a different prefix."""
    src = _fresh_session()
    src_actor = _admin(src)
    _set(src, _K.SCAN_GUARD_NETWORK_PREFIX_V6, prefix, src_actor)
    _set(src, _K.SCAN_GUARD_ENABLED, enabled, src_actor)
    _set(src, _K.SCAN_GUARD_SIGNAL_PROBE_PATH, probe, src_actor)
    _set(src, _K.SCAN_GUARD_SIGNAL_API_404, "false", src_actor)
    _set(src, _K.SCAN_GUARD_SIGNAL_AUTH_FAILURE, "false", src_actor)
    src.commit()
    raw = cb.build_backup(
        src, categories=_CATS, secret_mode="exclude", passphrase=None,
        include_env=False,
    )

    tgt = _fresh_session()
    tgt_actor = _admin(tgt)
    _set(tgt, _K.SCAN_GUARD_NETWORK_PREFIX_V6, "64", tgt_actor)
    tgt.commit()
    block = _live_network_block(tgt)

    summary = cb.apply_backup(
        tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=tgt_actor,
        request=None,
    )
    tgt.commit()
    return tgt, block.id, summary


def test_a_changed_prefix_releases_the_now_orphaned_network_block():
    db, block_id, summary = _import_with("56")
    row = db.query(IpBlock).filter(IpBlock.id == block_id).one()
    assert row.released_at is not None, (
        "the block is still enforcing under a prefix that no longer exists - "
        "and it is invisible to the admin page"
    )
    assert summary.counts.get("network_blocks_released") == 1


def test_the_release_is_audited_not_silently_stamped():
    """Through `release()`, so each freed block writes its own
    `ip_block_released` row. A hand-stamped `released_at` made a prefix change
    swallow every live network block with nothing recording it."""
    from app.models.audit_log import AuditEventType, AuditLog

    db, _block_id, _s = _import_with("56")
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.ip_block_released.value)
        .all()
    )
    assert rows, "the release left no audit trail"
    assert any(
        (r.extra or {}).get("via") == "config_import_v6_prefix_changed" for r in rows
    ), [r.extra for r in rows]


def test_the_operator_is_told():
    _db, _b, summary = _import_with("56")
    assert any("IPv6 prefix" in w for w in summary.warnings), summary.warnings


def test_an_unchanged_prefix_leaves_the_block_alone():
    """The control. Without it, every assertion above is satisfied by an import
    that releases every network block unconditionally."""
    db, block_id, summary = _import_with("64")
    row = db.query(IpBlock).filter(IpBlock.id == block_id).one()
    assert row.released_at is None, "an unrelated import released a live block"
    assert "network_blocks_released" not in summary.counts


def test_an_enabled_guard_with_no_signals_is_forced_off():
    db, _b, summary = _import_with("64", enabled="true", probe="false")
    assert settings_svc.get_bool(db, _K.SCAN_GUARD_ENABLED, False) is False, (
        "the guard renders as ON and can never fire - manufactured assurance"
    )
    assert any("no signals" in w for w in summary.warnings), summary.warnings


def test_a_guard_with_a_signal_is_left_enabled():
    """The control for the above: a valid enabled config must survive."""
    db, _b, _s = _import_with("64", enabled="true", probe="true")
    assert settings_svc.get_bool(db, _K.SCAN_GUARD_ENABLED, False) is True


def test_the_scan_guard_cache_is_reset():
    """The process cache holds the PRE-import snapshot for up to
    _CACHE_TTL_SEC; everything above reads through it."""
    sg._reset_cache()
    db, _b, _s = _import_with("56")
    assert sg._cache_expires == 0.0 or sg._snapshot is None, (
        "the import left the pre-import snapshot cached"
    )
    assert db is not None
