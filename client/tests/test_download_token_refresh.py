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
