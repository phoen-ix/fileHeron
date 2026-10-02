"""Answer the app's real httpx calls with an in-process handler.

`serve(monkeypatch, handler)` makes every `httpx.AsyncClient` built during the
test use `httpx.MockTransport(handler)`, so the code under test runs its real
streaming, status and size handling against a response the test controls - no
socket, no DNS. A stub that only implements `.get()` cannot see how the body is
read, which is the whole subject of the size caps.
"""
from __future__ import annotations

from collections.abc import Callable

import httpx

# Captured once: a second serve() in one test must replace the handler, not wrap
# the first serve()'s factory (which would keep answering with the old one).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def serve(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
