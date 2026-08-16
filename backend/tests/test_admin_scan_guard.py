"""Admin surface for the scan guard."""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"
SCANNER = "45.148.10.67"


@pytest.fixture(autouse=True)
def _isolate_scan_guard_redis(monkeypatch):
    """Keep every test in this file off the deployment's Redis.

    `docker compose run` joins the compose network, so a bare `get_redis()` in
    code under test reaches the LIVE instance - and `release()` now calls
    `clear_counters`, which issues DELETE/ZREM against fixed key names derived
    from the subject. The subjects here are real observed scanner addresses, so
    an unstubbed run would delete live counters for them. Same class of hazard
    as commit 24561bf (tests writing into production file storage).

    Tests that need to observe Redis monkeypatch `get_redis` themselves; a
    later patch in the test body wins over this one.
    """

    class _Inert:
        def __getattr__(self, _name):
            def _noop(*_a, **_kw):
                return None
            return _noop

        def pipeline(self):
            return self

        def execute(self):
            return [0]

    monkeypatch.setattr("app.redis_client.get_redis", lambda: _Inert())

def _payload(**over):
    body = {
        "enabled": True,
        "signal_probe_path": True,
        "signal_api_404": False,
        "signal_auth_failure": False,
        "escalation": True,
        "network_escalation": False,
        # `digest` was removed - it had no backend consumer for a whole release,
        # an inert control on the page that refuses inert configurations.
        "notify_mode": "off",
        "allowlist": "",
        "extra_paths": "",
        "ignore_paths": "",
        "threshold": 3,
        "window_sec": 3600,
        "block_minutes": 60,
        "max_block_minutes": 1440,
        "min_distinct_paths": 15,
        "network_threshold": 3,
        "network_lookback_hours": 168,
        "max_new_blocks_per_min": 60,
        "network_prefix_v6": 64,
    }
    body.update(over)
    return body


