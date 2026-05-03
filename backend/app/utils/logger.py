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
        log_record.setdefault("ts", self.formatTime(record, self.datefmt))
        log_record.setdefault("level", record.levelname)
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

    # Quiet noisy libs
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel("WARNING")
