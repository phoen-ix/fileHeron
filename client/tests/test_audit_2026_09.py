"""Desktop-client findings of the 2026-09 audit, each pinned by the test that
would have caught it.

Runs on the Linux leg: nothing here imports a Tk widget. UI-side rules are
either factored into tkinter-free helpers (``downloads_registry.effective_status``,
``upload_worker.set_direct_upload_limit``) or checked structurally.
"""
from __future__ import annotations

import ast
import json
import pathlib
import queue

import httpx
import pytest
import respx

from fileheron_client import config
from fileheron_client import downloads_registry as dlreg
from fileheron_client.api import ApiClient, ApiError, SessionExpiredError
from fileheron_client.api import client as client_mod
from fileheron_client.api import uploads as uploads_api
from fileheron_client.models import DirectUploadResponse

SERVER = "https://files.example.com"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "fileheron_client"


# --- the registry rule behind the Resume button ------------------------------


def test_an_active_row_with_no_live_worker_is_an_interrupted_download():
    """Session expiry mid-transfer raises into the GLOBAL handler, so the
    per-download failure path that marks the row never runs: the row stays
    ``active`` and the partial sits on disk with no Resume button until the
    next launch's reconcile promotes it."""
    entry = {"status": dlreg.ACTIVE, "dest": "/tmp/x.bin"}
    assert (
        dlreg.effective_status(entry, in_flight=False, partial_present=True)
        == dlreg.INTERRUPTED
    )


def test_an_active_row_with_a_live_worker_is_left_alone():
    entry = {"status": dlreg.ACTIVE, "dest": "/tmp/x.bin"}
    assert dlreg.effective_status(entry, in_flight=True, partial_present=True) is None


@pytest.mark.parametrize("status", [dlreg.PAUSED, dlreg.INTERRUPTED])
def test_a_resumable_row_needs_its_partial(status):
    entry = {"status": status}
    assert dlreg.effective_status(entry, in_flight=False, partial_present=True) == status
    assert dlreg.effective_status(entry, in_flight=False, partial_present=False) is None


def test_no_row_means_nothing_to_resume():
    assert dlreg.effective_status(None, in_flight=False, partial_present=True) is None
    assert dlreg.effective_status({}, in_flight=False, partial_present=True) is None


def test_the_share_view_uses_the_rule_and_teardown_pauses_workers():
    detail = (SRC / "ui" / "share_detail_view.py").read_text(encoding="utf-8")
    assert "dlreg.effective_status(" in detail
    assert "def pause_all_in_flight" in detail
    main = (SRC / "ui" / "main_window.py").read_text(encoding="utf-8")
    teardown = main[main.index("def teardown("):]
    assert "pause_all_in_flight()" in teardown


# --- config.json is hand-editable ---------------------------------------------


def _write_config(tmp_path: pathlib.Path, body: dict) -> None:
    (tmp_path / "config.json").write_text(json.dumps(body), encoding="utf-8")


def test_a_garbage_connection_count_falls_back_to_the_default(tmp_config_dir):
    _write_config(tmp_config_dir, {"download_connections": "many"})
    assert config.load_config().download_connections == config.ClientConfig().download_connections


def test_the_connection_count_is_clamped(tmp_config_dir):
    _write_config(tmp_config_dir, {"download_connections": 99})
    assert config.load_config().download_connections == config.MAX_DOWNLOAD_CONNECTIONS
    _write_config(tmp_config_dir, {"download_connections": 0})
    assert config.load_config().download_connections == config.MIN_DOWNLOAD_CONNECTIONS


def test_wrongly_typed_fields_are_ignored_not_adopted(tmp_config_dir):
    _write_config(
        tmp_config_dir,
        {"server_url": 12, "enable_diagnostic_logging": "yes", "locale": ["de"]},
    )
    cfg = config.load_config()
    assert cfg.server_url == ""
    assert cfg.enable_diagnostic_logging is False
    assert cfg.locale == ""


