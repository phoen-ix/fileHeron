""""Alert every admin" is ONE loop: `services/admin_alerts.notify_admins`.

The 2026-11-07 audit folded seven hand-rolled copies (query the enabled admins,
loop, dispatch, swallow a failure each in its own way) and the next audit found
four more it had missed: the server-error alert, the scan-guard block notice,
release-available and the quarantine notice (audit 2026-11-08). A list of the
known sites would have missed them too, so this scans EVERY function under
`app/`: one that filters on `UserRole.admin` and calls `dispatch` is a copy.
"""
from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_THE_HELPER = _APP / "services" / "admin_alerts.py"


def _is_admin_role(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "admin"
        and isinstance(node.value, ast.Name)
        and node.value.id == "UserRole"
    )


def _mentions_admin_role(fn: ast.AST) -> bool:
    """`<x>.role == UserRole.admin` - the filter of an admin fan-out. A role
    `in_([...employee, admin])` (a recipient list) is not one."""
    return any(
        isinstance(n, ast.Compare)
        and len(n.ops) == 1
        and isinstance(n.ops[0], ast.Eq)
        and (_is_admin_role(n.left) or _is_admin_role(n.comparators[0]))
        for n in ast.walk(fn)
    )


def _calls_dispatch(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if name == "dispatch":
                return True
    return False


def _copies() -> list[str]:
    found = []
    for path in sorted(_APP.rglob("*.py")):
        if path == _THE_HELPER:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _mentions_admin_role(fn) and _calls_dispatch(fn):
                found.append(f"{path.relative_to(_APP)}::{fn.name}")
    return found


def test_no_function_outside_admin_alerts_loops_over_admins_to_dispatch():
    copies = _copies()
    assert copies == [], (
        "hand-rolled 'notify every admin' loop(s) - use admin_alerts.notify_admins, "
        f"which counts and reports failures: {copies}"
    )


def test_the_scan_sees_the_helper_itself():
    """Anti-vacuity: the helper must match the pattern this scan looks for,
    or the scan has stopped looking."""
    tree = ast.parse(_THE_HELPER.read_text())
    helpers = [
        fn for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name in ("notify_admins", "enabled_admins")
    ]
    assert any(_calls_dispatch(fn) for fn in helpers)
    assert any(_mentions_admin_role(fn) for fn in helpers)
