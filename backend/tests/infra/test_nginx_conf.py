"""Semantic assertions on the edge config that no linter can express.

`nginx -t` (now in the infra-lint CI job) catches syntax and directive context.
It cannot catch any of these, which are all about what the config MEANS:

nginx-3   without `real_ip`, this container only ever sees the docker bridge
          gateway, so `limit_req_zone $binary_remote_addr` was ONE GLOBAL
          5 r/s bucket rather than per-IP - a single scanner could exhaust the
          scanner throttle for the entire internet - and the `X-Real-IP` handed
          to the backend was the gateway too.
nginx-13  a location-level `add_header` DISCARDS every add_header inherited
          from the server block. /assets/ and /fonts/ each declared a
          Cache-Control and thereby silently dropped all three security
          headers - a trap this same file documents 90 lines further down for
          /index.html, and then falls into twice above it.
nginx-12  `return 200` builds the response before `add_header` runs, and
          Content-Type is not one of the headers add_header replaces, so
          /healthz emitted it twice.
nginx-4   the file's own comment claimed access_log was off for /api and
          /uploads. It was not, so signed `?dt=` download tokens and
          /api/public/<token> paths were written to a json-file log.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[3] / "docker" / "frontend" / "nginx.conf"


def _strip_comments(text: str) -> str:
    """Comments mention directives by name. Matching on raw text made the test
    flag /healthz for an `add_header` that only appears in the comment
    explaining why it was removed."""
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())


@pytest.fixture(scope="module")
def conf() -> str:
    return _strip_comments(CONF.read_text(encoding="utf-8"))


def _location_blocks(text: str) -> dict[str, str]:
    """Crude but sufficient: split on `location <match> {` and brace-count to
    the matching close. The file has no nested blocks inside a location."""
    out: dict[str, str] = {}
    for m in re.finditer(r"location\s+([^\{]+?)\s*\{", text):
        name = m.group(1).strip()
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        out[name] = text[m.end() : i]
    return out


def test_the_parser_finds_the_blocks():
    """If this ever returns nothing, every assertion below passes for free."""
    assert len(_location_blocks(_strip_comments(CONF.read_text(encoding="utf-8")))) >= 6


# --- nginx-3 ----------------------------------------------------------------


def test_real_client_ip_is_resolved(conf):
    assert "real_ip_header X-Forwarded-For;" in conf, (
        "without real_ip the rate-limit zone keys on the docker bridge gateway"
    )
    assert re.search(r"set_real_ip_from\s+\S+;", conf)


def test_real_ip_recursion_is_off(conf):
    """Traefik overwrites rather than appends XFF, so the single value it sends
    is the trustworthy one. Recursion would walk past a genuine RFC1918 client
    on a VPN and misattribute it."""
    assert "real_ip_recursive off;" in conf


def test_trusted_sources_are_not_the_whole_internet(conf):
    """Nothing outside the compose network can reach this listener, so there is
    no reason to accept a forwarded header from anywhere."""
    assert "set_real_ip_from 0.0.0.0/0" not in conf


def test_the_probe_zone_still_keys_per_ip(conf):
    assert "limit_req_zone $binary_remote_addr zone=probe" in conf


# --- nginx-13 ---------------------------------------------------------------

_SECURITY_HEADERS = (
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
)


def test_every_location_that_sets_a_header_resets_the_security_set(conf):
    """The inheritance trap, asserted exhaustively rather than for the two
    blocks that happened to be reported. Any future location that adds a header
    and forgets the set fails here."""
    offenders = []
    for name, body in _location_blocks(conf).items():
        if "add_header" not in body:
            continue
        missing = [h for h in _SECURITY_HEADERS if h not in body]
        if missing:
            offenders.append(f"{name}: missing {missing}")
    assert not offenders, (
        "location blocks declare add_header and thereby drop the server-level "
        "security headers: " + "; ".join(offenders)
    )


def test_the_server_block_still_declares_them(conf):
    """Control: the per-location copies are a workaround for inheritance, not a
    replacement for the default."""
    # Up to the first location BLOCK, not the first occurrence of the word -
    # "see the bait locations below" appears in a comment far earlier.
    server_level = re.split(r"location\s+[^\{]+\{", conf, maxsplit=1)[0]
    for h in _SECURITY_HEADERS:
        assert h in server_level


# --- nginx-12 ---------------------------------------------------------------


def test_healthz_sets_content_type_once(conf):
    body = _location_blocks(conf)["= /healthz"]
    assert "default_type text/plain;" in body
    assert "add_header Content-Type" not in body, "two Content-Type headers"


# --- nginx-4 ----------------------------------------------------------------


@pytest.mark.parametrize("loc", ["/api/", "/uploads/"])
def test_token_bearing_routes_do_not_log_request_lines(conf, loc):
    """`?dt=` signed download tokens and /api/public/<token> both live in the
    request line."""
    assert "access_log off;" in _location_blocks(conf)[loc]