def test_a_non_object_config_file_is_treated_as_missing(tmp_config_dir):
    (tmp_config_dir / "config.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert config.load_config() == config.ClientConfig()


# --- the direct-upload ceiling comes from the server --------------------------


def test_the_direct_upload_limit_adopts_the_instance_value():
    from fileheron_client.ui import upload_worker as uw

    try:
        uw.set_direct_upload_limit(5 * 1024 * 1024)
        assert uw.direct_upload_limit() == 5 * 1024 * 1024
        uw.set_direct_upload_limit("7")
        assert uw.direct_upload_limit() == 7
        for bad in (None, 0, -1, "lots", {}):
            uw.set_direct_upload_limit(bad)
            assert uw.direct_upload_limit() == uw.DIRECT_LIMIT_BYTES
    finally:
        uw.set_direct_upload_limit(None)


def test_the_worker_decides_on_the_live_limit_not_the_constant():
    src = (SRC / "ui" / "upload_worker.py").read_text(encoding="utf-8")
    body = src[src.index("def _do("):]
    assert "direct_upload_limit()" in body
    assert "size <= DIRECT_LIMIT_BYTES" not in body
    ctrl = (SRC / "ui" / "controller.py").read_text(encoding="utf-8")
    assert "set_direct_upload_limit(" in ctrl
    assert "max_direct_upload_bytes" in ctrl


def test_the_public_config_is_fetched_by_the_sign_in_worker_not_the_tk_thread():
    """The controller used to call public_config() inline in _on_signed_in."""
    login = (SRC / "ui" / "login_window.py").read_text(encoding="utf-8")
    assert login.count("public_config(api)") == 3, "each _attempt returns it"
    ctrl = (SRC / "ui" / "controller.py").read_text(encoding="utf-8")
    signed_in = ctrl[ctrl.index("def _on_signed_in("):ctrl.index("def logout(")]
    assert "if public_cfg is None:" in signed_in, "the inline fetch is the fallback only"


# --- direct uploads refresh an expired token like every other call ------------


@respx.mock
def test_a_direct_upload_refreshes_on_401_and_replays(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_bytes(b"hello world")
    seen_tokens: list[str] = []

    def _direct(request: httpx.Request) -> httpx.Response:
        seen_tokens.append(request.headers.get("Authorization", ""))
        if len(seen_tokens) == 1:
            return httpx.Response(401, json={"code": "TOKEN_EXPIRED", "error": "expired"})
        assert b"hello world" in request.content, "the replay must carry the bytes"
        return httpx.Response(
            201, json={"file_id": "f1", "size_bytes": 11, "sha256_hex": None}
        )

    respx.post(f"{SERVER}/api/uploads/direct").mock(side_effect=_direct)
    refresh = respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(
            200, json={"access_token": "fresh", "expires_in_seconds": 900}
        )
    )
    api = ApiClient(SERVER, access_token="stale")
    progress: list[tuple[int, int]] = []
    out = uploads_api.upload_direct(
        api, share_id="s1", file_path=f, on_progress=lambda d, t: progress.append((d, t))
    )
    assert out.file_id == "f1" and out.sha256_hex is None
    assert refresh.call_count == 1
    assert seen_tokens == ["Bearer stale", "Bearer fresh"]
    assert progress[-1] == (11, 11)


def test_a_direct_upload_reply_without_a_digest_still_parses():
    """The backend types sha256_hex as `str | None`; requiring it here rejected
    a reply for bytes the server already holds."""
    out = DirectUploadResponse.model_validate({"file_id": "f", "size_bytes": 1, "sha256_hex": None})
    assert out.sha256_hex is None


# --- a revoked API token returns the user to the login screen -----------------


@respx.mock
def test_a_401_on_an_api_token_session_is_a_dead_session_with_the_servers_reason():
    respx.get(f"{SERVER}/api/shares").mock(
        return_value=httpx.Response(
            401, json={"code": "INVALID_TOKEN", "error": "Token revoked."}
        )
    )
    refresh = respx.post(f"{SERVER}/api/auth/refresh")
    api = ApiClient(SERVER, api_token="fh_deadbeef_" + "x" * 43)
    with pytest.raises(SessionExpiredError) as info:
        api.request_or_raise("GET", "/api/shares")
    assert info.value.code == "INVALID_TOKEN"
    assert info.value.message == "Token revoked."
    assert refresh.call_count == 0


@respx.mock
def test_an_api_token_401_on_an_auth_route_or_opted_out_call_stays_a_plain_error():
    respx.get(f"{SERVER}/api/account/api-tokens/current").mock(
        return_value=httpx.Response(401, json={"code": "INVALID_TOKEN", "error": "x"})
    )
    respx.post(f"{SERVER}/api/auth/logout").mock(
        return_value=httpx.Response(401, json={"code": "AUTH_REQUIRED", "error": "x"})
    )
    api = ApiClient(SERVER, api_token="fh_deadbeef_" + "x" * 43)
    resp = api.request("GET", "/api/account/api-tokens/current", retry_on_401=False)
    assert resp.status_code == 401
    resp = api.request("POST", "/api/auth/logout")
    assert resp.status_code == 401


def test_a_dead_session_with_no_screen_to_tear_down_reaches_the_callers_on_failed():
    """During sign-in there is no main window: the global handler reports it
    did nothing and the overlay's own on_failed gets the error, so a revoked
    token typed into the login form shows the server's reason instead of a
    spinner that never stops."""
    from fileheron_client.ui import _async

    exc = SessionExpiredError(status_code=401, code="INVALID_TOKEN", message="revoked")
    got: list[Exception] = []
    _async.set_session_expired_handler(lambda: False)
    try:
        _async._route_failure(exc, got.append)
        # Drain the main-thread queue by hand (no Tk loop here).
        while True:
            try:
                cb, args = _async._result_q.get_nowait()
            except queue.Empty:
                break
            cb(*args)
    finally:
        _async.set_session_expired_handler(None)
    assert got == [exc]


def test_a_handled_dead_session_does_not_also_reach_on_failed():
    from fileheron_client.ui import _async

    exc = SessionExpiredError(status_code=401, code="SESSION_EXPIRED", message="x")
    got: list[Exception] = []
    bounced: list[bool] = []

    def _handler() -> bool:
        bounced.append(True)
        return True

    _async.set_session_expired_handler(_handler)
    try:
        _async._route_failure(exc, got.append)
        while True:
            try:
                cb, args = _async._result_q.get_nowait()
            except queue.Empty:
                break
            cb(*args)
    finally:
        _async.set_session_expired_handler(None)
    assert bounced == [True]
    assert got == []


def test_the_controller_reports_whether_it_bounced():
    src = (SRC / "ui" / "controller.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "session_expired"
    )
    returned = {
        ast.unparse(n.value) for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value
    }
    assert returned == {"False", "True"}, returned
    assert 'clear_secret("refresh"' not in src, "no refresh secret is ever stored"


# --- request() plumbing -------------------------------------------------------


@respx.mock
def test_the_replay_after_a_refresh_keeps_the_per_call_timeout():
    route = respx.get(f"{SERVER}/api/shares").mock(
        side_effect=[
            httpx.Response(401, json={"code": "TOKEN_EXPIRED", "error": "x"}),
            httpx.Response(200, json={"items": []}),
        ]
    )
    respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in_seconds": 1})
    )
    api = ApiClient(SERVER, access_token="stale")
    api.request("GET", "/api/shares", timeout=5.0)
    assert route.call_count == 2
    for call in route.calls:
        assert call.request.extensions["timeout"]["read"] == 5.0


