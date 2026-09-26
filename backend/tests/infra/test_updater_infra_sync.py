"""The updater's infra sync: fast-forward the host checkout to the release and
recreate only the infra services (db, redis, clamav, tusd) whose definition
changed.

The git half runs against a REAL repository in the temp workspace - the
classification (at / behind / ahead / diverged, dirty overlap, shallow, fetch)
is exactly what a fake would get wrong. Docker is faked: `docker compose config`
is stood in for by a docker-compose.yml that holds the config JSON itself, so a
release commit that edits the file changes the "config" the way it would.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXECUTOR = ROOT / "docker" / "updater-executor" / "run.py"
SHIM = ROOT / "docker" / "updater-shim" / "shim.sh"
TAG = "v1.1.0"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *args: str) -> str:
    # S603/S607: the test's own git invocations against a temp repository.
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],  # noqa: S607
        capture_output=True, text=True, check=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_GIT_ENV},
    )
    return result.stdout.strip()


def base_config(ws: Path) -> dict:
    return {
        "services": {
            "db": {
                "image": "mariadb:11.8.3",
                "volumes": [
                    {"type": "bind", "source": f"{ws}/data/db", "target": "/var/lib/mysql"},
                    {"type": "bind", "source": f"{ws}/docker/mariadb/init.sh",
                     "target": "/docker-entrypoint-initdb.d/init.sh"},
                ],
                "healthcheck": {"interval": "10s", "retries": 5, "start_period": "1m0s"},
            },
            "redis": {"image": "redis:7.4.5-alpine3.22", "command": ["redis-server", "--appendonly", "yes"]},
            "clamav": {
                "image": "clamav/clamav:1.5.3",
                "volumes": [{"type": "bind", "source": f"{ws}/docker/clamav/clamd.conf",
                             "target": "/etc/clamav/clamd.conf"}],
                "healthcheck": {"interval": "30s", "retries": 5, "start_period": "3m0s"},
            },
            "tusd": {"image": "tusproject/tusd:v2.9.2", "command": ["-hooks-http", "x"]},
            "backend": {"image": "ghcr.io/phoen-ix/fileheron-backend:v1.0.0"},
        }
    }


class Host:
    """A temp checkout plus the docker state the fakes report."""

    def __init__(self, executor, monkeypatch):
        self.ex = executor
        self.ws: Path = executor.WORKSPACE
        cfg = base_config(self.ws)
        self.running = {s: cfg["services"][s]["image"] for s in executor.INFRA_SYNC_ORDER}
        self.missing: set[str] = set()
        monkeypatch.setattr(executor, "_compose_config", self._compose_config)
        monkeypatch.setattr(executor, "_container_id", self._container_id)
        monkeypatch.setattr(executor, "_inspect", self._inspect)

    # --- docker fakes ---
    def _compose_config(self, path, _tag):
        text = Path(path).read_text()
        if "BROKEN" in text:
            return None, "required variable NEW_VAR is not set in .env"
        return json.loads(text), ""

    def _container_id(self, svc):
        return None if svc in self.missing else f"cid-{svc}"

    def _inspect(self, cid, _fmt):
        return self.running.get(cid.removeprefix("cid-"))

    # --- repo helpers ---
    def init(self, where: Path | None = None) -> str:
        ws = where or self.ws
        ws.mkdir(parents=True, exist_ok=True)
        git(ws, "init", "-q", "-b", "main")
        self.write("docker-compose.yml", json.dumps(base_config(self.ws), indent=1), ws)
        self.write("docker/clamav/clamd.conf", "MaxFileSize 30G\n", ws)
        self.write("docker/mariadb/init.sh", "#!/bin/sh\n", ws)
        self.write("README.md", "hi\n", ws)
        git(ws, "add", "-A")
        git(ws, "commit", "-q", "-m", "base")
        return git(ws, "rev-parse", "HEAD")

    def write(self, rel: str, text: str, ws: Path | None = None) -> None:
        p = (ws or self.ws) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def edit_config(self, fn, ws: Path | None = None) -> None:
        path = (ws or self.ws) / "docker-compose.yml"
        cfg = json.loads(path.read_text())
        fn(cfg["services"])
        path.write_text(json.dumps(cfg, indent=1))

    def commit(self, msg: str, ws: Path | None = None) -> str:
        ws = ws or self.ws
        git(ws, "add", "-A")
        git(ws, "commit", "-q", "-m", msg)
        return git(ws, "rev-parse", "HEAD")

    def release(self, change=None, *, files: dict[str, str] | None = None, stay: bool = False) -> str:
        """Commit a release on top of HEAD and tag it; HEAD goes back to where
        it was unless `stay`, which leaves the checkout BEHIND the tag."""
        before = git(self.ws, "rev-parse", "HEAD")
        if change:
            self.edit_config(change)
        for rel, text in (files or {}).items():
            self.write(rel, text)
        sha = self.commit("release")
        git(self.ws, "tag", TAG)
        if not stay:
            git(self.ws, "reset", "-q", "--hard", before)
        self.ex.RELEASE_SHA = sha
        return sha

    def plan(self, **opts):
        options = {**self.ex._OPTION_DEFAULTS, **opts}
        return self.ex.plan_infra(TAG, options, "v1.0.0")

    def warnings(self) -> list[str]:
        if not self.ex.STATE_FILE.exists():
            return []
        return json.loads(self.ex.STATE_FILE.read_text()).get("warnings", [])


def bump_db_and_redis(s):
    s["db"]["image"] = "mariadb:12.3.3"
    s["redis"]["image"] = "redis:8.10.2-alpine3.23"


@pytest.fixture
def host(executor, monkeypatch):
    executor.STATE_FILE.write_text(json.dumps({"id": "j", "status": "pulling"}))
    return Host(executor, monkeypatch)


# --- the plan ---------------------------------------------------------------------


def test_behind_with_new_images_plans_exactly_those_services_and_a_fast_forward(host):
    host.init()
    sha = host.release(bump_db_and_redis)
    plan = host.plan()
    assert plan.skip_reason is None
    assert plan.services == ["db", "redis"]
    assert plan.images == {"db": "mariadb:12.3.3", "redis": "redis:8.10.2-alpine3.23"}
    assert plan.ff_commit == sha
    # an image bump is not also reported as a definition change
    assert plan.reasons["db"] == "image mariadb:11.8.3 -> mariadb:12.3.3"
    # the budget comes from the healthcheck, floored for a MariaDB major upgrade
    assert plan.budgets["db"] == 900


def test_a_command_change_alone_recreates_only_that_service(host):
    host.init()
    host.release(lambda s: s["tusd"].update(command=["-hooks-http", "x", "-progress-hooks-interval=30s"]))
    plan = host.plan()
    assert plan.services == ["tusd"]
    assert "definition changed" in plan.reasons["tusd"]


def test_a_changed_bind_mounted_file_recreates_its_service(host):
    host.init()
    host.release(files={"docker/clamav/clamd.conf": "MaxFileSize 2G\n"})
    plan = host.plan()
    assert plan.services == ["clamav"]
    assert "docker/clamav/clamd.conf" in plan.reasons["clamav"]
    assert plan.budgets["clamav"] >= 600


def test_an_initdb_script_change_does_not_recreate_a_running_database(host):
    host.init()
    host.release(files={"docker/mariadb/init.sh": "#!/bin/sh\necho changed\n"})
    plan = host.plan()
    assert plan.services == []
    assert plan.ff_commit is not None, "the checkout still fast-forwards"


def test_at_the_tag_only_image_drift_counts(host):
    """The operator pulled the release by hand but never ran `up`."""
    host.init()
    host.release(bump_db_and_redis, stay=True)
    plan = host.plan()
    assert plan.ff_commit is None
    assert plan.services == ["db", "redis"]


def test_ahead_without_infra_changes_syncs_from_the_checkout(host):
    host.init()
    host.release(bump_db_and_redis, stay=True)
    host.write("README.md", "newer docs\n")
    host.commit("docs on main")
    plan = host.plan()
    assert plan.skip_reason is None and plan.ff_commit is None
    assert plan.services == ["db", "redis"]


def test_ahead_with_unreleased_infra_changes_is_skipped(host):
    host.init()
    host.release(stay=True, files={"README.md": "release\n"})
    host.edit_config(lambda s: s["redis"].update(image="redis:9.0.0-alpine"))
    host.commit("unreleased redis bump on main")
    plan = host.plan()
    assert plan.services == [] and "ahead" in plan.skip_reason
    assert any("git fetch --tags && git merge --ff-only v1.1.0" in w for w in host.warnings())


def test_a_diverged_checkout_is_skipped(host):
    host.init()
    host.release(bump_db_and_redis)
    host.write("README.md", "local commit\n")
    host.commit("local")
    plan = host.plan()
    assert plan.services == [] and "diverged" in plan.skip_reason


def test_local_edits_the_release_would_overwrite_skip_the_sync(host):
    host.init()
    host.release(bump_db_and_redis)
    host.edit_config(lambda s: s["db"].update(mem_limit="4g"))  # uncommitted site edit
    plan = host.plan()
    assert plan.services == [] and "docker-compose.yml" in plan.skip_reason


def test_local_edits_elsewhere_do_not_block_the_sync(host):
    host.init()
    host.release(bump_db_and_redis)
    host.write("README.md", "local notes\n")  # uncommitted, untouched by the release
    assert host.plan().services == ["db", "redis"]


@pytest.mark.parametrize("name", ["docker-compose.override.yml", "compose.override.yaml"])
def test_an_override_file_skips_the_sync(host, name):
    host.init()
    host.release(bump_db_and_redis)
    host.write(name, "services: {}\n")
    plan = host.plan()
    assert plan.services == [] and name in plan.skip_reason


def test_compose_file_in_env_skips_the_sync(host):
    host.init()
    host.release(bump_db_and_redis)
    host.write(".env", "FH_TAG=v1.0.0\nCOMPOSE_FILE=a.yml:b.yml\n")
    assert "COMPOSE_FILE" in host.plan().skip_reason


def test_not_a_git_checkout_skips_the_sync(host):
    host.ex.RELEASE_SHA = "a" * 40
    assert "not a git checkout" in host.plan().skip_reason


def test_a_shallow_clone_skips_the_sync(host, tmp_path):
    src = tmp_path / "src"
    host.init(src)
    git(src, "tag", TAG)
    git(host.ws, "clone", "-q", "--depth", "1", f"file://{src}", ".")
    host.ex.RELEASE_SHA = git(src, "rev-parse", "HEAD")
    assert "shallow" in host.plan().skip_reason


def test_a_tag_that_is_not_the_release_commit_skips_the_sync(host):
    host.init()
    host.release(bump_db_and_redis)
    host.ex.RELEASE_SHA = "b" * 40
    assert "not the commit this release was built from" in host.plan().skip_reason


def test_an_image_without_a_release_commit_skips_the_sync(host):
    host.init()
    host.release(bump_db_and_redis)
    host.ex.RELEASE_SHA = "unknown"
    assert "release commit" in host.plan().skip_reason


def test_a_release_compose_that_does_not_resolve_skips_before_any_change(host):
    host.init()
    host.release(lambda s: s["db"].update(environment={"X": "BROKEN"}))
    head = git(host.ws, "rev-parse", "HEAD")
    plan = host.plan()
    assert plan.ff_commit is None and "NEW_VAR" in plan.skip_reason
    assert git(host.ws, "rev-parse", "HEAD") == head


def test_switched_off_skips_quietly(host):
    host.init()
    host.release(bump_db_and_redis)
    plan = host.plan(infra_sync=False)
    assert plan.services == [] and plan.skip_reason == "switched off"
    assert host.warnings() == []


def test_a_service_without_a_container_is_left_alone(host):
    host.init()
    host.release(bump_db_and_redis)
    host.missing.add("redis")
    assert host.plan().services == ["db"]
    assert any("redis has no container" in w for w in host.warnings())


def test_the_release_tag_is_fetched_when_the_checkout_lacks_it(host, tmp_path):
    host.init()
    sha = host.release(bump_db_and_redis)
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(host.ws), str(bare))
    git(host.ws, "tag", "-d", TAG)
    git(host.ws, "remote", "add", "origin", str(bare))
    plan = host.plan()
    assert plan.ff_commit == sha and plan.services == ["db", "redis"]


def test_a_path_the_owner_cannot_write_skips_the_sync(host):
    host.init()
    host.release(files={"docker/clamav/extra.conf": "x\n"})
    (host.ws / "docker" / "clamav").chmod(0o555)
    try:
        plan = host.plan()
    finally:
        (host.ws / "docker" / "clamav").chmod(0o755)
    assert plan.ff_commit is None and "not writable" in plan.skip_reason


def test_the_fast_forward_moves_the_checkout_to_the_release(host):
    host.init()
    sha = host.release(bump_db_and_redis, files={"docker/clamav/clamd.conf": "MaxFileSize 2G\n"})
    plan = host.plan()
    assert host.ex.fast_forward_checkout(plan, TAG) is True
    assert git(host.ws, "rev-parse", "HEAD") == sha
    assert (host.ws / "docker/clamav/clamd.conf").read_text() == "MaxFileSize 2G\n"


def test_a_failed_fast_forward_is_a_warning_with_the_manual_command(host):
    host.init()
    host.release(bump_db_and_redis)
    plan = host.plan()
    host.write("docker-compose.yml", "{}")  # an edit made after planning
    assert host.ex.fast_forward_checkout(plan, TAG) is False
    assert any("could not be fast-forwarded" in w for w in host.warnings())


@pytest.mark.parametrize(
    ("ref", "normal"),
    [
        ("mariadb:12.3.3", "mariadb:12.3.3"),
        ("docker.io/library/mariadb:12.3.3", "mariadb:12.3.3"),
        ("index.docker.io/library/redis", "redis:latest"),
        ("clamav/clamav:1.5.4", "clamav/clamav:1.5.4"),
        ("registry.local:5000/x/y", "registry.local:5000/x/y:latest"),
        ("redis@sha256:abc", "redis@sha256:abc"),
    ],
)
def test_image_references_are_normalised(executor, ref, normal):
    assert executor._normalize_image(ref) == normal


def test_options_fall_back_to_the_defaults(executor):
    d = executor._OPTION_DEFAULTS
    assert executor._read_options({}) == d
    assert executor._read_options({"options": "yes"}) == d
    bad = {"backup": 1, "infra_sync": "true", "backup_keep": True, "backup_max_age_days": 99999}
    assert executor._read_options({"options": bad}) == d
    good = {"backup": True, "infra_sync": False, "backup_keep": 0, "backup_max_age_days": 7}
    assert executor._read_options({"options": good}) == {**d, **good}
    # the defaults that protect the update which INSTALLS this executor
    assert d["infra_sync"] is True and d["backup_on_db_change"] is True


# --- sync_data_layer / sync_remaining_infra -----------------------------------------


def _plan(ex, services):
    plan = ex.InfraPlan()
    for s in services:
        plan.services.append(s)
        plan.images[s] = f"{s}:new"
        plan.reasons[s] = "test"
        plan.budgets[s] = 1
    return plan


@pytest.fixture
def docker_calls(executor, monkeypatch):
    calls: list[list[str]] = []
    envs: list[dict | None] = []
    health: dict[str, bool] = {}

    def run_capture(cmd, env=None):
        calls.append(cmd)
        envs.append(env)
        return 0

    monkeypatch.setattr(executor, "run_capture", run_capture)
    monkeypatch.setattr(executor, "_container_id", lambda s: f"cid-{s}")
    monkeypatch.setattr(executor, "wait_service_healthy", lambda s, _i, _b: health.get(s, True))
    executor.STATE_FILE.write_text(json.dumps({"id": "j", "status": "restarting"}))
    return calls, health, envs


def _ups(calls):
    return [c for c in calls if "up" in c]


def test_the_data_layer_is_recreated_with_the_app_stopped(executor, docker_calls):
    calls, _, _ = docker_calls
    plan = _plan(executor, ["db", "redis", "clamav", "tusd"])
    assert executor.sync_data_layer(plan, "v1.0.0", "backups/pre-update/x") == 0
    assert calls[0] == ["docker", "stop", "-t", str(executor.APP_STOP_TIMEOUT_SEC),
                        "cid-backend", "cid-worker"]
    ups = _ups(calls)
    assert [c[-1] for c in ups] == ["db", "redis"], "clamav/tusd are not the data layer's to recreate"
    for c in ups:
        assert {"--no-deps", "--force-recreate"} <= set(c) and c[c.index("--pull") + 1] == "never"


def test_without_db_or_redis_the_data_layer_step_touches_nothing(executor, docker_calls):
    calls, _, _ = docker_calls
    assert executor.sync_data_layer(_plan(executor, ["clamav", "tusd"]), "v1.0.0", None) == 0
    assert calls == []


def test_clamav_and_tusd_are_recreated_with_the_app_running(executor, docker_calls):
    """The v2.19.0 update stopped the app, then waited 22s on clamav's
    healthcheck before starting it again - neither service is needed to serve a
    request. They are recreated after the new app, never stopping it."""
    calls, _, envs = docker_calls
    executor.sync_remaining_infra(_plan(executor, ["db", "redis", "clamav", "tusd"]), "v1.1.0")
    assert not any(c[:2] == ["docker", "stop"] for c in calls)
    ups = _ups(calls)
    assert [c[-1] for c in ups] == ["clamav", "tusd"], "db/redis belong to the data-layer step"
    for c in ups:
        assert {"--no-deps", "--force-recreate"} <= set(c) and c[c.index("--pull") + 1] == "never"
    # FH_TAG already names the release here; the compose run must agree with .env.
    assert {e["FH_TAG"] for c, e in zip(calls, envs, strict=True) if "up" in c} == {"v1.1.0"}


def test_a_failed_database_restarts_the_old_app_and_fails_the_job(executor, docker_calls):
    calls, health, _ = docker_calls
    health["db"] = False
    rc = executor.sync_data_layer(_plan(executor, ["db", "redis"]), "v1.0.0", "backups/pre-update/x")
    assert rc == 20
    assert ["docker", "start", "cid-backend", "cid-worker"] in calls
    assert not any(c[-1] == "redis" and "up" in c for c in calls), "stopped at the first failure"
    state = json.loads(executor.STATE_FILE.read_text())
    assert state["status"] == "failed"
    assert "backups/pre-update/x" in state["error"] and "v1.0.0" in state["error"]


def test_a_failed_clamav_is_a_warning(executor, docker_calls):
    calls, health, _ = docker_calls
    health["clamav"] = False
    executor.sync_remaining_infra(_plan(executor, ["clamav", "tusd"]), "v1.1.0")
    state = json.loads(executor.STATE_FILE.read_text())
    assert state["status"] == "restarting"
    assert any("clamav did not come up healthy" in w for w in state["warnings"])
    assert _ups(calls)[-1][-1] == "tusd", "a clamav failure must not skip tusd"


def _inspect_sequence(executor, monkeypatch, answers):
    """wait_service_healthy against a scripted series of `docker inspect` answers."""
    seq = iter(answers)
    last = answers[-1]
    monkeypatch.setattr(executor, "_container_id", lambda s: "cid")
    monkeypatch.setattr(executor, "_inspect", lambda _c, _f: next(seq, last))
    monkeypatch.setattr(executor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(executor, "run_capture", lambda cmd, env=None: 0)
    executor.STATE_FILE.write_text(json.dumps({"id": "j", "status": "restarting"}))


def test_a_healthy_service_on_the_release_image_passes(executor, monkeypatch):
    _inspect_sequence(executor, monkeypatch, [
        "running|starting|mariadb:12.3.3|0",
        "running|healthy|mariadb:12.3.3|0",
    ])
    assert executor.wait_service_healthy("db", "mariadb:12.3.3", 60) is True


def test_healthy_on_the_old_image_is_not_success(executor, monkeypatch):
    _inspect_sequence(executor, monkeypatch, ["running|healthy|mariadb:11.8.3|0"])
    import itertools

    clock = itertools.count(0, 10)
    monkeypatch.setattr(executor.time, "time", lambda: next(clock))
    assert executor.wait_service_healthy("db", "mariadb:12.3.3", 60) is False


def test_a_crash_loop_fails_fast_instead_of_waiting_out_the_budget(executor, monkeypatch):
    _inspect_sequence(executor, monkeypatch, [
        "running|starting|mariadb:12.3.3|0",
        "restarting||mariadb:12.3.3|1",
        "restarting||mariadb:12.3.3|3",
    ])
    started = executor.time.time()
    assert executor.wait_service_healthy("db", "mariadb:12.3.3", 900) is False
    assert executor.time.time() - started < 5


# --- main(): ordering ---------------------------------------------------------------


@pytest.fixture
def flow(executor, monkeypatch):
    """main() with every side effect recorded in order."""
    events: list[str] = []
    executor.ENV_FILE.write_text("FH_TAG=v1.0.0\n")
    executor.STATE_FILE.write_text(json.dumps(
        {"id": "j", "action": "update", "target_tag": TAG, "status": "claiming", "options": {}}
    ))
    monkeypatch.setattr(executor, "capture_alembic_head", lambda: "abc123")
    monkeypatch.setattr(executor, "_pull", lambda ref, quiet=False: events.append(f"pull {ref}") or 0)

    def run_capture(cmd, env=None):
        if "up" in cmd:
            events.append("up " + cmd[-1])
        return 0

    monkeypatch.setattr(executor, "run_capture", run_capture)
    real_write_tag = executor.write_current_tag
    monkeypatch.setattr(executor, "write_current_tag",
                        lambda t: events.append(f"tag {t}") or real_write_tag(t))
    monkeypatch.setattr(executor, "prune_pre_update_backups", lambda *a, **k: events.append("prune"))
    state = {"plan": executor.InfraPlan(), "backup": "backups/pre-update/b", "ff": True, "sync": 0,
             "health": True}
    monkeypatch.setattr(executor, "wait_for_backend_health",
                        lambda *a, **k: events.append("health") or state["health"])

    def plan_infra(*_a):
        events.append("plan")
        return state["plan"]

    monkeypatch.setattr(executor, "plan_infra", plan_infra)
    monkeypatch.setattr(executor, "take_pre_update_backup",
                        lambda *a: events.append("backup") or state["backup"])
    monkeypatch.setattr(executor, "fast_forward_checkout",
                        lambda *a: events.append("ff") or state["ff"])
    monkeypatch.setattr(executor, "sync_data_layer",
                        lambda *a: events.append("sync data") or state["sync"])

    def sync_remaining_infra(*_a):
        # The job must still be in flight: the SPA and the shim treat `healthy`
        # as done.
        events.append("sync rest while " + json.loads(executor.STATE_FILE.read_text())["status"])

    monkeypatch.setattr(executor, "sync_remaining_infra", sync_remaining_infra)
    monkeypatch.setattr(executor, "auto_rollback", lambda *a, **k: events.append("auto_rollback") or 7)
    return events, state


def _synced(events) -> bool:
    return any(e.startswith("sync") for e in events)


def _options(executor, **opts):
    job = json.loads(executor.STATE_FILE.read_text())
    job["options"] = opts
    executor.STATE_FILE.write_text(json.dumps(job))


def test_a_database_change_forces_a_backup_before_anything_changes(executor, flow):
    events, state = flow
    plan = _plan(executor, ["db"])
    plan.ff_commit = "c" * 40
    state["plan"] = plan
    _options(executor, backup=False)
    assert executor.main() == 0
    order = [e for e in events if not e.startswith("pull")]
    assert order == ["plan", "backup", "prune", "ff", "sync data", f"tag {TAG}",
                     "up frontend", "health", "up updater-shim"]
    assert events.index("pull db:new") < events.index("backup")
    assert json.loads(executor.STATE_FILE.read_text())["backup_dir"] == "backups/pre-update/b"


def test_clamav_and_tusd_wait_until_the_new_app_is_verified(executor, flow):
    events, state = flow
    plan = _plan(executor, ["db", "redis", "clamav", "tusd"])
    plan.ff_commit = "c" * 40
    state["plan"] = plan
    assert executor.main() == 0
    order = [e for e in events if not e.startswith("pull")]
    assert order == ["plan", "backup", "prune", "ff", "sync data", f"tag {TAG}",
                     "up frontend", "health", "sync rest while restarting", "up updater-shim"]
    job = json.loads(executor.STATE_FILE.read_text())
    assert job["status"] == "healthy"


def test_clamav_alone_never_runs_the_data_layer_step(executor, flow):
    events, state = flow
    state["plan"] = _plan(executor, ["clamav"])
    assert executor.main() == 0
    assert "sync data" not in events
    assert events.index("health") < events.index("sync rest while restarting")


def test_a_failed_app_start_rolls_back_without_touching_clamav_or_tusd(executor, flow):
    events, state = flow
    state["plan"], state["health"] = _plan(executor, ["db", "clamav", "tusd"]), False
    assert executor.main() == 7
    assert "auto_rollback" in events
    assert not any(e.startswith("sync rest") for e in events)


def test_no_forced_backup_when_the_setting_is_off(executor, flow):
    events, state = flow
    state["plan"] = _plan(executor, ["db"])
    _options(executor, backup=False, backup_on_db_change=False)
    assert executor.main() == 0
    assert "backup" not in events and "prune" in events


def test_the_checkbox_alone_takes_a_backup(executor, flow):
    events, _ = flow
    _options(executor, backup=True)
    assert executor.main() == 0
    assert "backup" in events and not _synced(events)


def test_a_failed_backup_changes_nothing(executor, flow):
    events, state = flow
    plan = _plan(executor, ["db"])
    plan.ff_commit = "c" * 40
    state["plan"], state["backup"] = plan, None
    assert executor.main() == 6
    assert not {"ff", "prune"} & set(events) and not _synced(events)
    assert not any(e.startswith(("tag", "up")) for e in events)
    assert executor.read_current_tag() == "v1.0.0"
    assert json.loads(executor.STATE_FILE.read_text())["status"] == "failed"
    assert not executor.ROLLBACK_FILE.exists()


def test_a_failed_fast_forward_still_updates_the_app(executor, flow):
    events, state = flow
    plan = _plan(executor, ["db"])
    plan.ff_commit = "c" * 40
    state["plan"], state["ff"] = plan, False
    assert executor.main() == 0
    assert not _synced(events) and "up frontend" in events


def test_a_failed_data_layer_leaves_the_tag_and_rollback_target_alone(executor, flow):
    events, state = flow
    state["plan"], state["sync"] = _plan(executor, ["db"]), 20
    assert executor.main() == 20
    assert not any(e.startswith(("tag", "up")) for e in events)
    assert executor.read_current_tag() == "v1.0.0"
    assert not executor.ROLLBACK_FILE.exists()


def test_a_rollback_never_touches_git_infra_or_backups(executor, flow):
    events, _ = flow
    executor.ROLLBACK_FILE.write_text(json.dumps({"tag": "v0.9.0", "alembic_head": None}))
    job = json.loads(executor.STATE_FILE.read_text())
    job.update(action="rollback", target_tag="v0.9.0", options={"backup": True})
    executor.STATE_FILE.write_text(json.dumps(job))
    assert executor.main() == 0
    assert not {"plan", "backup", "prune", "ff"} & set(events) and not _synced(events)


# --- pins ---------------------------------------------------------------------------


def test_every_status_the_executor_writes_is_one_the_shim_knows():
    """The shim that supervises an update is the PREVIOUS release's. An unknown
    status lands in its `*)` arm - logged, never swept, never stuck-checked -
    and after the executor exits it is overwritten as a crash. Progress detail
    goes in `phase` instead."""
    shim = SHIM.read_text(encoding="utf-8")
    known = set()
    for arm in re.findall(r"^\s*([a-z_|\"]+)\)\s*$", shim, re.M):
        known |= {s.strip('"') for s in arm.split("|")}
    written = set(re.findall(r'status="([a-z_]+)"', EXECUTOR.read_text(encoding="utf-8")))
    assert written, "no status literal found in run.py"
    assert written <= known, f"statuses the shim does not know: {written - known}"


@pytest.mark.parametrize("locale", ["en", "de"])
def test_every_phase_the_executor_sets_has_a_label(locale):
    """AdminSystem renders `admin_system.update.phase.<phase>` only when the key
    exists (`te`), so a phase without a label shows nothing, silently."""
    phases = set(re.findall(r'set_phase\("([a-z_]+)"\)', EXECUTOR.read_text(encoding="utf-8")))
    assert "syncing_services" in phases, "the scan found no set_phase literal"
    path = ROOT / "frontend" / "src" / "i18n" / "locales" / f"{locale}.json"
    labels = json.loads(path.read_text(encoding="utf-8"))["admin_system"]["update"]["phase"]
    assert phases <= set(labels), f"phases without a {locale} label: {phases - set(labels)}"


def test_the_infra_list_is_separate_and_exact(executor):
    assert executor.INFRA_SYNC_ORDER == ("db", "redis", "clamav", "tusd")
    assert not set(executor.INFRA_SYNC_ORDER) & set(executor.SERVICES)
    assert "updater-shim" not in executor.INFRA_SYNC_ORDER
    assert {"db", "redis"} == executor.DATA_SERVICES


def test_the_compose_config_output_is_never_logged():
    """It carries every secret in .env."""
    tree = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_compose_config")
    called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"log_line", "run_capture", "print", "add_warning"}
