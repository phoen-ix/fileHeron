"""Structured JSON logging setup. Every log line is one JSON object with at
minimum: ts, level, msg, logger. Where a request_id / user_id is in scope,
include those.
"""
from __future__ import annotations

import logging
import sys

# `pythonjsonlogger.jsonlogger` was renamed to `pythonjsonlogger.json`
# in python-json-logger 4.x. Alias keeps the rest of this file using
# `jsonlogger.JsonFormatter` as before.
from pythonjsonlogger import json as jsonlogger


class _Formatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        # ASSIGN, never setdefault. The parent writes every field named in the
        # format string as `record.__dict__.get(field)`, and a LogRecord has no
        # `ts` and no `level` (it is `levelname`) - so both keys already exist
        # as None by the time we run, and setdefault is a silent no-op. That
        # shipped `"ts": null, "level": null` on every line the app ever logged,
        # leaving the stdout record - the only durable trace for anything that
        # never reaches error_log - with no timestamp and unfilterable by
        # severity. `logger` below was correct only because it assigns.
        log_record["ts"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter("%(ts)s %(level)s %(name)s %(message)s"))

    root = logging.getLogger()
    # Replace any handlers (uvicorn adds its own; we want JSON across the board)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # arq installs its own StreamHandler on the `arq` logger, NOT on root, so
    # the loop above cannot reach it: every worker event was emitted twice, once
    # plain-text by arq and once JSON by root (~58k of the worker's 125k lines).
    # Strip it and let the records propagate to the single root handler.
    arq_logger = logging.getLogger("arq")
    for h in list(arq_logger.handlers):
        arq_logger.removeHandler(h)
    arq_logger.propagate = True

    # Quiet noisy libs
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel("WARNING")
