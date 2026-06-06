"""Single source of truth for the running app's version.

Both fields default to the placeholders below for source-tree runs
(dev + tests). Production images bake real values via Dockerfile
`ARG FH_VERSION` / `ARG FH_GIT_SHA`, which the entrypoint exports as
env vars before `uvicorn` starts. We read the env at import time so a
WORKDIR rebuild is enough to pick up a new tag - no code edit needed.

The CI workflow (`server-release.yml`) passes both args on `v*` tag
push. Local `docker compose build` without args keeps the placeholders,
which is what we want for source-mode development.
"""
from __future__ import annotations

import os

VERSION: str = os.environ.get("FH_VERSION", "0.0.0-dev")
GIT_SHA: str = os.environ.get("FH_GIT_SHA", "unknown")
