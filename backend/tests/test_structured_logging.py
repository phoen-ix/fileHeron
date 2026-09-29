"""The JSON log shape is a project convention ("JSON one-line-per-event
logging"), and until now NOTHING pinned it - which is how `"ts": null,
"level": null` shipped on every line this app has ever logged, in every release.

The defect: python-json-logger's `BaseJsonFormatter.add_fields` writes every
field named in the format string as `record.__dict__.get(field)`. A LogRecord
has no `ts` and no `level` (it is `levelname`), so both keys already EXIST as
None by the time our subclass runs - and `setdefault` on an existing key is a
silent no-op. `logger` was correct only because it assigns.

Pinned GENERICALLY - against the format string the code actually installs,
rather than a hand-list of the two fields that happened to break. A future
`%(foo)s` with no LogRecord attribute and no explicit assignment is the same
bug and must fail here. Reading the configured formatter rather than restating
the format string is also what stops this test from passing on a re-derivation
of the thing it is supposed to check.
"""
from __future__ import annotations

import io
import json
import logging
import re

import pytest

from app.utils.logger import configure_logging


@pytest.fixture
def logging_state():
    """configure_logging() mutates global logging state (it strips root
    handlers, which pytest also installs). Restore everything afterwards."""
    root = logging.getLogger()
    arq = logging.getLogger("arq")
    saved = (list(root.handlers), root.level, list(arq.handlers), arq.propagate, arq.level)
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    arq.handlers[:] = saved[2]
    arq.propagate = saved[3]
    arq.setLevel(saved[4])


def _configured_formatter() -> logging.Formatter:
    configure_logging()
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1, f"expected exactly one root handler, got {handlers!r}"
    fmt = handlers[0].formatter
    assert fmt is not None
    return fmt


def _render(fmt: logging.Formatter, **kw) -> dict:
    record = logging.LogRecord(
        kw.get("name", "fileheron.audit"),
        kw.get("level", logging.ERROR),
        "/app/x.py",
        1,
        kw.get("msg", "audit"),
        None,
        None,
    )
    return json.loads(fmt.format(record))


def _declared_fields(fmt: logging.Formatter) -> list[str]:
    fields = getattr(fmt, "_required_fields", None)
    if not fields:
        fields = re.findall(r"%\((\w+)\)", fmt._fmt or "")
    assert fields, "could not read the formatter's declared fields"
    return list(fields)


def test_every_field_named_in_the_format_string_is_actually_populated(logging_state):
    """The generic invariant. Declaring a field in the format string and not
    assigning it emits the key as null - which is the whole bug."""
    fmt = _configured_formatter()
    declared = _declared_fields(fmt)
    payload = _render(fmt)

    for field in declared:
        assert field in payload, f"{field} declared in the format string but absent from the JSON"
        assert payload[field] is not None, (
            f"{field} is declared in the format string but emitted as null - "
            "assign it in _Formatter.add_fields (setdefault is a no-op, the "
            "parent already wrote the key as None)"
        )


def test_a_log_line_carries_a_real_timestamp_and_severity(logging_state):
    """The two that were broken in production, asserted on what the code
    produced rather than on a re-derivation of it."""
    fmt = _configured_formatter()
    payload = _render(fmt, level=logging.ERROR)

    assert payload["level"] == "ERROR"
    assert re.match(r"\d{4}-\d{2}-\d{2}", str(payload["ts"])), payload["ts"]
    assert payload["logger"] == "fileheron.audit"

    warning = _render(fmt, level=logging.WARNING)
    assert warning["level"] == "WARNING", "level must track the record, not be a constant"


def test_configure_logging_strips_arqs_own_handler(logging_state):
    """arq installs a StreamHandler on the `arq` logger, not on root, so the
    root-handler sweep cannot reach it and every worker event was emitted twice
    - once plain by arq, once JSON by root (~58k of 125k lines)."""
    arq = logging.getLogger("arq")
    arq.addHandler(logging.StreamHandler())
    assert arq.handlers

    configure_logging()

    assert arq.handlers == [], "arq's own handler survived - worker events will log twice"
    assert arq.propagate is True, "arq records must still reach the single root JSON handler"


def test_a_newline_in_a_logged_value_cannot_start_a_second_line(logging_state):
    """Why CodeQL's py/log-injection is switched off
    (.github/codeql/codeql-config.yml): every logging call in backend/app ends
    in the one root JSON handler, which escapes a newline inside a JSON string,
    so a user-supplied value cannot forge a line. Emitted through a real logger
    and captured from the installed handler, not the formatter alone - a
    plain-text handler added beside it fails here, and then the check has to
    come back on."""
    configure_logging()
    captured = []
    for handler in logging.getLogger().handlers:
        assert isinstance(handler, logging.StreamHandler), handler
        buf = io.StringIO()
        handler.setStream(buf)
        captured.append(buf)

    hostile = 'x\n{"level": "CRITICAL", "message": "forged"}\r\ny'
    log = logging.getLogger("fileheron.test_log_injection")
    log.warning("provider=%s", hostile, extra={"target_id": hostile})
    try:
        raise ValueError(hostile)
    except ValueError:
        log.exception("handler %s failed", hostile)

    for buf in captured:
        lines = buf.getvalue().splitlines()
        assert len(lines) == 2, f"two log calls must give two lines, got {lines!r}"
        warning, error = (json.loads(line) for line in lines)
        assert warning["message"] == f"provider={hostile}"
        assert warning["target_id"] == hostile
        assert error["message"] == f"handler {hostile} failed"
        assert hostile in error["exc_info"]
