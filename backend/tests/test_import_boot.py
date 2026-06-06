"""Import-completeness smoke.

Both application entry points — the web app (`app.main`, served by uvicorn)
and the background worker (`app.workers.worker`, run by ARQ) — must import
cleanly against the declared dependency closure. This mirrors the Dockerfile's
build-time `python -c "import app.main; import app.workers.worker"` so the exact
class of failure that took prod down in v1.25.0/.1 (a module imported in code
but never declared in pyproject) also fails fast in the unit suite.
"""
from __future__ import annotations

import importlib


def test_app_main_imports() -> None:
    importlib.import_module("app.main")


def test_worker_imports() -> None:
    worker = importlib.import_module("app.workers.worker")
    # The ARQ entry point must expose its settings + at least one task.
    assert getattr(worker, "WorkerSettings", None) is not None
    assert getattr(worker.WorkerSettings, "functions", None)
