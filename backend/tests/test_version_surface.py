"""Phase 1 self-update: version constant is wired through to user-facing
endpoints. The constant is read from env at import time so a Docker
rebuild with a new FH_VERSION arg is enough — no code edit needed."""
from __future__ import annotations

import importlib

import pytest


@pytest.mark.asyncio
async def test_health_exposes_running_version(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "running_version" in body
    assert "running_sha" in body
    # The test runner inherits whatever FH_VERSION the test image was
    # built with; just assert the fields are present + non-empty.
    assert body["running_version"]
    assert body["running_sha"]


@pytest.mark.asyncio
async def test_config_public_exposes_running_version(client):
    r = await client.get("/api/config-public")
    assert r.status_code == 200
    body = r.json()
    assert body.get("running_version")


def test_version_module_reads_from_env(monkeypatch):
    """A rebuilt image's ENV override flows into VERSION/GIT_SHA when
    the module is freshly imported. Use a sub-import so we don't clobber
    the already-loaded module that the rest of the suite depends on."""
    monkeypatch.setenv("FH_VERSION", "v9.9.9")
    monkeypatch.setenv("FH_GIT_SHA", "deadbeefcafe")
    from app import version as version_mod
    reloaded = importlib.reload(version_mod)
    try:
        assert reloaded.VERSION == "v9.9.9"
        assert reloaded.GIT_SHA == "deadbeefcafe"
    finally:
        # Reset so subsequent tests see the original placeholders.
        monkeypatch.delenv("FH_VERSION", raising=False)
        monkeypatch.delenv("FH_GIT_SHA", raising=False)
        importlib.reload(version_mod)
