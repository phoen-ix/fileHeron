"""Scoped API tokens - per-token least-privilege enforcement.

A token's `scopes` is NULL (unrestricted, back-compat) or a confined set. These
tests prove a restricted token can do exactly what it's scoped for and is 403
INSUFFICIENT_SCOPE everywhere else, that unrestricted tokens + JWT sessions are
unaffected, and the two awkward enforcement points (inline public-link on
share-create; the bearer download path vs the exempt ?dt= path).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.middleware.errors import AppError
from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind
from app.models.user import UserRole
from app.services import api_token as api_token_svc
from app.services import share as share_svc


def _future() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def _future_iso() -> str:
    return _future().isoformat()


def _mint(db, owner, scopes):
    record, plaintext = api_token_svc.create_token(
        db, owner=owner, name="t", scopes=api_token_svc.normalize_scopes(scopes)
    )
    db.commit()
    return plaintext


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _share_payload(rec_id: int, *, public_link: dict | None = None) -> dict:
    body = {
        "kind": "outbound",
        "recipients": {"user_ids": [rec_id], "group_ids": []},
        "expires_at": _future_iso(),
        "subject": "s",
        "message": None,
    }
    if public_link is not None:
        body["public_link"] = public_link
    return body


# --------------------------------------------------------------------------- #
# A. A {shares:create, files:upload} token can do exactly those two things.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_scoped_token_can_create_and_upload(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    tok = _mint(db, admin, ["shares:create", "files:upload"])

    r = await client.post("/api/shares", json=_share_payload(rec.id), headers=_h(tok))
    assert r.status_code == 201, r.text
    share_id = r.json()["id"]

    up = await client.post(
        "/api/uploads/direct",
        data={"share_id": share_id},
        files={"file": ("h.txt", b"hello world", "text/plain")},
        headers=_h(tok),
    )
    assert up.status_code == 201, up.text


# --------------------------------------------------------------------------- #
# B. The same token is 403 INSUFFICIENT_SCOPE on everything else.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_scoped_token_denied_outside_scope(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        expires_at=_future(),
    )
    db.commit()
    sid = share.id
    tok = _mint(db, admin, ["shares:create", "files:upload"])

    # (method, url, expected required_scope) - the require_scope Depends fires
    # before the handler body, so a nonexistent id still 403s at the gate.
    cases = [
        ("get", "/api/shares", "shares:read"),
        ("get", f"/api/shares/{sid}", "shares:read"),
        ("get", "/api/shares/pending-approval", "shares:read"),
        ("delete", f"/api/shares/{sid}", "shares:manage"),
        ("post", f"/api/shares/{sid}/expire", "shares:manage"),
        ("get", "/api/files/nope/download-url", "files:download"),
        ("delete", "/api/files/nope", "files:delete"),
        ("get", "/api/users/search?q=x", "recipients:search"),
        ("get", "/api/groups/recipient-targets", "shares:read"),
    ]
    for method, url, scope in cases:
        r = await getattr(client, method)(url, headers=_h(tok))
        assert r.status_code == 403, f"{method} {url}: {r.status_code} {r.text}"
        body = r.json()
        assert body["code"] == "INSUFFICIENT_SCOPE", body
        assert body["details"]["required_scope"] == scope, (url, body)

    # Public-link create needs a valid body so a 422 can't mask the 403.
    r = await client.post(f"/api/shares/{sid}/public-link", json={}, headers=_h(tok))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INSUFFICIENT_SCOPE"
    assert r.json()["details"]["required_scope"] == "public_links:write"


# --------------------------------------------------------------------------- #
# C. Unrestricted (NULL scopes) token is unaffected - back-compat for every
#    pre-existing token.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unrestricted_token_unaffected(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    tok = _mint(db, admin, None)  # NULL scopes

    # A read it would be denied on if scoped:
    r = await client.get("/api/shares", headers=_h(tok))
    assert r.status_code == 200, r.text
    # A create:
    c = await client.post("/api/shares", json=_share_payload(rec.id), headers=_h(tok))
    assert c.status_code == 201, c.text
    # Search:
    s = await client.get("/api/users/search?q=r", headers=_h(tok))
    assert s.status_code == 200, s.text


# --------------------------------------------------------------------------- #
# D. JWT/session users never see scope enforcement.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_jwt_session_unaffected(make_user, db, client, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    # A scope-gated read with a plain session JWT must pass (auth_via=session).
    r = await client.get("/api/shares", headers=_h(token))
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# E. Inline public-link on share-create: shares:create WITHOUT public_links:write
#    is refused before any write.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_inline_public_link_refused_without_scope(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    tok = _mint(db, admin, ["shares:create"])  # no public_links:write

    r = await client.post(
        "/api/shares",
        json=_share_payload(rec.id, public_link={"notify_on_download": False}),
        headers=_h(tok),
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INSUFFICIENT_SCOPE"
    assert r.json()["details"]["required_scope"] == "public_links:write"
    # Atomicity: the refusal precedes the write - no share row was created.
    assert db.query(Share).filter(Share.created_by_id == admin.id).count() == 0


@pytest.mark.asyncio
async def test_inline_public_link_allowed_with_scope(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    tok = _mint(db, admin, ["shares:create", "public_links:write"])

    r = await client.post(
        "/api/shares",
        json=_share_payload(rec.id, public_link={"download_limit": 3, "notify_on_download": False}),
        headers=_h(tok),
    )
    assert r.status_code == 201, r.text
    assert r.json()["public_link"] is not None


# --------------------------------------------------------------------------- #
# F. Download: bearer path needs files:download; the ?dt= signed path is exempt.
# --------------------------------------------------------------------------- #


async def _seed_clean_file(client, db, admin, share_id) -> str:
    """Upload a file (unrestricted token) and mark it clean so it's downloadable."""
    seed_tok = _mint(db, admin, None)
    up = await client.post(
        "/api/uploads/direct",
        data={"share_id": share_id},
        files={"file": ("doc.txt", b"the bytes", "text/plain")},
        headers=_h(seed_tok),
    )
    assert up.status_code == 201, up.text
    fid = up.json()["file_id"]
    f = db.query(File).filter(File.id == fid).one()
    f.state = FileState.clean
    db.commit()
    return fid


