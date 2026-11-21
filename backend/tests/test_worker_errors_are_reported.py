"""An error-level log line in a worker is never the only trace of a failure.

`logger.error` reaches neither the error log nor any alert, and stdout goes
with the container at the next update. `av_scan_file` logged "error from
clamd" and returned normally: when clamd could not open an upload on
2026-11-20, nothing else recorded it and no admin was told. tracked crons had
the same hole until `tests/test_cron_failure_reporting.py` (2026-11-07); this
is that rule for everything else a worker runs - the event-driven jobs
(scan, mail, webhook) and the helpers around the crons.

Pinned GENERICALLY: an AST scan over every `app/workers/*.py`. A
`logger.error` / `.exception` / `.critical` call outside a `@track_cron`
function must share its block - the statement list it sits in - with a durable
report (an audit row, an alert, a finalized mail-log row, a failed-run mark)
or a `raise`. The allowlist names the deliberate exceptions with their reason;
a new bare error log fails here until it is classified.
"""
from __future__ import annotations

import ast
from pathlib import Path

_WORKERS = sorted((Path(__file__).resolve().parents[1] / "app" / "workers").glob("*.py"))

_LOUD = {"error", "exception", "critical"}
# Calls that leave a durable record or reach an admin.
_REPORTS = {
    "record_audit_event",
    "mark_failed",
    "ops_alert",
    "notify_admins",
    "_alert_admins",
    "_report_scan_failure",
    "_record_undeliverable_audit",
    "_finalize_log",
}

# (module stem, function) -> why its bare error log is not a lost failure.
_ALLOWED: dict[tuple[str, str], str] = {
    ("av_scan", "_report_scan_failure"): (
        "the report itself could not be written; the sweep's six-hour rule "
        "(cleanup_stale_uploads) is the backstop"
    ),
    ("notify_admin_error", "notify_admin_error"): (
        "the alert pipeline's own failure must not feed itself as a new alert"
    ),
    ("send_email", "_record_undeliverable_audit"): (
        "the audit write that reports an undeliverable mail failed; the mail-log "
        "row is still finalized by the caller"
    ),
    ("send_email", "_finalize_log"): (
        "the mail-log write that records the outcome failed; there is no second "
        "place to record a failed record"
    ),
    ("send_email", "_already_sent"): (
        "the mail-log row could not be read: the mail is sent anyway, nothing failed"
    ),
    ("cron_dispatch", "_alarm"): (
        "the alarm about a task that could not be dispatched could not be written "
        "itself; the next tick tries again"
    ),
    ("webhook_deliver", "webhook_deliver"): (
        "the delivery row already carries the error and stays `pending` in the "
        "admin's delivery list"
    ),
    ("drain_pending_update", "drain_pending_update"): (
        "reporting how a handed-off update ended is retried on the next tick and "
        "must never stop the drain"
    ),
}

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_tracked(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
        if name == "track_cron":
            return True
    return False


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    return f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)


def _is_loud_log(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LOUD
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    )


def _reports(stmts: list[ast.stmt]) -> bool:
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise) or _call_name(node) in _REPORTS:
                return True
    return False


def _blocks(node: ast.AST):
    """Every statement list directly inside `node`, never entering a nested
    function - its blocks are its own."""
    for field in ("body", "orelse", "finalbody"):
        stmts = getattr(node, field, None)
        if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
            yield stmts
    for handler in getattr(node, "handlers", []):
        yield handler.body
    for case in getattr(node, "cases", []):
        yield case.body


def _bare_logs(fn: ast.AST) -> int:
    """Loud log calls in `fn` whose own block neither reports nor raises."""
    bare = 0

    def visit(stmts: list[ast.stmt]) -> None:
        nonlocal bare
        own = [
            s
            for s in stmts
            if isinstance(s, ast.Expr) and _is_loud_log(s.value)
        ]
        if own and not _reports(stmts):
            bare += len(own)
        for s in stmts:
            if isinstance(s, _FUNCS):
                continue
            for inner in _blocks(s):
                visit(inner)

    for block in _blocks(fn):
        visit(block)
    return bare


def _scan(source: str, stem: str) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, _FUNCS) and not _is_tracked(node):
            n = _bare_logs(node)
            if n:
                out[(stem, node.name)] = n
    return out


def test_no_worker_failure_is_only_a_log_line():
    found: dict[tuple[str, str], int] = {}
    for path in _WORKERS:
        found.update(_scan(path.read_text(), path.stem))
    unexplained = sorted(k for k in found if k not in _ALLOWED)
    assert unexplained == [], (
        "an error-level log is the only trace of a failure here - record it "
        "(audit row, alert, mark_failed) or raise, or add it to _ALLOWED with "
        f"the reason: {unexplained}"
    )
    stale = sorted(k for k in _ALLOWED if k not in found)
    assert stale == [], f"_ALLOWED names code that no longer logs bare: {stale}"
    assert len(_WORKERS) >= 25, "the glob no longer finds the workers"


def test_every_allowance_has_a_reason():
    assert all(len(why) > 30 for why in _ALLOWED.values())


def test_the_scan_catches_what_it_is_for():
    # The scan worker's error branch as it was on 2026-11-20.
    before = '''
async def av_scan_file(_ctx, file_id):
    result = scan(file_id)
    if result.state == "clean":
        return {"state": "clean"}
    logger.error("av_scan: %s error from clamd: %s", file_id, result.raw)
    return {"file_id": file_id, "state": "error"}
'''
    assert _scan(before, "av_scan") == {("av_scan", "av_scan_file"): 1}

    after = before.replace(
        'logger.error("av_scan: %s error from clamd: %s", file_id, result.raw)',
        "logger.warning('...')\n    _report_scan_failure(db, file_id, result.raw)",
    )
    assert _scan(after, "av_scan") == {}

    swallowed = '''
def job():
    try:
        work()
    except Exception:
        logger.exception("work failed")
    return {}
'''
    assert _scan(swallowed, "x") == {("x", "job"): 1}
    log = 'logger.exception("work failed")'
    assert _scan(swallowed.replace(log, f"{log}\n        raise"), "x") == {}
    recorded = swallowed.replace(log, f'{log}\n        record_audit_event(db, event_type="x")')
    assert _scan(recorded, "x") == {}
    # A report in ANOTHER block of the same function does not cover it.
    elsewhere = swallowed.replace("    return {}", "    if flag:\n        mark_failed({}, 'x')\n    return {}")
    assert _scan(elsewhere, "x") == {("x", "job"): 1}
    # A tracked cron is the cron ratchet's business.
    assert _scan("@track_cron('j')\n" + swallowed.lstrip(), "x") == {}
