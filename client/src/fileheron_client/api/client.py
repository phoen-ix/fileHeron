"""Thin httpx wrapper.

One ``ApiClient`` instance per running session. Holds the base URL,
the current bearer token, and an ``httpx.Client`` cookie jar so the
backend's ``fh_refresh`` cookie (path-scoped to ``/api/auth``)
survives between requests.

When a request returns 401, the client tries one ``/api/auth/refresh``
and replays the original. Refresh failure → ``ApiError`` propagates
upward; the UI layer is responsible for bouncing the user back to
the login window and clearing the keyring entry.
"""
from __future__ import annotations

import logging
import ssl
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

import httpx

logger = logging.getLogger("fileheron_client.api.client")

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0, read=120.0)


@dataclass
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.status_code} {self.code}] {self.message}"

    def localized(self) -> str:
        """The message to show a person, in THEIR language.

        `message` is whatever the backend wrote, and the backend writes English
        - so a German user saw every label around them in German and the error
        itself in English (audit #2). Keyed on the code, which is the stable
        part of the envelope; an unknown code falls back to the server's text
        rather than to nothing.
        """
        from ..i18n import has, t

        key = f"errors.{self.code}"
        return t(key) if has(key) else (self.message or self.code)


class SessionExpiredError(ApiError):
    """Raised when a 401 could not be recovered by a token refresh - the
    session is truly dead (revoked refresh token, disabled account, …).

    Subclasses ``ApiError`` so existing ``isinstance(exc, ApiError)`` checks
    in the panels still match (no regression), but the UI's async layer
    intercepts it specifically to bounce the user back to the login overlay
    instead of rendering an inline error on a now-unusable screen."""


def _envelope_from_response(resp: httpx.Response) -> ApiError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        # A proxy that answers JSON that is not an envelope (a bare list or
        # string) must not turn the error report itself into an AttributeError.
        body = {}
    return ApiError(
        status_code=resp.status_code,
        code=body.get("code", "HTTP_ERROR"),
        message=body.get("error") or body.get("message") or resp.text or "",
        details=body.get("details") or {},
        request_id=body.get("request_id"),
    )


def json_or_raise(resp: httpx.Response) -> Any:
    """Parse a (already status-checked) response body as JSON, or raise a
    clear ApiError. The typed wrappers feed this straight into a Pydantic
    model, so a 200 with a non-JSON body (proxy/misconfig) should surface as
    a clean MALFORMED_RESPONSE error rather than a raw ValueError that the
    UI reports as a generic failure (finding C3)."""
    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(
            status_code=resp.status_code,
            code="MALFORMED_RESPONSE",
            message="Server returned a malformed (non-JSON) response.",
        ) from exc


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """Trust the OS certificate store IN ADDITION TO certifi's bundle.

    httpx verifies against certifi alone. On Windows that ignores the machine's
    Trusted Root store, which is where an organisation's TLS-inspecting proxy
    (and any internal CA) puts its root via group policy - so every browser on
    the laptop reaches the instance and this client alone fails at sign-in with
    CERTIFICATE_VERIFY_FAILED, which reads like a server fault. `ssl`'s default
    context loads that store on Windows and the system bundle elsewhere.

    Additive, deliberately: certifi is loaded on top, so a machine whose store
    is missing a public root is no worse off than before. Verification stays
    ON - this widens who is trusted to include the operator's own CAs, it does
    not weaken the check.
    """
    ctx = ssl.create_default_context()  # calls load_default_certs()
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi ships with httpx
        logger.debug("certifi bundle unavailable; using OS trust store only")
    return ctx


