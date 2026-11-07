"""`utils/client_ip.get_client_ip` is the ONE reading of the caller's address.

`request.client.host` was inlined at 26 sites next to it, with three different
falsy defaults - every per-IP limiter key in the auth routers among them. The
helper exists because the scan guard joins `login_attempts.ip` against the
address it counted and both must be in one canonical form (IPv4-mapped IPv6
unwrapped); a limiter keyed on the raw form gives one host two budgets on a
dual-stack deployment (audit 2026-11-07).

AST-based, so a mention in a comment or docstring does not count, and a new
inline read anywhere under `app/` fails here.
"""
from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_HELPER = _APP / "utils" / "client_ip.py"


def _inline_client_host_reads():
    for path in sorted(_APP.rglob("*.py")):
        if path == _HELPER:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "host"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "client"
            ):
                yield f"{path.relative_to(_APP)}:{node.lineno}"


def test_no_module_reads_request_client_host_directly():
    assert list(_inline_client_host_reads()) == []


def test_the_scan_sees_the_pattern_it_guards_against():
    tree = ast.parse("ip = request.client.host if request.client else ''")
    hits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "host"
        and isinstance(n.value, ast.Attribute) and n.value.attr == "client"
    ]
    assert len(hits) == 1
