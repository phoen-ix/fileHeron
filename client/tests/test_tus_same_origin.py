"""The TUS upload URL must be forced onto the origin we connected to.

A reverse proxy that forwards ``X-Forwarded-Proto: http`` makes tusd
(``-behind-proxy``) hand back an ``http://`` Location; PATCHing it triggers an
http→https 308 that the client can't follow (and following it strips auth).
``_same_origin`` keeps only the Location's path + query and grafts on our own
scheme + host. (Pure function — no tkinter, safe in CI.)
"""
from __future__ import annotations

import pytest

from fileheron_client.tus import _same_origin

SERVER = "https://files.example.com"


@pytest.mark.parametrize(
    "location",
    [
        "http://files.example.com/uploads/abc",   # wrong scheme (the actual bug)
        "https://files.example.com/uploads/abc",  # already correct
        "/uploads/abc",                            # root-relative
        "uploads/abc",                             # bare-relative
        "http://internal-host:8080/uploads/abc",   # wrong host AND scheme
    ],
)
def test_forces_server_origin(location):
    assert _same_origin(SERVER, location) == "https://files.example.com/uploads/abc"


def test_preserves_query_string():
    out = _same_origin(SERVER, "http://files.example.com/uploads/abc?x=1")
    assert out == "https://files.example.com/uploads/abc?x=1"


def test_keeps_nondefault_port():
    out = _same_origin("https://files.example.com:8443", "http://files.example.com/uploads/abc")
    assert out == "https://files.example.com:8443/uploads/abc"
