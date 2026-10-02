"""Every outbound fetch from a host this instance does not control is bounded.

A plain `client.get(url).json()` reads the whole body before anything can look
at its size. SSO discovery and JWKS streamed with a cap; the admin issuer probe,
the token exchange, the release check and webhook delivery did not. These run
the REAL streaming code (utils/http_fetch.py) against a MockTransport.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

from app.middleware.errors import AppError
from app.utils.http_fetch import ResponseTooLargeError, fetch_json_capped
from tests._http_mock import serve

MiB = 1024 * 1024


class _Exploding(httpx.AsyncByteStream):
    """A body that fails the test if anyone reads it."""

    async def __aiter__(self):
        raise AssertionError("the body was read")
        yield b""  # pragma: no cover - makes this an async generator


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, n_chunks: int, size: int):
        self.n, self.size = n_chunks, size

    async def __aiter__(self):
        for _ in range(self.n):
            yield b" " * self.size


# --- the helper ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_declared_length_over_the_cap_is_refused_before_the_body(monkeypatch):
    serve(monkeypatch, lambda _r: httpx.Response(200, headers={"content-length": "999999"}, stream=_Exploding()))
    with pytest.raises(ResponseTooLargeError):
        await fetch_json_capped("GET", "https://x.test/", max_bytes=1000, http_timeout_sec=5)


@pytest.mark.asyncio
async def test_an_undeclared_body_is_counted_as_it_streams(monkeypatch):
    serve(monkeypatch, lambda _r: httpx.Response(200, stream=_Chunks(10, 200)))
    with pytest.raises(ResponseTooLargeError):
        await fetch_json_capped("GET", "https://x.test/", max_bytes=1000, http_timeout_sec=5)


@pytest.mark.asyncio
async def test_a_non_numeric_content_length_is_ignored_not_a_500(monkeypatch):
    body = json.dumps({"ok": True}).encode()
    serve(monkeypatch, lambda _r: httpx.Response(200, headers={"content-length": "lots"}, content=body))
    assert await fetch_json_capped("GET", "https://x.test/", max_bytes=1000, http_timeout_sec=5) == {"ok": True}


@pytest.mark.asyncio
async def test_status_and_parse_failures_keep_their_types(monkeypatch):
    serve(monkeypatch, lambda _r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_json_capped("GET", "https://x.test/", max_bytes=1000, http_timeout_sec=5)
    serve(monkeypatch, lambda _r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(ValueError):
        await fetch_json_capped("GET", "https://x.test/", max_bytes=1000, http_timeout_sec=5)


# --- each caller ----------------------------------------------------------------


class _BigJson(httpx.AsyncByteStream):
    """A VALID JSON object just over 8 MiB, so only the cap can refuse it - a
    body that merely fails to parse would fail every caller for the wrong reason."""

    async def __aiter__(self):
        yield b'{"pad": "'
        for _ in range(9):
            yield b"a" * MiB
        yield b'"}'


def _too_big(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, stream=_BigJson())


@pytest.mark.asyncio
async def test_sso_discovery_refuses_an_oversized_document(make_provider, monkeypatch):
    from app.services import oidc as oidc_svc

    monkeypatch.setattr(oidc_svc, "assert_public_http_url", lambda *a, **k: None)
    serve(monkeypatch, _too_big)
    with pytest.raises(AppError) as exc:
        await oidc_svc._discovery(make_provider())
    assert exc.value.code == "OIDC_DISCOVERY_TOO_LARGE"


@pytest.mark.asyncio
async def test_sso_discovery_survives_a_non_numeric_content_length(make_provider, monkeypatch):
    from app.services import oidc as oidc_svc

    p = make_provider()
    doc = {"issuer": p.issuer_url, "token_endpoint": "https://idp.example.com/t"}
    monkeypatch.setattr(oidc_svc, "assert_public_http_url", lambda *a, **k: None)
    body = json.dumps(doc).encode()
    serve(monkeypatch, lambda _r: httpx.Response(200, headers={"content-length": "x"}, content=body))
    assert (await oidc_svc._discovery(p))["token_endpoint"] == "https://idp.example.com/t"


@pytest.mark.asyncio
async def test_jwks_refuses_an_oversized_key_set(monkeypatch):
    from app.services import jwks as jwks_svc

    monkeypatch.setattr(jwks_svc, "assert_public_http_url", lambda *a, **k: None)
    serve(monkeypatch, _too_big)
    with pytest.raises(AppError) as exc:
        await jwks_svc._fetch_jwks("https://idp.example.com/jwks")
    assert exc.value.code == "OIDC_JWKS_TOO_LARGE"


@pytest.mark.asyncio
async def test_the_admin_issuer_probe_reports_an_oversized_document(monkeypatch):
    from app.routers.admin import oidc as oidc_router

    monkeypatch.setattr(oidc_router, "assert_public_http_url", lambda *a, **k: None)
    serve(monkeypatch, _too_big)
    res = await oidc_router._probe_issuer("https://idp.example.com/realms/fh")
    assert res.ok is False
    assert "larger than 1 MiB" in (res.error or "")


@pytest.mark.asyncio
async def test_the_token_exchange_refuses_an_oversized_response(make_provider, monkeypatch):
    from app.services import oidc as oidc_svc

    p = make_provider()
    doc = {"issuer": p.issuer_url, "token_endpoint": "https://idp.example.com/token"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/openid-configuration"):
            return httpx.Response(200, json=doc)
        assert req.method == "POST"
        return _too_big(req)

    monkeypatch.setattr(oidc_svc, "assert_public_http_url", lambda *a, **k: None)
    serve(monkeypatch, handler)
    with pytest.raises(AppError) as exc:
        await oidc_svc._exchange_code(p, "the-code")
    assert exc.value.code == "OIDC_TOKEN_EXCHANGE_FAILED"


@pytest.mark.asyncio
async def test_the_release_check_reports_an_oversized_list_without_raising(db, monkeypatch):
    from app.services import release_check as rc

    serve(monkeypatch, _too_big)
    out = await rc.run_check(db, manual=True)
    assert out["ok"] is False
    assert "larger than 8 MiB" in out["error"]


@pytest.mark.asyncio
async def test_webhook_delivery_never_reads_the_response_body(db, monkeypatch):
    from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus
    from app.utils.crypto import encrypt_setting
    from app.workers import webhook_deliver as wd

    wh = Webhook(name="t", url="https://hooks.example.com/x", event_types=["share_created"],
                 secret_encrypted=encrypt_setting("s3cret"), active=True)
    db.add(wh)
    db.commit()
    monkeypatch.setattr(wd, "assert_public_http_url", lambda *a, **k: None)
    serve(monkeypatch, lambda _r: httpx.Response(200, stream=_Exploding()))
    res = await wd.webhook_deliver(None, wh.id, "share_created", {"target_id": "x"})
    assert res["status"] == "sent"
    row = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wh.id).one()
    assert row.status == WebhookDeliveryStatus.sent and row.response_code == 200


# --- the ratchet ---------------------------------------------------------------

APP = Path(__file__).resolve().parents[1] / "app"
# Every module that builds an httpx client, and why its reads are bounded.
_CLIENT_BUILDERS = {
    "utils/http_fetch.py": "the bounded JSON fetch every other caller goes through",
    "services/hibp.py": "a fixed public host (api.pwnedpasswords.com); a range answer is ~40 KB by design",
    "workers/webhook_deliver.py": "streams the POST and reads only the status code",
}


def test_only_the_bounded_paths_build_an_httpx_client():
    builders: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"AsyncClient", "Client"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx"
            ):
                builders.add(str(path.relative_to(APP)))
    assert builders == set(_CLIENT_BUILDERS), (
        "a module builds an httpx client outside the bounded paths; fetch JSON "
        f"through utils/http_fetch.fetch_json_capped instead: {sorted(builders - set(_CLIENT_BUILDERS))}"
    )


def test_webhook_delivery_does_not_buffer_the_response():
    tree = ast.parse((APP / "workers" / "webhook_deliver.py").read_text(encoding="utf-8"))
    calls = {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "client"
    }
    assert "stream" in calls and not calls & {"post", "get", "put", "request"}, calls