@pytest.mark.asyncio
async def test_download_bearer_requires_scope(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db, created_by=admin, kind=ShareKind.outbound,
        recipient_user_ids=[rec.id], expires_at=_future(),
    )
    db.commit()
    fid = await _seed_clean_file(client, db, admin, share.id)

    # Token WITHOUT files:download -> 403 on the bearer download/preview/zip path.
    no_dl = _mint(db, admin, ["files:upload"])
    for url in (
        f"/api/files/{fid}/download",
        f"/api/files/{fid}/preview",
        f"/api/files/{share.id}/download-zip",
    ):
        r = await client.get(url, headers=_h(no_dl))
        assert r.status_code == 403, f"{url}: {r.status_code} {r.text}"
        assert r.json()["code"] == "INSUFFICIENT_SCOPE"
        assert r.json()["details"]["required_scope"] == "files:download"

    # Token WITH files:download -> 200.
    dl = _mint(db, admin, ["files:download"])
    ok = await client.get(f"/api/files/{fid}/download", headers=_h(dl))
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_download_dt_path_is_scope_exempt(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    rec = make_user(email="r@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db, created_by=admin, kind=ShareKind.outbound,
        recipient_user_ids=[rec.id], expires_at=_future(),
    )
    db.commit()
    fid = await _seed_clean_file(client, db, admin, share.id)

    # Mint a signed URL with an unrestricted token (the mint itself is gated by
    # files:download; unrestricted passes), then consume ?dt= with NO bearer.
    mint_tok = _mint(db, admin, None)
    minted = await client.get(f"/api/files/{fid}/download-url", headers=_h(mint_tok))
    assert minted.status_code == 200, minted.text
    url = next(v for v in minted.json().values() if isinstance(v, str) and "dt=" in v)
    path = url.split("/api/", 1)[1]
    consume = await client.get(f"/api/{path}")  # no Authorization header
    assert consume.status_code == 200, consume.text


# --------------------------------------------------------------------------- #
# G. normalize_scopes / token_scope_set validation.
# --------------------------------------------------------------------------- #


def test_normalize_none_is_unrestricted():
    assert api_token_svc.normalize_scopes(None) is None


def test_normalize_empty_rejected():
    with pytest.raises(AppError) as exc:
        api_token_svc.normalize_scopes([])
    assert exc.value.code == "INVALID_SCOPE"
    with pytest.raises(AppError):
        api_token_svc.normalize_scopes(["   "])


def test_normalize_unknown_rejected():
    with pytest.raises(AppError) as exc:
        api_token_svc.normalize_scopes(["files:upload", "bogus:scope"])
    assert exc.value.code == "INVALID_SCOPE"
    assert exc.value.details["unknown"] == ["bogus:scope"]


def test_normalize_dedupes_and_sorts():
    out = api_token_svc.normalize_scopes(["shares:read", "shares:read", "files:upload"])
    assert out == '["files:upload", "shares:read"]'


def test_token_scope_set_roundtrip(make_user, db):
    user = make_user(email="a@test.local")
    rec, _ = api_token_svc.create_token(
        db, owner=user, name="t",
        scopes=api_token_svc.normalize_scopes(["files:upload", "shares:create"]),
    )
    db.commit()
    assert api_token_svc.token_scope_set(rec) == {"files:upload", "shares:create"}
    assert rec.scopes_list == ["files:upload", "shares:create"]

    rec2, _ = api_token_svc.create_token(db, owner=user, name="full", scopes=None)
    db.commit()
    assert api_token_svc.token_scope_set(rec2) is None
    assert rec2.scopes_list is None


# --------------------------------------------------------------------------- #
# H. The two any-token routes stay reachable regardless of scope.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_any_token_routes_reachable(make_user, db, client):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    tok = _mint(db, admin, ["files:upload"])  # minimal, unrelated scope
    me = await client.get("/api/account/me", headers=_h(tok))
    assert me.status_code == 200, me.text
    cur = await client.get("/api/account/api-tokens/current", headers=_h(tok))
    assert cur.status_code == 200, cur.text
    assert cur.json()["scopes"] == ["files:upload"]


# --------------------------------------------------------------------------- #
# I + J. Create-via-API validates scopes + audit records them.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_via_api_validates_and_audits_scopes(make_user, db, client, login_as):
    make_user(email="a@test.local", role=UserRole.employee, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")

    # Unknown scope -> 400 at the router boundary.
    bad = await client.post(
        "/api/account/api-tokens",
        json={"name": "x", "scopes": ["bogus"], "password": "Pass12345678!"},
        headers=_h(token),
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "INVALID_SCOPE"

    # Valid scopes -> 201, response echoes sorted scopes, audit records them.
    ok = await client.post(
        "/api/account/api-tokens",
        json={"name": "ci", "scopes": ["shares:create", "files:upload"], "password": "Pass12345678!"},
        headers=_h(token),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["scopes"] == ["files:upload", "shares:create"]

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.api_token_created)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.extra["scopes"] == ["files:upload", "shares:create"]