class ApiClient:
    """One-per-session HTTP client. Methods return parsed JSON dicts;
    callers wrap them in Pydantic models."""

    def __init__(
        self,
        server_url: str,
        *,
        access_token: Optional[str] = None,
        api_token: Optional[str] = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.access_token = access_token
        self.api_token = api_token
        # Serialises refreshes across threads - see _refresh_access_token.
        self._refresh_lock = threading.Lock()
        self._http = httpx.Client(
            base_url=self.server_url,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=False,
            verify=_ssl_context(),
        )

    # ---- lifecycle -----------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ---- auth helpers --------------------------------------------------

    def set_access_token(self, token: Optional[str]) -> None:
        self.access_token = token

    def set_api_token(self, token: Optional[str]) -> None:
        self.api_token = token

    @property
    def bearer(self) -> Optional[str]:
        # API token wins when set - never used together.
        return self.api_token or self.access_token

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self.bearer:
            h["Authorization"] = f"Bearer {self.bearer}"
        if extra:
            h.update(extra)
        return h

    # ---- request --------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        data: Any = None,
        files: Any = None,
        retry_on_401: bool = True,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Issue a request relative to ``self.server_url``. Auto-handles
        a single 401 → refresh → retry cycle when an access token is in
        play (not for API-token sessions - those never refresh).

        ``timeout`` overrides the client default for this one call. Only set
        it when given - passing ``timeout=None`` to httpx means *infinite*,
        the opposite of what a short-deadline caller (e.g. logout-on-close)
        wants."""
        extra_kw: dict[str, Any] = {}
        if timeout is not None:
            extra_kw["timeout"] = timeout
        # Snapshot BEFORE the call: if this 401s and the token has moved on by
        # the time we look, another thread already refreshed for us.
        token_used = self.access_token
        resp = self._http.request(
            method,
            path,
            json=json,
            params=params,
            data=data,
            files=files,
            headers=self._headers(headers),
            **extra_kw,
        )
        if (
            resp.status_code == 401
            and retry_on_401
            and self.access_token is not None
            and self.api_token is None
            and not path.startswith("/api/auth/")
        ):
            # Try one refresh, then replay. Single-flighted: `token_used` is what
            # this call actually presented, so a thread that lost the race
            # replays instead of rotating the cookie a second time.
            if self._refresh_access_token(token_used):
                return self.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=headers,
                    data=data,
                    files=files,
                    retry_on_401=False,
                    timeout=timeout,
                )
            # Refresh was attempted (we had an access token, this isn't an
            # /api/auth call, not an API-token session) but didn't yield a
            # usable token → the session is dead. Signal the UI to return to
            # login rather than letting a raw 401 surface as a panel error.
            raise SessionExpiredError(
                status_code=401,
                code="SESSION_EXPIRED",
                message="Your session expired. Please sign in again.",
            )
        if (
            resp.status_code == 401
            and retry_on_401
            and self.api_token is not None
            and not path.startswith("/api/auth/")
        ):
            # An API token cannot be refreshed, so a 401 on one is final: it was
            # revoked, expired or disabled server-side. As a plain ApiError it
            # left every panel printing inline errors on a screen that could
            # never work again; as SessionExpiredError the global handler
            # returns the user to the login overlay. The envelope's own code and
            # message travel with it, so what a person reads is the server's
            # reason (INVALID_TOKEN, ...), not a generic "session expired".
            env = _envelope_from_response(resp)
            raise SessionExpiredError(
                status_code=401,
                code=env.code,
                message=env.message,
                details=env.details,
                request_id=env.request_id,
            )
        return resp

    def _refresh_access_token(self, seen: Optional[str]) -> Optional[str]:
        """Refresh at most once per expiry, however many threads ask at once.

        Every thread in this process shares one ``httpx.Client`` and therefore
        one ``fh_refresh`` cookie, and a segmented download runs N workers off a
        single access token - so all N 401 within milliseconds of each other and,
        unguarded, all N POST /api/auth/refresh with the same cookie value.
        Server-side that is ``rotate_refresh``, where exactly one wins and the
        rest are either soft-failed (``INVALID_REFRESH``) or - if their read
        lands after the winner committed - taken for a replay of a rotated chain
        link, which revokes EVERY session the user has on every device and files
        a ``refresh_token_reused`` audit row that reads as token theft. On a long
        download that would recur every time the access token expired.

        ``seen`` is the token the caller actually presented. If the current one
        no longer matches it, a sibling thread has already refreshed and this
        caller should simply replay - no second rotation.
        """
        with self._refresh_lock:
            # `seen is None` means the caller presented NOTHING. If a token
            # exists now, a sibling minted it while we queued - which is
            # conclusive, not inconclusive. Requiring `seen is not None` made
            # every such caller opt out of the single-flight and rotate again,
            # which is the redundant rotation this exists to prevent. It is
            # reachable from request() itself: `token_used` is snapshotted
            # BEFORE the call and may be None while the 401 branch re-reads the
            # current value, so the two disagree exactly when a sibling won.
            if self.access_token is not None and self.access_token != seen:
                return self.access_token
            ref = self._http.post(
                "/api/auth/refresh",
                headers={"Accept": "application/json"},
            )
            if ref.status_code != 200:
                return None
            # Defensive parse (finding C3): a non-JSON 200 from a misconfigured
            # proxy must not raise here - the caller falls through to a clean
            # re-login prompt.
            try:
                token = ref.json().get("access_token")
            except ValueError:
                return None
            if not token:
                return None
            self.access_token = token
            return str(token)

    def refresh_bearer(self, seen: Optional[str] = None) -> str:
        """Refresh the access token for a call site that cannot use request().

        Byte transfers stream their responses, so they issue requests straight
        against the transport and never pass through request()'s
        401 -> refresh -> replay. They also snapshotted the Authorization
        header once, at kick-off - so any transfer outliving the access token
        (15 minutes by default, and the server can lower it) died mid-flight.
        Worse, it surfaced as a transport error rather than SessionExpiredError,
        so the UI never offered a re-login and the user just saw a failed
        download.

        Returns the new header value. Raises SessionExpiredError if the session
        is genuinely gone, which is what the UI listens for.

        `seen` is what this caller presented (a bare token or a full
        "Bearer <tok>" value). Supplying it is what makes N parallel download
        segments share ONE refresh - see _refresh_access_token.
        """
        if self.api_token is not None:
            # API tokens do not expire and cannot be refreshed, so a 401 on one
            # is a real authorisation failure, not an expiry.
            raise SessionExpiredError(
                status_code=401,
                code="SESSION_EXPIRED",
                message="This API token is no longer accepted.",
            )
        # Callers hand us whatever they presented - a bare token from request(),
        # or a whole "Bearer <tok>" header value from the transfer call sites.
        # Compare like with like or the short-circuit never matches and every
        # segment rotates anyway.
        if seen and seen.lower().startswith("bearer "):
            seen = seen.split(" ", 1)[1].strip()
        token = self._refresh_access_token(seen)
        if not token:
            raise SessionExpiredError(
                status_code=401,
                code="SESSION_EXPIRED",
                message="Your session expired. Please sign in again.",
            )
        return f"Bearer {token}"

    def refresh_bearer_header(self, seen: Optional[str] = None) -> dict:
        """`refresh_bearer` as a header dict, for the byte-transfer call sites.

        Pass `seen` - the Authorization value this worker actually sent - so
        parallel segments sharing one expired token refresh once between them
        instead of once each.
        """
        return {"Authorization": self.refresh_bearer(seen)}

    def auth_header(self) -> dict:
        """Current Authorization header, read fresh.

        Call sites must not cache this: the whole defect was a value snapshot
        taken once at kick-off.
        """
        return {"Authorization": f"Bearer {self.bearer}"} if self.bearer else {}

    def request_or_raise(
        self,
        method: str,
        path: str,
        *,
        expected: int = 200,
        **kwargs: Any,
    ) -> Any:
        resp = self.request(method, path, **kwargs)
        if resp.status_code != expected:
            raise _envelope_from_response(resp)
        if resp.status_code == 204 or not resp.content:
            return None
        # Non-JSON body on a success status → clean error, not raw bytes.
        # The old `return resp.content` fallback handed callers bytes that
        # they then fed to model_validate()/[...] (finding C3/C4).
        return json_or_raise(resp)