async def _admin_headers(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_a_virgin_instance_reports_the_guard_off(make_user, client, login_as):
    """The upgrade must be behaviour-neutral: nothing is blocked until an admin
    opts in."""
    headers = await _admin_headers(make_user, login_as)
    body = (await client.get("/api/admin/scan-guard", headers=headers)).json()
    assert body["enabled"] is False
    assert body["network_escalation"] is False
    assert body["signal_auth_failure"] is False
    assert body["active_ip_blocks"] == 0


@pytest.mark.asyncio
async def test_settings_round_trip(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard", json=_payload(threshold=9), headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["threshold"] == 9
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_a_stored_digest_reads_as_off(db, make_user, client, login_as):
    """An instance that stored `digest` before v2.11.0 removed the mode must not
    fall through to a mode that no longer exists. It reads as `off` - which is
    silent, so the coercion is also why an upgraded instance stops notifying
    without saying so, and the admin sees `off` on the page rather than a value
    the backend cannot honour."""
    from app.services import scan_guard as sg
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.SCAN_GUARD_NOTIFY_MODE, value="digest", actor=None
    )
    db.commit()

    assert sg.get_settings(db)["notify_mode"] == "off"

    headers = await _admin_headers(make_user, login_as)
    body = (await client.get("/api/admin/scan-guard", headers=headers)).json()
    assert body["notify_mode"] == "off"


@pytest.mark.asyncio
async def test_digest_is_refused_on_write(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard", json=_payload(notify_mode="digest"), headers=headers
    )
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_enabling_with_no_signals_is_refused(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.put(
        "/api/admin/scan-guard",
        json=_payload(signal_probe_path=False),
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "SCAN_GUARD_NO_SIGNALS"


@pytest.mark.asyncio
async def test_a_bad_allowlist_entry_is_refused(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.post(
        "/api/admin/scan-guard/allowlist", json={"entry": "junk"}, headers=headers
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "ALLOWLIST_INVALID"


@pytest.mark.asyncio
async def test_the_settings_put_no_longer_writes_the_allowlist(
    make_user, client, login_as
):
    """The settings form is no longer a writer of the allowlist.

    It used to carry the whole CSV, so an admin with the page open who
    allowlisted an address elsewhere erased it on save. `APIBaseModel` allows
    extra fields, so a stale SPA still sends the key - it must be ignored, not
    422'd, and above all it must not wipe."""
    headers = await _admin_headers(make_user, login_as)
    add = await client.post(
        "/api/admin/scan-guard/allowlist",
        json={"entry": "203.0.113.7"}, headers=headers,
    )
    assert add.status_code == 200, add.text
    assert add.json()["entries"] == ["203.0.113.7/32"]

    # A NON-EMPTY stale value, deliberately: sending "" would also pass against
    # an implementation that merely skips falsy input, while a real stale
    # snapshot from an open settings tab still overwrote the list.
    saved = await client.put(
        "/api/admin/scan-guard",
        json=_payload(allowlist="10.0.0.0/8,192.168.0.0/16"), headers=headers,
    )
    assert saved.status_code == 200, saved.text

    still = await client.get("/api/admin/scan-guard/allowlist", headers=headers)
    assert still.json()["entries"] == ["203.0.113.7/32"]


@pytest.mark.asyncio
async def test_allowlist_add_is_idempotent_and_removable(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    for _ in range(2):
        resp = await client.post(
            "/api/admin/scan-guard/allowlist",
            json={"entry": "198.51.100.0/24"}, headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["entries"] == ["198.51.100.0/24"]

    # Removal accepts the canonical form the API reported back.
    gone = await client.delete(
        "/api/admin/scan-guard/allowlist",
        params={"entry": "198.51.100.0/24"}, headers=headers,
    )
    assert gone.status_code == 200, gone.text
    assert gone.json()["entries"] == []

    missing = await client.delete(
        "/api/admin/scan-guard/allowlist",
        params={"entry": "198.51.100.0/24"}, headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "ALLOWLIST_ENTRY_NOT_FOUND"


@pytest.mark.asyncio
async def test_unblock_and_allow_is_one_decision(make_user, client, login_as, db):
    """Release + allowlist in one request. Releasing without allowlisting would
    just hand the source back to the guard to re-block."""
    from app.models.ip_block import IpBlock

    headers = await _admin_headers(make_user, login_as)
    created = await client.post(
        "/api/admin/scan-guard/blocks",
        json={"subject": "45.148.10.67", "minutes": 60}, headers=headers,
    )
    assert created.status_code == 201, created.text
    block_id = created.json()["id"]

    resp = await client.post(
        f"/api/admin/scan-guard/blocks/{block_id}/allow", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowlist"] == ["45.148.10.67/32"]
    assert resp.json()["block"]["released_at"] is not None

    row = db.query(IpBlock).filter(IpBlock.id == block_id).one()
    db.refresh(row)
    assert row.released_at is not None
    assert row.released_by_id is not None


@pytest.mark.asyncio
async def test_a_manual_block_refuses_a_non_global_subject(
    make_user, client, login_as
):
    """The manual endpoint must not be a way around the invariant - blocking the
    compose bridge or loopback would take out the frontend, tusd and the
    healthcheck."""
    headers = await _admin_headers(make_user, login_as)
    for subject in ("127.0.0.1", "10.0.0.5", "192.168.0.0/16"):
        resp = await client.post(
            "/api/admin/scan-guard/blocks",
            json={"subject": subject, "minutes": 60},
            headers=headers,
        )
        assert resp.status_code == 400, subject
        assert resp.json()["code"] == "SUBJECT_NOT_BLOCKABLE"


@pytest.mark.asyncio
async def test_manual_block_then_release(make_user, db, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    created = await client.post(
        "/api/admin/scan-guard/blocks",
        json={"subject": SCANNER, "minutes": 30, "note": "seen in the log"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    block_id = created.json()["id"]
    assert created.json()["source"] == "manual"

    listed = await client.get("/api/admin/scan-guard/blocks", headers=headers)
    assert [r["id"] for r in listed.json()["items"]] == [block_id]

    gone = await client.delete(
        f"/api/admin/scan-guard/blocks/{block_id}", headers=headers
    )
    assert gone.status_code == 204
    still = await client.get("/api/admin/scan-guard/blocks", headers=headers)
    assert still.json()["items"] == []
    # Released, not deleted: the row remains as history.
    with_expired = await client.get(
        "/api/admin/scan-guard/blocks?active=false", headers=headers
    )
    assert [r["id"] for r in with_expired.json()["items"]] == [block_id]


@pytest.mark.asyncio
async def test_releasing_an_unknown_block_404s(make_user, client, login_as):
    headers = await _admin_headers(make_user, login_as)
    resp = await client.delete("/api/admin/scan-guard/blocks/424242", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "IP_BLOCK_NOT_FOUND"


@pytest.mark.asyncio
async def test_a_non_admin_cannot_reach_any_of_it(make_user, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/admin/scan-guard", headers=headers)).status_code == 403
    assert (
        await client.get("/api/admin/scan-guard/blocks", headers=headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_the_settings_audit_records_keys_not_values(
    make_user, db, client, login_as
):
    from app.models.audit_log import AuditEventType, AuditLog

    headers = await _admin_headers(make_user, login_as)
    await client.put(
        "/api/admin/scan-guard", json=_payload(allowlist=f"{SCANNER}/32"),
        headers=headers,
    )
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.scan_guard_settings_changed.value)
        .one()
    )
    assert "keys" in row.extra
    assert SCANNER not in str(row.extra), "an audit row must not carry the values"


# --- List filters ----------------------------------------------------------


def _seed_blocks(db):
    """One of each shape, so a filter that does nothing shows up as a filter
    that returns everything."""
    from app.services import scan_guard as sg

    sg.apply_block(db, subject="45.148.10.67", reason="probe_path", snap=sg._defaults())
    sg.apply_block(db, subject="203.0.113.9", reason="auth_failure", snap=sg._defaults())
    sg.apply_block(
        db, subject="198.51.100.0/24", reason="network", is_network=True,
        snap=sg._defaults(),
    )
    sg.apply_block(
        db, subject="192.0.2.5", reason="manual", source="manual",
        minutes=60, note="by hand", snap=sg._defaults(),
    )
    db.commit()


@pytest.mark.asyncio
async def test_block_filters_narrow_the_list(make_user, client, login_as, db):
    headers = await _admin_headers(make_user, login_as)
    _seed_blocks(db)

    async def _subjects(**params):
        resp = await client.get(
            "/api/admin/scan-guard/blocks", params=params, headers=headers
        )
        assert resp.status_code == 200, resp.text
        return {r["subject"] for r in resp.json()["items"]}

    assert len(await _subjects()) == 4
    assert await _subjects(reason="auth_failure") == {"203.0.113.9"}
    assert await _subjects(source="manual") == {"192.0.2.5"}
    assert await _subjects(is_network=True) == {"198.51.100.0/24"}
    assert await _subjects(q="203.0.113") == {"203.0.113.9"}
    # An address inside a blocked RANGE finds the range, which a substring
    # search never would - this is what the locked-out admin needs.
    assert await _subjects(covers="198.51.100.77") == {"198.51.100.0/24"}
    assert await _subjects(covers="45.148.10.67") == {"45.148.10.67"}


@pytest.mark.asyncio
async def test_a_like_wildcard_in_the_search_is_not_a_wildcard(
    make_user, client, login_as, db
):
    """`_` is a single-character wildcard in SQL LIKE. An admin typing it must
    get a literal match, not everything."""
    headers = await _admin_headers(make_user, login_as)
    _seed_blocks(db)
    resp = await client.get(
        "/api/admin/scan-guard/blocks", params={"q": "_"}, headers=headers
    )
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_status_filter_separates_live_released_and_expired(
    make_user, client, login_as, db
):
    from datetime import timedelta

    from app.models.ip_block import IpBlock
    from app.services import scan_guard as sg
    from app.utils.timeutil import utc_now

    headers = await _admin_headers(make_user, login_as)
    live = sg.apply_block(
        db, subject="45.148.10.67", reason="probe_path", snap=sg._defaults()
    )
    released = sg.apply_block(
        db, subject="203.0.113.9", reason="probe_path", snap=sg._defaults()
    )
    db.commit()
    sg.release(db, block_id=released.id, actor_id=None)
    expired = IpBlock(
        subject="192.0.2.5", network="192.0.2.0/24", is_network=False,
        reason="probe_path", source="auto", hit_count=1, strikes=1,
        created_at=utc_now() - timedelta(days=2),
        expires_at=utc_now() - timedelta(days=1),
    )
    db.add(expired)
    db.commit()

    async def _subjects(status):
        resp = await client.get(
            "/api/admin/scan-guard/blocks", params={"status": status}, headers=headers
        )
        return {r["subject"] for r in resp.json()["items"]}

    assert await _subjects("active") == {live.subject}
    assert await _subjects("released") == {"203.0.113.9"}
    assert await _subjects("expired") == {"192.0.2.5"}
    assert len(await _subjects("all")) == 3

    bad = await client.get(
        "/api/admin/scan-guard/blocks", params={"status": "nonsense"}, headers=headers
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "BLOCK_STATUS_INVALID"


@pytest.mark.asyncio
async def test_the_deprecated_active_flag_still_works(make_user, client, login_as, db):
    """Kept one release so the shipped SPA and any scripted caller do not break
    the moment the backend updates.

    The two calls must return DIFFERENT sets, or this proves nothing: seed a
    released row so `active=false` has something extra to find."""
    from app.services import scan_guard as sg

    headers = await _admin_headers(make_user, login_as)
    _seed_blocks(db)
    dead = sg.apply_block(
        db, subject="45.148.11.9", reason="probe_path", snap=sg._defaults()
    )
    db.commit()
    sg.release(db, block_id=dead.id, actor_id=None)
    db.commit()

    live = await client.get(
        "/api/admin/scan-guard/blocks", params={"active": "true"}, headers=headers
    )
    assert live.json()["total"] == 4
    every = await client.get(
        "/api/admin/scan-guard/blocks", params={"active": "false"}, headers=headers
    )
    assert every.json()["total"] == 5


# --- Manual blocking -------------------------------------------------------


@pytest.mark.asyncio
async def test_release_all_releases_each_row_with_its_own_trail(
    make_user, client, login_as, db
):
    from app.models.audit_log import AuditLog

    headers = await _admin_headers(make_user, login_as)
    _seed_blocks(db)

    resp = await client.post(
        "/api/admin/scan-guard/blocks/release-all", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["released"] == 4

    # A bulk UPDATE would have lifted four blocks and recorded none of them.
    rows = (
        db.query(AuditLog).filter(AuditLog.event_type == "ip_block_released").all()
    )
    assert len(rows) == 4
    assert all(r.actor_user_id is not None for r in rows)


# --- Watchlist -------------------------------------------------------------


class _WatchRedis:
    """Minimal Redis for the watch structures. Fixed key names mean a test that
    reached the real client would write into the deployment's Redis."""

    def __init__(self):
        self.z = {}
        self.h = {}
        self.calls = 0

    def _pipe(self):
        return self

    pipeline = _pipe

    def zincrby(self, key, amt, member):
        self.calls += 1
        self.z.setdefault(key, {})[member] = self.z.setdefault(key, {}).get(member, 0) + amt

    def zadd(self, key, mapping):
        self.calls += 1
        self.z.setdefault(key, {}).update(mapping)

    def hset(self, key, field, value):
        self.calls += 1
        self.h.setdefault(key, {})[field] = value

    def expire(self, key, seconds, nx=False):
        self.calls += 1

    def zcard(self, key):
        self.calls += 1
        return len(self.z.get(key, {}))

    def execute(self):
        return [len(self.z.get("fh:scanguard:watch:count", {}))]

    def zrevrange(self, key, start, stop, withscores=False):
        self.calls += 1
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: -kv[1])
        sliced = items[start:stop + 1]
        return sliced if withscores else [m for m, _ in sliced]

    def zrangebyscore(self, key, lo, hi):
        self.calls += 1
        cutoff = float(str(hi).lstrip("("))
        return [m for m, s in self.z.get(key, {}).items() if s < cutoff]

    def hmget(self, key, fields):
        self.calls += 1
        store = self.h.get(key, {})
        return [store.get(f) for f in fields]

    def zmscore(self, key, members):
        self.calls += 1
        store = self.z.get(key, {})
        return [store.get(m) for m in members]

    def zrem(self, key, *members):
        self.calls += 1
        for m in members:
            self.z.get(key, {}).pop(m, None)

    def hdel(self, key, *fields):
        self.calls += 1
        for f in fields:
            self.h.get(key, {}).pop(f, None)


@pytest.mark.asyncio
async def test_the_watchlist_reports_sources_before_they_are_blocked(
    make_user, client, login_as, db, monkeypatch
):
    import json

    from app.services import scan_guard as sg
    from app.utils.timeutil import utc_now

    headers = await _admin_headers(make_user, login_as)
    fake = _WatchRedis()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: fake)
    fake.z["fh:scanguard:watch:count"] = {"45.148.10.67": 7}
    fake.z["fh:scanguard:watch:seen"] = {"45.148.10.67": utc_now().timestamp()}
    fake.h["fh:scanguard:watch:meta"] = {
        "45.148.10.67": json.dumps({"sig": "probe_path", "p": "/.env"})
    }

    resp = await client.get("/api/admin/scan-guard/watchlist", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["enabled"] is True
    assert body["items"][0]["ip"] == "45.148.10.67"
    assert body["items"][0]["offences"] == 7
    assert body["items"][0]["last_signal"] == "probe_path"
    # Both thresholds, because an auth row must not be rendered against the
    # scan threshold.
    assert body["threshold"] == sg._defaults()["threshold"]
    assert body["auth_threshold"] == sg._defaults()["auth_threshold"]


@pytest.mark.asyncio
async def test_a_stale_watch_entry_is_pruned_rather_than_shown(
    make_user, client, login_as, db, monkeypatch
):
    """Per-member pruning is what actually bounds how long a plaintext address
    is retained. EXPIRE is whole-key and any other source's write slides it, so
    on a busy instance the keys effectively never expire on their own."""
    import json

    from app.utils.timeutil import utc_now

    headers = await _admin_headers(make_user, login_as)
    fake = _WatchRedis()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: fake)
    stale = utc_now().timestamp() - 999_999
    fake.z["fh:scanguard:watch:count"] = {"45.148.10.67": 3}
    fake.z["fh:scanguard:watch:seen"] = {"45.148.10.67": stale}
    fake.h["fh:scanguard:watch:meta"] = {
        "45.148.10.67": json.dumps({"sig": "probe_path", "p": "/.env"})
    }

    resp = await client.get("/api/admin/scan-guard/watchlist", headers=headers)
    assert resp.json()["items"] == []
    # And it is gone from the store, not merely filtered out of the response.
    assert fake.z["fh:scanguard:watch:count"] == {}
    assert fake.h["fh:scanguard:watch:meta"] == {}


@pytest.mark.asyncio
async def test_redis_down_degrades_the_watchlist_instead_of_the_page(
    make_user, client, login_as, monkeypatch
):
    headers = await _admin_headers(make_user, login_as)

    def _boom():
        raise RuntimeError("redis is down")

    monkeypatch.setattr("app.redis_client.get_redis", _boom)
    resp = await client.get("/api/admin/scan-guard/watchlist", headers=headers)
    assert resp.status_code == 200, "a watchlist outage must not fail the request"
    assert resp.json()["available"] is False
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_turning_the_watchlist_off_touches_redis_at_all(
    make_user, client, login_as, db, monkeypatch
):
    """Off means no plaintext addresses reach Redis - not "written and hidden"."""
    from app.services import scan_guard as sg
    from app.services import settings as settings_svc

    headers = await _admin_headers(make_user, login_as)
    settings_svc.set_value(
        db, key=settings_svc.Keys.SCAN_GUARD_WATCHLIST, value="false", actor=None
    )
    db.commit()
    sg._reset_cache()

    fake = _WatchRedis()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: fake)
    resp = await client.get("/api/admin/scan-guard/watchlist", headers=headers)
    assert resp.json()["enabled"] is False
    assert resp.json()["items"] == []
    assert fake.calls == 0, "the watchlist was read from Redis while switched off"


@pytest.mark.asyncio
async def test_admin_block_and_release_record_where_they_came_from(
    make_user, client, login_as, db
):
    """An admin denying service to someone is exactly what `audit_log.ip` is for.

    Allowlist changes carried the originating address while blocks and releases
    carried NULL, because neither `apply_block` nor `release` ever took a
    request. Spotted on the live instance: `ip_allowlisted` had an address next
    to it and `ip_blocked` did not.
    """
    from app.models.audit_log import AuditLog

    headers = await _admin_headers(make_user, login_as)
    created = await client.post(
        "/api/admin/scan-guard/blocks",
        json={"subject": "45.148.10.67", "minutes": 60}, headers=headers,
    )
    assert created.status_code == 201, created.text
    resp = await client.delete(
        f"/api/admin/scan-guard/blocks/{created.json()['id']}", headers=headers
    )
    assert resp.status_code == 204, resp.text

    rows = {
        a.event_type: a
        for a in db.query(AuditLog)
        .filter(AuditLog.event_type.in_(["ip_blocked", "ip_block_released"]))
        .all()
    }
    for event in ("ip_blocked", "ip_block_released"):
        assert rows[event].ip, f"{event} recorded no originating address"


def test_the_automatic_path_records_no_origin(db):
    """The mirror, and it is deliberate: a blank origin means the guard decided,
    not a person. `note_offence` has no request to hand over."""
    from app.models.audit_log import AuditLog
    from app.services import scan_guard as sg

    sg.apply_block(db, subject="45.148.10.67", reason="probe_path", snap=sg._defaults())
    db.commit()
    row = db.query(AuditLog).filter(AuditLog.event_type == "ip_blocked").one()
    assert row.ip is None
    assert row.actor_user_id is None
