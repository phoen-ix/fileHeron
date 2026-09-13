"""`scripts/send_ops_alert.py` reuses the shared `server_error` template rather
than shipping one of its own, and the shape it passes did not match what that
template reads.

A backup or restore-drill failure therefore arrived reading:

    Ein Serverfehler ist aufgetreten: None None
    Status: None RESTORE_DRILL_FAILED
    Fehler: None

- four literal `None`s, because an ops alert has no method, no path, no HTTP
status and no exception type - plus a blank occurrence count, because the script
set `occurrences` while the template reads `occurrence_count`. The real content
was present further down, so this was legibility rather than lost information,
but it is the mail that tells an operator their backups have stopped.
"""
from __future__ import annotations

import pytest

from app.services import email as email_svc
from app.utils.timeutil import utc_now


def _ops_payload(**over):
    """Exactly the shape scripts/send_ops_alert.py builds."""
    base = {
        "source": "ops",
        "exception_type": None,
        "message": "fileheron-restore-drill.service failed\nFAIL: throwaway db never came up",
        "method": None,
        "path": None,
        "job_name": "fileheron-restore-drill.service",
        "status_code": None,
        "code": "RESTORE_DRILL_FAILED",
        "at": utc_now(),
        "occurrence_count": 1,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("locale", ["en", "de"])
def test_an_ops_alert_renders_no_literal_none(locale):
    subject, text, html = email_svc.render_email(locale, "server_error", _ops_payload())
    assert "None" not in text, text[:400]
    assert "None" not in (html or "")
    assert "None" not in subject
    assert "RESTORE_DRILL_FAILED" in subject


@pytest.mark.parametrize("locale", ["en", "de"])
def test_an_ops_alert_names_the_unit_and_keeps_the_detail(locale):
    _subject, text, html = email_svc.render_email(locale, "server_error", _ops_payload())
    assert "fileheron-restore-drill.service" in text
    assert "throwaway db never came up" in text
    assert "fileheron-restore-drill.service" in (html or "")


@pytest.mark.parametrize("locale", ["en", "de"])
def test_the_occurrence_count_is_populated(locale):
    """The script wrote `occurrences`; the template reads `occurrence_count`, so
    the line rendered with nothing after it."""
    _subject, text, _html = email_svc.render_email(
        locale, "server_error", _ops_payload(occurrence_count=3)
    )
    assert "3" in text


def test_send_ops_alert_builds_the_key_the_template_reads():
    """Pinned against the script itself, so the two cannot drift apart again."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "scripts" / "send_ops_alert.py"
    ).read_text(encoding="utf-8")
    assert '"occurrence_count"' in src
    assert '"occurrences"' not in src, "the template reads occurrence_count"


@pytest.mark.parametrize("locale", ["en", "de"])
def test_an_http_5xx_alert_still_renders_its_request_line(locale):
    """Control: the ops branch must not swallow the ordinary HTTP path."""
    _subject, text, _html = email_svc.render_email(
        locale, "server_error",
        _ops_payload(source="http", method="GET", path="/api/shares",
                     status_code=500, exception_type="RuntimeError", code="INTERNAL"),
    )
    assert "GET /api/shares" in text
    assert "RuntimeError" in text