def test_a_non_envelope_json_error_body_does_not_crash_the_error_report():
    resp = httpx.Response(502, json=["not", "an", "envelope"])
    err = client_mod._envelope_from_response(resp)
    assert isinstance(err, ApiError)
    assert err.status_code == 502 and err.code == "HTTP_ERROR"


# --- tus: no backoff after the last attempt ----------------------------------


@respx.mock
def test_tus_does_not_sleep_after_its_final_transport_failure(tmp_path, monkeypatch):
    from fileheron_client import tus

    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 10)
    respx.post(f"{SERVER}/uploads/").mock(
        return_value=httpx.Response(201, headers={"Location": f"{SERVER}/uploads/abc"})
    )
    respx.patch(f"{SERVER}/uploads/abc").mock(side_effect=httpx.ConnectError("down"))
    naps: list[float] = []
    monkeypatch.setattr(tus.time, "sleep", naps.append)
    with pytest.raises(tus.TusError):
        tus.upload_tus(
            server_url=SERVER, tus_endpoint="/uploads/", upload_metadata_header="k v",
            file_path=f,
        )
    assert len(naps) == tus.MAX_RETRIES - 1, naps


# --- the form honours the public-link policy ----------------------------------


def test_the_new_share_form_hides_the_public_link_when_policy_denies_it():
    src = (SRC / "ui" / "upload_panel.py").read_text(encoding="utf-8")
    assert "can_create_public_link" in src
    build = src[src.index("def _build(self)"):src.index("def _build_expiry_section")]
    assert "if self._public_link_allowed:" in build
    # And the collector distinguishes "no link wanted" from "bad input".
    assert "-> tuple[Optional[dict], bool]" in src


# --- dead code stays dead ----------------------------------------------------


def test_the_legacy_single_stream_download_is_gone():
    from fileheron_client import api as api_pkg
    from fileheron_client.api import files

    assert not hasattr(files, "download_file")
    assert "download_file" not in api_pkg.__all__
    assert "download_file_resumable" in api_pkg.__all__


def test_one_crash_log_writer():
    """__main__ and the login overlay each had their own copy, and the
    overlay's rebuilt the log directory from a second spelling of the app
    name."""
    login = (SRC / "ui" / "login_window.py").read_text(encoding="utf-8")
    assert "write_crash(" in login
    assert "platformdirs" not in login
    main = (SRC / "__main__.py").read_text(encoding="utf-8")
    assert main.count("write_crash(") >= 2
