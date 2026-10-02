"""No application code walks the Redis keyspace.

This Redis may be shared with other tenants, and a SCAN/KEYS over it pays a
latency tax under their load - scan_guard.py refused it for that reason, and
the inbound-attachment rescan used one anyway to find its deferred ids (now a
fixed ZSET). A reader that needs "every key like X" keeps a fixed index key
instead. ZSCAN/HSCAN/SSCAN iterate ONE key and are not keyspace walks.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_WALKS = {"scan", "scan_iter"}


def test_no_module_scans_or_keys_the_keyspace():
    scanned = 0
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name = node.func.attr
            # `.keys()` with no argument is a dict; `.keys(pattern)` is Redis KEYS.
            if name in _WALKS or (name == "keys" and (node.args or node.keywords)):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno} .{name}(...)")
    assert scanned > 200, f"the scan went vacuous ({scanned} modules)"
    assert not offenders, f"keyspace walk on a possibly shared Redis: {offenders}"
