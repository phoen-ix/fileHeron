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
docker-9  an upstream written as a literal hostname is resolved ONCE, at config
          load. `docker compose up -d backend` on its own therefore left this
          container proxying to an address nothing answers on - and, worse, an
          unresolvable upstream at STARTUP makes nginx refuse to boot at all, so
          a tusd that is slow to come up took the entire SPA down with it.
files-9   client_max_body_size was pinned at 110m to "match
          MAX_DIRECT_UPLOAD_BYTES", which is an admin-tunable value: raising it
          in the UI changed nothing except that the refusal came from nginx as a
          bare 413 with no error envelope.
fe-xss-5  the SPA shipped with no CSP, justified in a comment by Element Plus -
          removed two releases earlier.

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


# --- docker-9 ---------------------------------------------------------------


def test_upstreams_are_resolved_per_request(conf):
    """A literal hostname in proxy_pass is resolved once and cached for the life
    of the worker; a variable forces a lookup per request."""
    assert "resolver 127.0.0.11" in conf, "no resolver, so no re-resolution"
    literal = re.findall(r"proxy_pass\s+http://(?!\$)([a-z]+):", conf)
    assert not literal, f"these upstreams are pinned at startup: {set(literal)}"


def test_every_proxy_pass_carries_the_request_uri(conf):
    """Naming the upstream through a variable disables proxy_pass's implicit URI
    handling. Forgetting $request_uri sends every request to `/`."""
    for m in re.finditer(r"proxy_pass\s+(http://\$[^;]+);", conf):
        assert "$request_uri" in m.group(1), m.group(1)


# --- files-9 ----------------------------------------------------------------


def test_the_api_body_cap_is_above_the_tunable_ceiling(conf):
    """The BACKEND must be what enforces the direct-upload limit, so that
    exceeding it produces the standard error envelope rather than a bare nginx
    413 the SPA cannot explain."""
    body = _location_blocks(conf)["/api/"]
    m = re.search(r"client_max_body_size\s+(\d+)m;", body)
    assert m, "the /api/ body cap is gone; nginx defaults to 1 MB"
    assert int(m.group(1)) >= 512, (
        f"{m.group(1)}m is at or near the admin-tunable ceiling; raising "
        "MAX_DIRECT_UPLOAD_BYTES would 413 at the edge instead"
    )


def test_the_tus_path_stays_uncapped(conf):
    """Control: resumable uploads run to ~30 GB and must never be capped here."""
    assert "client_max_body_size 0;" in _location_blocks(conf)["/uploads/"]


# --- fe-xss-5 / fe-auth-10 --------------------------------------------------


def test_a_csp_is_shipped(conf):
    assert "Content-Security-Policy" in conf


def test_the_csp_is_report_only_for_now(conf):
    """Enforcing is a separate, deliberate release step: a wrong policy is a
    blank SPA, which reads as a total outage."""
    assert "Content-Security-Policy-Report-Only" in conf
    assert re.search(r"add_header\s+Content-Security-Policy\s", conf) is None


def test_the_policy_has_somewhere_to_report_to(conf):
    """Without a sink, "observe for a release" observes nothing."""
    assert "report-uri /api/telemetry/csp-report" in conf


@pytest.mark.parametrize(
    "directive",
    ["script-src 'self'", "object-src 'none'", "frame-ancestors 'none'",
     "base-uri 'self'", "form-action 'self'"],
)
def test_the_policy_locks_down_the_directives_that_matter(conf, directive):
    assert directive in conf


def test_script_src_has_no_unsafe_escape_hatch(conf):
    """style-src keeps 'unsafe-inline' deliberately; script-src is the directive
    doing the actual work, and an escape hatch there voids the whole policy."""
    csp = re.search(r'set \$fh_csp "([^"]+)"', conf).group(1)
    script = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
    assert "unsafe-inline" not in script and "unsafe-eval" not in script


def test_no_comment_still_blames_a_removed_library(conf):
    """The comment may reference Element Plus only to record that it USED to be
    the excuse - never as a live reason."""
    raw = CONF.read_text(encoding="utf-8")
    for idx in [
        m.start() for m in re.finditer(r"Element Plus", raw)
    ]:
        window = raw[max(0, idx - 200): idx + 200]
        assert "was removed" in window or "removed two releases" in window, (
            "Element Plus is cited as a live constraint; it was removed in v1.14"
        )


# --- the anonymous telemetry beacons -----------------------------------------


def test_the_telemetry_beacons_are_capped_far_below_the_upload_path(conf):
    """`/api/telemetry/*` are the only unauthenticated POSTs on the API and
    exist to accept a few hundred bytes of JSON, but inherited the 1024m cap
    that exists for `/api/uploads/direct`.

    Capping at the EDGE is what covers both of them: `/csp-report` reads the
    body itself and can check Content-Length first, but `/page-404` takes a
    Pydantic body model, so FastAPI buffers and validates before any handler
    code - or dependency - runs."""
    blocks = _location_blocks(conf)
    assert "/api/telemetry/" in blocks, (
        "the telemetry beacons fell back to the /api/ block's 1024m cap"
    )
    body = blocks["/api/telemetry/"]
    m = re.search(r"client_max_body_size\s+(\d+)k;", body)
    assert m, f"expected a kilobyte-scale cap, got: {body}"
    assert int(m.group(1)) <= 256, f"{m.group(1)}k is not a beacon-sized cap"


def test_the_telemetry_block_still_proxies_to_the_backend(conf):
    """A longer prefix than /api/ wins in nginx, so this block must carry the
    full proxy config - otherwise capping the body silently breaks the route."""
    body = _location_blocks(conf)["/api/telemetry/"]
    assert "proxy_pass" in body
    assert "X-Forwarded-For" in body
    assert "X-Real-IP" in body
