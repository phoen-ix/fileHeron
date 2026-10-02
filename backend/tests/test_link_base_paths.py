"""The public-link, secret-link and secret-request paths are the SPA's own
routes, so they are code constants (`config.Settings` ClassVars), never settings.

`PUBLIC_LINK_BASE_PATH` used to be an environment variable (shipped in
`.env.example`), but the SPA serves exactly `/d/:token` and `/s`, and Vue routes
on the browser's own URL - so any other value, even behind a proxy rewrite, made
every mailed and copied link a dead page. The backend builds the links
(`public_link.public_url`, `secret.secret_url`) and masks them in the mail log
from these constants, so this file reads both sides.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.config import Settings, settings

_ROUTER = (
    pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "router" / "index.ts"
)
_NAMES = ("PUBLIC_LINK_BASE_PATH", "SECRET_LINK_BASE_PATH", "REQUEST_LINK_BASE_PATH")


@pytest.mark.parametrize("name", _NAMES)
def test_the_path_is_not_a_setting(name):
    assert name not in Settings.model_fields


def test_the_environment_cannot_move_either_path(monkeypatch):
    monkeypatch.setenv("PUBLIC_LINK_BASE_PATH", "/download")
    monkeypatch.setenv("SECRET_LINK_BASE_PATH", "/secret")
    monkeypatch.setenv("REQUEST_LINK_BASE_PATH", "/request")
    fresh = Settings()
    assert (
        fresh.PUBLIC_LINK_BASE_PATH,
        fresh.SECRET_LINK_BASE_PATH,
        fresh.REQUEST_LINK_BASE_PATH,
    ) == ("/d", "/s", "/r")


@pytest.mark.skipif(not _ROUTER.is_file(), reason="frontend/ is not present in this checkout")
def test_the_spa_routes_are_the_paths_the_backend_links_to():
    paths = set(re.findall(r"""\bpath:\s*['"]([^'"]+)['"]""", _ROUTER.read_text(encoding="utf-8")))
    assert len(paths) > 20, f"the route scan matched only {sorted(paths)}"
    assert f"{settings.PUBLIC_LINK_BASE_PATH}/:token" in paths
    assert settings.SECRET_LINK_BASE_PATH in paths
    assert settings.REQUEST_LINK_BASE_PATH in paths
