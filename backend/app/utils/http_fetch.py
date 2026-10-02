"""Bounded JSON fetches from hosts this instance does not control.

A non-streamed `client.get(url)` followed by `.json()` reads the WHOLE response
into memory before anything can look at its size, so one oversized answer from
an identity provider, a release mirror an admin pointed `updates.api_url` at, or
anything in between can exhaust a worker. SSO discovery and JWKS already
streamed with a byte cap, each with its own copy of the loop; the admin issuer
probe, the token exchange and the release check did not. This is the one copy.

The SSRF guard stays at each call site: the flags differ per caller and tests
patch it there.
"""
from __future__ import annotations

import json
from typing import Any

import httpx


class ResponseTooLargeError(Exception):
    """The upstream body exceeded the caller's byte cap."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"response larger than {limit} bytes")
        self.limit = limit


def mib(limit: int) -> str:
    """`1 MiB`, for messages that name a cap."""
    return f"{limit // (1024 * 1024)} MiB" if limit % (1024 * 1024) == 0 else f"{limit} bytes"


async def fetch_json_capped(
    method: str,
    url: str,
    *,
    max_bytes: int,
    http_timeout_sec: float,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> Any:
    """Stream `url`, refuse a body over `max_bytes`, parse it as JSON.

    Raises `httpx.HTTPError` (transport failure or a 4xx/5xx), `ResponseTooLargeError`,
    or `ValueError` (not JSON). A `Content-Length` over the cap is refused before
    the body is read; one that is not a plain number is ignored rather than
    trusted, and the streamed count decides - `int()` on it used to raise a bare
    ValueError, a 500, inside SSO discovery.
    """
    async with (
        httpx.AsyncClient(timeout=http_timeout_sec) as cli,
        cli.stream(method, url, headers=headers, data=data) as resp,
    ):
        resp.raise_for_status()
        declared = (resp.headers.get("content-length") or "").strip()
        if declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLargeError(max_bytes)
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ResponseTooLargeError(max_bytes)
    return json.loads(bytes(buf))
