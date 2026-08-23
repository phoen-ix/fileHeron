"""Every router mounted in main.py is either gated or deliberately exempt.

`test_2fa_gate_coverage.py` proves the gate BLOCKS, behaviourally, for the
routes someone thought to name. It says why runtime introspection cannot do
better: dependencies attached at `include_router()` time are invisible, and
FastAPI 0.141 collapses each include into an opaque `_IncludedRouter`.

That leaves the other half uncovered - a NEW router mounted without `_gate`
fails nothing. This scan closes it from the source instead of from the route
table, which is a different technique and not subject to that limitation.

The exempt list is the point. Each entry is a router that authenticates some
other way, and adding to it should feel like a decision:
  - anonymous by design (health, setup, branding, telemetry, public)
  - reachable before 2FA setup completes (auth, account.setup_router)
  - self-authenticating on a signed token or HMAC, because the caller cannot
    send an Authorization header (files.download_router `?dt=`, both SSE
    stream routers `?token=`, oidc_connect.callback_router state cookie,
    tus_hooks HMAC)
  - gated inside the handler (metrics: scraper token / IP allowlist)
"""
from __future__ import annotations

import ast
import pathlib

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"

# Mounted WITHOUT `dependencies=_gate`, each for a recorded reason above.
_EXEMPT = {
    "health.router",
    "metrics.router",
    "setup.router",
    "auth.router",
    "account.setup_router",
    "public.router",
    "notification_subscriptions.router",
    "branding.router",
    "telemetry.router",
    "tus_hooks.router",
    "oidc.router",
    "webauthn.auth_router",
    "files.download_router",
    "notifications.stream_router",
    "admin.stream_router",
    "oidc_connect.callback_router",
}


def _mounts() -> list[tuple[str, bool]]:
    """(router expression, carries a `dependencies=` keyword) for every
    `include_router` call in main.py."""
    text = _MAIN.read_text(encoding="utf-8")
    out: list[tuple[str, bool]] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"):
            continue
        if not node.args:
            continue
        out.append((
            ast.unparse(node.args[0]),
            any(k.arg == "dependencies" for k in node.keywords),
        ))
    return out


def test_the_scan_actually_finds_the_mounts():
    """Without this the whole file passes by matching nothing - the failure
    `_route_helpers.py` records (0 routes instead of ~234) and the one ci.yml
    records for `vue-tsc --noEmit` (0 files)."""
    mounts = _mounts()
    assert len(mounts) > 20, (
        f"the include_router scan found {len(mounts)} mounts in {_MAIN} - it has "
        "stopped working"
    )
    assert any(gated for _, gated in mounts), "no gated mount found at all"
    assert any(not gated for _, gated in mounts), "no exempt mount found at all"


def test_every_mounted_router_is_gated_or_explicitly_exempt():
    offenders = [name for name, gated in _mounts() if not gated and name not in _EXEMPT]
    assert not offenders, (
        "these routers are mounted without `dependencies=_gate` and are not in "
        f"the exempt list in {__file__}: {offenders}. Mandatory 2FA does not "
        "cover them. If that is deliberate, add them to _EXEMPT with the reason "
        "- mounting the whole account module ungated is exactly how the gate "
        "became bypassable (audit 2026-07-30)."
    )


def test_the_exempt_list_has_no_stale_entries():
    """A router that gained the gate, or was renamed, must not keep a standing
    exemption - that is a hole waiting for the name to be reused."""
    mounted_ungated = {name for name, gated in _mounts() if not gated}
    stale = sorted(_EXEMPT - mounted_ungated)
    assert not stale, (
        f"these are exempt in {__file__} but are no longer mounted ungated in "
        f"{_MAIN}: {stale}"
    )
