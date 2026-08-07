"""The updater must not break the next update.

`docker/updater-executor/run.py` runs `docker compose up -d` from inside a
container whose working directory is `/workspace`. `docker-compose.yml` defines
the shim's host-path variables with a `${PWD}` fallback, so unless the executor
pins them explicitly, the updater-shim it recreates is left believing the host
state directory is `/workspace/data/updater`.

Nothing looks wrong at the time - the shim's own `/state` mount resolves
correctly, because that line uses COMPOSE_HOST_ROOT. The damage shows up on the
NEXT update: the shim spawns an executor with `-v /workspace/data/updater:/state`,
Docker creates that path empty and root-owned on the host, and the executor exits
1 with "no /state/current_job.json" before it can write a status.

So every SUCCESSFUL update broke the one after it. Observed in production going
v2.9.0 -> v2.10.0. There is no other test over this file, and the failure is
invisible until an operator is already mid-upgrade, so it gets one here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RUN_PY = (
    Path(__file__).resolve().parents[2] / "docker" / "updater-executor" / "run.py"
)


@pytest.fixture(scope="module")
def executor():
    """Load the executor script by path - it is a standalone entrypoint that
    ships in its own image, not an importable package."""
    spec = importlib.util.spec_from_file_location("fh_updater_executor", _RUN_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compose_env_pins_every_host_path(executor, monkeypatch):
    monkeypatch.setenv("COMPOSE_HOST_ROOT", "/opt/fileHeron")
    monkeypatch.delenv("UPDATER_HOST_WORKSPACE", raising=False)
    monkeypatch.delenv("UPDATER_HOST_STATE", raising=False)

    env = executor._compose_env("v9.9.9")

    assert env["FH_TAG"] == "v9.9.9"
    assert env["COMPOSE_HOST_ROOT"] == "/opt/fileHeron"
    # These two are the regression. Without them compose falls back to $PWD,
    # which is /workspace inside the executor.
    assert env["UPDATER_HOST_WORKSPACE"] == "/opt/fileHeron"
    assert env["UPDATER_HOST_STATE"] == "/opt/fileHeron/data/updater"
    assert "/workspace" not in "".join(env.values())


def test_an_explicit_value_from_a_newer_shim_wins(executor, monkeypatch):
    """A shim that passes the paths verbatim must be believed, so an operator
    who relocated the state directory is not silently overridden."""
    monkeypatch.setenv("COMPOSE_HOST_ROOT", "/srv/fh")
    monkeypatch.setenv("UPDATER_HOST_WORKSPACE", "/srv/fh")
    monkeypatch.setenv("UPDATER_HOST_STATE", "/var/lib/fh/updater")

    env = executor._compose_env("v9.9.9")

    assert env["UPDATER_HOST_WORKSPACE"] == "/srv/fh"
    assert env["UPDATER_HOST_STATE"] == "/var/lib/fh/updater"


def test_without_a_host_root_nothing_is_invented(executor, monkeypatch):
    """No COMPOSE_HOST_ROOT means we are not running under the shim at all
    (a manual invocation). Guessing a host path there would be worse than
    leaving compose to its own defaults."""
    monkeypatch.delenv("COMPOSE_HOST_ROOT", raising=False)
    monkeypatch.delenv("UPDATER_HOST_WORKSPACE", raising=False)
    monkeypatch.delenv("UPDATER_HOST_STATE", raising=False)

    env = executor._compose_env("v9.9.9")

    assert env == {"FH_TAG": "v9.9.9"}


def test_the_shim_forwards_the_paths_it_already_knows():
    """Belt and braces on the other side of the same bug: the shim holds the
    real host paths, so it should hand them over rather than let the executor
    reconstruct them."""
    shim = (
        Path(__file__).resolve().parents[2] / "docker" / "updater-shim" / "shim.sh"
    ).read_text()
    assert '-e "UPDATER_HOST_WORKSPACE=$HOST_WORKSPACE"' in shim
    assert '-e "UPDATER_HOST_STATE=$HOST_STATE"' in shim
