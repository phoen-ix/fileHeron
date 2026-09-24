"""Every OFFSET-paginated query must end its ORDER BY on a unique key.

MariaDB is free to return rows that tie on the ORDER BY columns in any order,
and may pick a different order for each query - DATETIME here is whole seconds,
and sorting the share list by `state` ties nearly every row. OFFSET pages over
such an order are not a partition: a row appears on two pages and another on
none. SQLite happens to return ties in rowid order, so no behavioural test on
the harness can see it.

`/api/shares` (the main list) and `/admin/file-history` paginated without a
tiebreaker while the other eleven OFFSET queries had one. This scan is generic
on purpose - every function that calls `.offset(` - so the next paginated query
is held to it too, not just today's thirteen (CLAUDE.md §Conventions).
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _local_assignments(fn: ast.AST) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value
    return out


def _is_id_key(e: ast.AST, assigns: dict[str, ast.AST], depth: int = 0) -> bool:
    """Is `e` a sort KEY on a primary key - `X.id`, `X.id.desc()`, `desc(X.id)`,
    a conditional of those, or a local name / starred tuple holding one?

    Deliberately not "does `.id` appear anywhere": `func.count(DownloadLog.id)`
    inside a joined subquery mentions an id and orders by nothing unique, and
    the first version of this scan let exactly that through."""
    if isinstance(e, ast.Attribute):
        return e.attr == "id"
    if isinstance(e, ast.Call):
        f = e.func
        if isinstance(f, ast.Attribute) and f.attr in ("asc", "desc") and not e.args:
            return _is_id_key(f.value, assigns, depth)
        if isinstance(f, ast.Name) and f.id in ("asc", "desc", "sa_asc", "sa_desc") and len(e.args) == 1:
            return _is_id_key(e.args[0], assigns, depth)
        return False
    if isinstance(e, ast.IfExp):
        return _is_id_key(e.body, assigns, depth) and _is_id_key(e.orelse, assigns, depth)
    if isinstance(e, ast.Starred):
        return _is_id_key(e.value, assigns, depth)
    if isinstance(e, (ast.Tuple, ast.List)):
        return any(_is_id_key(x, assigns, depth) for x in e.elts)
    if isinstance(e, ast.Name) and depth < 3 and e.id in assigns:
        return _is_id_key(assigns[e.id], assigns, depth + 1)
    return False


def _calls(fn: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


def _offenders(source: str, where: str) -> tuple[list[str], int]:
    tree = ast.parse(source)
    offenders: list[str] = []
    sites = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _calls(fn, "offset"):
            continue
        sites += len(_calls(fn, "offset"))
        assigns = _local_assignments(fn)
        order_bys = _calls(fn, "order_by")
        if not order_bys:
            offenders.append(f"{where}::{fn.name} paginates with no ORDER BY")
        for call in order_bys:
            if not any(_is_id_key(a, assigns) for a in call.args):
                offenders.append(f"{where}:{call.lineno} {fn.name}")
    return offenders, sites


def test_every_offset_paginated_query_has_a_unique_tiebreaker():
    offenders: list[str] = []
    sites = 0
    for f in sorted(APP.rglob("*.py")):
        found, n = _offenders(f.read_text(encoding="utf-8"), str(f.relative_to(APP)))
        offenders += found
        sites += n
    # Vacuity guard: the scan must actually be looking at the paginated routes.
    assert sites >= 13, f"only {sites} .offset( call sites found - is the scan broken?"
    assert not offenders, (
        "OFFSET pagination ordered only by non-unique columns - add the primary "
        f"key as the last ORDER BY term: {offenders}"
    )


def test_the_scan_flags_a_query_without_a_tiebreaker():
    """Negative control: the exact shape `/api/shares` had."""
    src = (
        "def list_things(db, page):\n"
        "    order = Thing.created_at.desc()\n"
        "    base = db.query(Thing).order_by(order)\n"
        "    return base.offset(page).limit(50).all()\n"
    )
    offenders, sites = _offenders(src, "synthetic.py")
    assert sites == 1
    assert offenders == ["synthetic.py:3 list_things"]

    fixed = src.replace(".order_by(order)", ".order_by(order, Thing.id.desc())")
    assert _offenders(fixed, "synthetic.py")[0] == []

    # An id that only appears inside an aggregate is not a tiebreaker.
    aggregate = src.replace(".order_by(order)", ".order_by(order, func.count(Other.id))")
    assert _offenders(aggregate, "synthetic.py")[0] == ["synthetic.py:3 list_things"]
