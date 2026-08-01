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
            # Try one refresh, then replay.
            ref = self._http.post(
                "/api/auth/refresh",
                headers={"Accept": "application/json"},
            )
            if ref.status_code == 200:
                # Defensive parse (finding C3): a non-JSON 200 from a
                # misconfigured proxy must not raise here - fall through to
                # returning the original 401, which the UI turns into a
                # clean re-login prompt.
                try:
                    token = ref.json().get("access_token")
                except ValueError:
                    token = None
                if token:
                    self.access_token = token
                    return self.request(
                        method,
                        path,
                        json=json,
                        params=params,
                        headers=headers,
                        data=data,
                        files=files,
                        retry_on_401=False,
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
        return resp

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
