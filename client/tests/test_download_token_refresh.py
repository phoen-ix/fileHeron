"""Byte transfers must survive an access-token expiry.

Every transfer path issued its requests straight against the transport, so none
of them passed through `ApiClient.request` - the only place the
401 -> refresh -> replay logic lives. They also snapshotted the Authorization
header once, at kick-off. A segmented download of a large file routinely
outlives the 15-minute access token, so it simply died, and it died as an
`OSError` rather than a `SessionExpiredError`, which meant the UI never offered
a re-login: the user saw a generic failed transfer.

The whole existing download suite builds its client with an `api_token`, which
`ApiClient.request` explicitly excludes from refresh and which defaults to never
expiring - so it was structurally incapable of seeing any of this.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import download_resumable as dr
from fileheron_client.api.client import SessionExpiredError

SERVER = "https://files.example.com"
DATA = bytes((i % 251) for i in range(50))
ETAG = '"abc-50"'


def _session_api() -> ApiClient:
    """A PASSWORD session - the mode that actually expires."""
    api = ApiClient(SERVER)
    api.access_token = "stale-token"
    return api


@respx.mock
def test_the_probe_refreshes_an_expired_token(tmp_path):
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        calls.append(auth)
        if auth == "Bearer stale-token":
            return httpx.Response(401, json={"code": "TOKEN_EXPIRED"})
        rng = request.headers.get("range")
        if not rng:
            # No Range means a full download; answering 206 here would put the
            # caller on the RESUME branch, which expects an existing .part.
            return httpx.Response(
                200, content=DATA,
                headers={"Content-Length": str(len(DATA)), "ETag": ETAG,
                         "Accept-Ranges": "bytes"},
            )
        spec = rng.split("=", 1)[1]
        a_s, b_s = spec.split("-", 1)
        a = int(a_s)
        b = int(b_s) if b_s else len(DATA) - 1
        chunk = DATA[a : b + 1]
        return httpx.Response(
            206, content=chunk,
            headers={
                "Content-Range": f"bytes {a}-{b}/{len(DATA)}",
                "Content-Length": str(len(chunk)),
                "ETag": ETAG,
            },
        )

    respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(200, json={"access_token": "fresh-token"})
    )
    respx.get(url__regex=rf"{SERVER}/api/files/.*").mock(side_effect=_handler)

    dest = tmp_path / "out.bin"
    dr.download_file_resumable(_session_api(), "f1", dest=dest, connections=1)

    assert dest.read_bytes() == DATA
    assert "Bearer stale-token" in calls, "the stale token was never presented"
    assert "Bearer fresh-token" in calls, "the refreshed token was never used"


@respx.mock
def test_a_dead_session_surfaces_as_session_expired(tmp_path):
    """Not as an OSError. The UI listens for SessionExpiredError to return the
    user to the login screen; anything else is a generic transfer failure, and
    the user has no idea they simply need to sign in again."""
    respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(401, json={"code": "INVALID_TOKEN"})
    )
    respx.get(url__regex=rf"{SERVER}/api/files/.*").mock(
        return_value=httpx.Response(401, json={"code": "TOKEN_EXPIRED"})
    )

    with pytest.raises(SessionExpiredError):
        dr.download_file_resumable(
            _session_api(), "f1", dest=tmp_path / "out.bin", connections=1
        )


@respx.mock
def test_an_expired_session_is_not_reported_as_a_network_fault(tmp_path):
    """With a checkpoint present, a failed probe used to raise 503
    RESUME_PROBE_FAILED - "Couldn't reach the server to resume this download" -
    which is a lie about the cause AND repeats forever, because nothing
    refreshes. The blanket `except Exception` in _probe is what made an expired
    session indistinguishable from an unreachable one."""
    from fileheron_client.api import download_checkpoint as ck

    dest = tmp_path / "out.bin"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(DATA[:10])
    ck.write(dest, ck.Checkpoint(file_id="f1", total=len(DATA), etag=ETAG, mode="single"))

    respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(401, json={"code": "INVALID_TOKEN"})
    )
    respx.get(url__regex=rf"{SERVER}/api/files/.*").mock(
        return_value=httpx.Response(401, json={"code": "TOKEN_EXPIRED"})
    )

    with pytest.raises(SessionExpiredError):
        dr.download_file_resumable(_session_api(), "f1", dest=dest, connections=1)


@respx.mock
def test_an_api_token_session_does_not_attempt_a_refresh():
    """API tokens do not expire and cannot be refreshed, so a 401 on one is a
    real authorisation failure - refreshing would be noise, and silently
    swapping in a session token would be worse."""
    refresh = respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(200, json={"access_token": "nope"})
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")

    with pytest.raises(SessionExpiredError):
        api.refresh_bearer()
    assert not refresh.called


@respx.mock
def test_parallel_segments_share_one_refresh():
    """N download workers share one access token AND one refresh cookie, so they
    all 401 within milliseconds of each other. Unguarded, each POSTs
    /api/auth/refresh with the same cookie: exactly one wins, and a loser whose
    read lands after the winner committed is taken for a replay of a rotated
    chain link - which revokes every session the user has on every device and
    files a `refresh_token_reused` row that reads as token theft. On a long
    download that recurred at every token expiry.

    Passing what the caller presented is what collapses N refreshes into one."""
    refresh = respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(200, json={"access_token": "fresh-token"})
    )
    api = _session_api()

    first = api.refresh_bearer_header("Bearer stale-token")
    assert first == {"Authorization": "Bearer fresh-token"}
    assert refresh.call_count == 1

    # Three sibling workers now arrive holding the SAME stale token.
    for _ in range(3):
        assert api.refresh_bearer_header("Bearer stale-token") == first
    assert refresh.call_count == 1, "siblings must replay, not rotate again"

    # A worker that presents the CURRENT token has genuinely expired: refresh.
    api.refresh_bearer_header("Bearer fresh-token")
    assert refresh.call_count == 2


@respx.mock
def test_refresh_is_serialised_across_threads():
    """The short-circuit above is only sound if the check and the POST happen
    under ONE lock. Otherwise all four threads read the stale token, all four
    pass the check, and all four rotate.

    The in-flight refresh has to be SLOW for this to test what it names: with an
    instant mock the first thread finishes before the others even look, so the
    short-circuit alone carries the test and it passes with the lock removed.
    (Verified by mutation - the earlier, instant version of this test did
    exactly that.) The barrier lines the threads up, the delay holds the window
    open, and only a real lock keeps the count at one.
    """
    import threading
    import time

    calls = {"n": 0}
    counter_lock = threading.Lock()

    def _slow_refresh(request: httpx.Request) -> httpx.Response:
        with counter_lock:
            calls["n"] += 1
        time.sleep(0.25)
        return httpx.Response(200, json={"access_token": "fresh-token"})

    respx.post(f"{SERVER}/api/auth/refresh").mock(side_effect=_slow_refresh)
    api = _session_api()
    start = threading.Barrier(4)
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            start.wait(timeout=5)
            api.refresh_bearer_header("Bearer stale-token")
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, errors
    assert calls["n"] == 1, "four threads must produce exactly one rotation"
    assert api.access_token == "fresh-token"


@respx.mock
def test_a_caller_that_presented_nothing_still_short_circuits():
    """`seen=None` means the caller presented NO token. If one exists by the time
    we hold the lock, a sibling minted it - that is conclusive, not inconclusive.

    Requiring `seen is not None` made every such caller opt out of the
    single-flight and rotate again. It is reachable from request() itself:
    `token_used` is snapshotted BEFORE the call and may be None, while the 401
    branch re-reads the current value, so the two disagree exactly when a
    sibling won the race."""
    refresh = respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(200, json={"access_token": "sibling-minted"})
    )
    api = ApiClient(SERVER)
    api.access_token = None  # this caller presented nothing

    # A sibling refreshes first.
    assert api.refresh_bearer_header("Bearer stale") == {"Authorization": "Bearer sibling-minted"}
    assert refresh.call_count == 1

    # Our caller now arrives having presented nothing at all.
    assert api.refresh_bearer_header(None) == {"Authorization": "Bearer sibling-minted"}
    assert refresh.call_count == 1, "presenting nothing must not force a rotation"


@respx.mock
def test_the_probe_passes_what_it_actually_presented():
    """_probe merged `api.auth_header()` over `headers` when sending, but passed
    `headers["Authorization"]` as `seen` - a stale snapshot, not what the server
    rejected. The short-circuit could then hand back the very token that had just
    401'd, and the retry would 401 again, surfacing as `503 RESUME_PROBE_FAILED`
    ("Couldn't reach the server") - the exact misreport _probe's docstring says
    must not happen."""
    presented: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        presented.append(request.headers.get("authorization", ""))
        if request.headers.get("authorization") == "Bearer current":
            return httpx.Response(401, json={"code": "TOKEN_EXPIRED"})
        return httpx.Response(206, headers={"Content-Range": "bytes 1-1/50", "ETag": ETAG})

    respx.get(url__regex=rf"{SERVER}/api/files/.*").mock(side_effect=_handler)
    refresh = respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(200, json={"access_token": "rotated"})
    )

    api = ApiClient(SERVER)
    api.access_token = "current"
    # A STALE value in the caller's dict - auth_header() overrides it on the wire.
    stale_headers = {"Authorization": "Bearer long-gone"}

    dr._probe(api, f"{SERVER}/api/files/fid/download", stale_headers)

    # The 401 was for "Bearer current", so that is what must be reported as seen.
    # Passing the stale value would short-circuit (current != long-gone) and
    # replay the rejected token without ever contacting the server.
    assert refresh.call_count == 1, "must actually rotate, not short-circuit on a stale seen"
    assert presented[0] == "Bearer current"
    assert presented[-1] == "Bearer rotated"
