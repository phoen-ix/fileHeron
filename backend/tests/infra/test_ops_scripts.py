"""Container lifecycle in the shipped scripts and workflows.

A `docker run -d` without `--rm`, torn down later with a `docker rm -f` that
has no `-v`, strands the image's anonymous volumes forever. For `mariadb:11`
(`VOLUME /var/lib/mysql`) and `redis:7-alpine` (`VOLUME /data`) that is ~167 MB
and ~a dataset per invocation.

That is not hypothetical here. On 2026-08-23 the reference host was found
accumulating ~1 GB/day of stranded MariaDB datadirs. The generator was not a
scheduled job and not any script in this repo - it was a `docker run -d --name
… mariadb:11` typed by hand, once per session, because the three
`RUN_ALEMBIC_ROUNDTRIP` files need a real database and **the repo offered no
supported local way to get one**. Their docstrings said "point ``DB_*`` at a
throwaway MariaDB" and stopped there, so the throwaway was re-invented every
time, and the re-invented one leaked. `scripts/run_mariadb_tests.sh` is the
supported path; this file is what stops the next one drifting back.

The same survey found the identical shape twice more, which is why the scans
below are generic rather than a list of the three sites known today:

* `server-release.yml`'s boot-smoke removed its containers without `-v` and
  never removed the network it created at all - free on an ephemeral GitHub
  runner, the same ~1 GB/day on a self-hosted one.
* `CONTRIBUTING.md`'s e2e recipe omitted `COMPOSE_PROJECT_NAME`, so run verbatim
  from a `fileHeron/` checkout it recreated the LIVE compose project with
  `AV_SKIP=true` and seeded accounts.

Each scan asserts it matched something. A structural test whose pattern has
quietly stopped matching passes forever and pins nothing - the failure this
repo already recorded for `test_wrong_secret_routes.py`, whose first version
sliced a file backwards and examined an empty string.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _ROOT / "scripts"
_WORKFLOWS = _ROOT / ".github" / "workflows"

# Images that declare a VOLUME, so a container of one that is removed without
# -v leaves an anonymous volume behind.
_VOLUME_IMAGES = ("mariadb", "redis", "postgres", "mysql")


def _shell_sources() -> list[Path]:
    return sorted(list(_SCRIPTS.rglob("*.sh")) + list(_WORKFLOWS.glob("*.yml")))


def _detached_runs(text: str) -> list[str]:
    """Every `docker run -d …` invocation, flattened onto one line each."""
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [
        m.group(0)
        for m in re.finditer(r"docker run\b[^\n]*?\s-d\b[^\n]*", joined)
    ]


def test_the_scan_finds_the_detached_runs_it_is_meant_to_police() -> None:
    """Vacuity guard: if this hits zero, every assertion below is free."""
    found = [(p, r) for p in _shell_sources() for r in _detached_runs(p.read_text())]
    assert len(found) >= 3, f"the `docker run -d` scan matched {len(found)} sites"


@pytest.mark.parametrize("path", _shell_sources(), ids=lambda p: p.name)
def test_a_detached_container_is_either_self_removing_or_removed_with_v(
    path: Path,
) -> None:
    text = path.read_text()
    offenders = []
    for run in _detached_runs(text):
        if " --rm" in run:
            continue
        # No --rm: the teardown must then carry -v, or the anonymous volume of
        # any VOLUME-declaring image outlives the container.
        if re.search(r"docker rm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*v", text):
            continue
        if not any(img in run for img in _VOLUME_IMAGES):
            continue
        offenders.append(run.strip()[:120])
    assert not offenders, (
        f"{path.relative_to(_ROOT)}: detached container of a VOLUME-declaring image "
        f"with neither --rm nor a `docker rm -v` teardown:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", _shell_sources(), ids=lambda p: p.name)
def test_every_created_network_is_removed_again(path: Path) -> None:
    text = path.read_text()
    created = set(re.findall(r"docker network create\s+(?:-[^\s]+\s+)*([\w\"$\{\}-]+)", text))
    if not created:
        pytest.skip("creates no networks")
    for name in created:
        assert re.search(rf"docker network rm\s+{re.escape(name)}", text), (
            f"{path.relative_to(_ROOT)}: creates network {name} and never removes it"
        )


def test_the_local_mariadb_runner_exists_and_cleans_up() -> None:
    """The supported path the test docstrings now point at.

    Both mechanisms are required and neither is redundant: `--rm` covers the
    normal exit but not a killed container or a daemon restart, and the trap's
    `docker rm -f -v` is the only thing that takes the datadir with it.
    """
    runner = _SCRIPTS / "run_mariadb_tests.sh"
    assert runner.exists(), "scripts/run_mariadb_tests.sh is gone; make test-mariadb is broken"
    text = runner.read_text()
    assert "docker run -d --rm" in text
    assert "docker rm -f -v" in text
    assert re.search(r"^trap teardown EXIT$", text, re.M), "teardown is not trapped"
    assert "docker network rm" in text
    # Readiness, not a sleep: the drill's PING-loop defect, one service over.
    assert "innodb_initialized" in text, "readiness must be mariadb's own healthcheck"


def test_the_makefile_target_points_at_the_runner() -> None:
    makefile = (_ROOT / "Makefile").read_text()
    assert "test-mariadb:" in makefile
    assert "scripts/run_mariadb_tests.sh" in makefile


def test_restore_and_the_drill_agree_on_redis_readiness() -> None:
    """v2.13.1 fixed three redis defects in the drill and not in restore.sh.

    The drill is a control; `restore.sh` is the path an operator actually runs
    in an emergency. Hardening only the copy that is exercised weekly is how
    the real one stayed broken - the same "applied to the surfaces someone
    thought of" shape this repo recorded for the recipient-roster rule.
    """
    restore = (_SCRIPTS / "restore.sh").read_text()
    drill = (_SCRIPTS / "restore_drill_e2e.sh").read_text()
    for name, text in (("restore.sh", restore), ("restore_drill_e2e.sh", drill)):
        assert "aof_rewrite_in_progress" in text, (
            f"{name}: waits on aof_last_bgrewrite_status alone, which reads `ok` "
            "before any rewrite has run and cannot observe the one it names"
        )
        assert re.search(r"redis-cli DBSIZE", text), f"{name}: no DBSIZE readiness poll"
        assert "CONFIG SET appendonly yes" in text
        assert 'redis-cli CONFIG SET appendonly yes > /dev/null' not in text, (
            f"{name}: discards CONFIG SET's reply - redis-cli exits 0 on an error reply"
        )
    assert "trap loader_down EXIT" in restore, (
        "restore.sh: the redis loader holds ./data/redis and `set -e` can abort "
        "between start and shutdown, stranding it mid-restore"
    )


def test_the_drill_waits_for_mariadbs_own_healthcheck_not_a_bare_select() -> None:
    """The drill builds a FRESH datadir every run, so mariadb's entrypoint first
    runs a TEMPORARY initialisation server that accepts connections and is then
    shut down before the real one starts.

    A gate that breaks out of its retry loop on the first successful `SELECT 1`
    can break against THAT server and find the socket gone one line later. That
    is how the 2026-09-13 drill failed two seconds into a 120s budget with
    "throwaway db never came up", while the identical script passed on
    2026-09-06 - the race is timing-dependent, so one green run proves nothing.
    Same failure family as the redis PING-loop defect above: a probe that passes
    against a server that is not the one you are about to use.
    `run_mariadb_tests.sh` already waits on `innodb_initialized` (pinned by
    test_the_mariadb_runner_cleans_up_after_itself); this keeps the two in step.
    """
    drill = (_SCRIPTS / "restore_drill_e2e.sh").read_text()
    assert "innodb_initialized" in drill, (
        "the drill's db readiness must be mariadb's own healthcheck, not a bare SELECT"
    )
    assert not re.search(r'mariadb -uroot -e "SELECT 1" >/dev/null 2>&1 && break', drill), (
        "the drill breaks out of its readiness loop on the first SELECT again - "
        "that can be the init server, which is then shut down"
    )
    assert re.search(r'\[ "\$db_ready" = "1" \] \|\| fail', drill), (
        "the drill must fail only once the retry budget is exhausted, never on a "
        "single un-retried re-test after the loop"
    )


def test_contributing_e2e_recipe_cannot_recreate_the_live_stack() -> None:
    """Compose defaults its project name to the directory - i.e. `fileheron`.

    Without COMPOSE_PROJECT_NAME the documented e2e command recreates the
    running production containers with AV_SKIP=true, ENVIRONMENT=development,
    COOKIE_SECURE=false and two seeded accounts with published credentials.
    """
    text = (_ROOT / "CONTRIBUTING.md").read_text()
    block = re.search(
        r"### End-to-end.*?(?=\n## )", text, re.S
    )
    assert block, "the e2e section of CONTRIBUTING.md moved; re-point this test"
    body = block.group(0)
    assert "docker-compose.e2e.yml" in body, "vacuity guard: matched the wrong block"
    assert "COMPOSE_PROJECT_NAME" in body, (
        "CONTRIBUTING's e2e recipe omits COMPOSE_PROJECT_NAME, so run verbatim it "
        "recreates the live compose project with the e2e overrides"
    )
    # Without a teardown line the obvious next step is a bare `docker compose
    # down`, which - for the same reason - stops production.
    assert re.search(r"down\s+-v", body), (
        "CONTRIBUTING's e2e section gives no teardown, so the natural follow-up "
        "is a bare `docker compose down` against whatever project is default"
    )


# --- audit 2026-09-24: an offsite copy that was configured and not written ----


def _offsite_section(dest: Path) -> str:
    """The real text of backup.sh's offsite push plus its closing verdict, run
    under bash with a stub `restic` - behaviour, not a grep."""
    src = (_ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    push = src[src.index("# 6. Optional restic push"):src.index("# 7. Restic forget")]
    verdict = src[src.index('echo "[backup] done - $DEST"'):]
    return f"set -euo pipefail\nDEST={dest}\nSTAMP=stamp\n" + push + verdict


def _run_offsite(tmp_path: Path, env: dict[str, str], restic_exit: int | None):
    import os
    import shutil
    import subprocess

    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    dest = tmp_path / "backup"
    dest.mkdir()
    (dest / "manifest.txt").write_text("x")
    if restic_exit is not None:
        stub = bindir / "restic"
        stub.write_text(f"#!/bin/sh\nexit {restic_exit}\n")
        stub.chmod(0o755)
    # S603: argv is an absolute bash plus this repo's own script text - no
    # untrusted input. Same shape as tests/infra/test_deploy_scripts.py.
    bash = shutil.which("bash")
    assert bash, "bash is required to exercise backup.sh"
    return subprocess.run(  # noqa: S603
        [bash, "-c", _offsite_section(dest)],
        env={"PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}", **env},
        capture_output=True, text=True, timeout=30,
    )


def test_an_unconfigured_offsite_is_announced_not_failed(tmp_path: Path) -> None:
    r = _run_offsite(tmp_path, {}, restic_exit=None)
    assert r.returncode == 0, r.stderr
    assert "local-only" in r.stdout


def test_a_configured_offsite_without_a_password_fails_the_run(tmp_path: Path) -> None:
    """It used to print one stderr line and exit 0, so OnFailure= never fired."""
    r = _run_offsite(tmp_path, {"BACKUP_RESTIC_REPO": "/tmp/repo"}, restic_exit=0)
    assert r.returncode == 1
    assert "no offsite copy" in r.stderr


def test_a_configured_offsite_without_restic_fails_the_run(tmp_path: Path) -> None:
    import shutil

    if shutil.which("restic"):
        pytest.skip("restic is installed here; the stub PATH cannot hide it")
    r = _run_offsite(
        tmp_path,
        {"BACKUP_RESTIC_REPO": "/tmp/repo", "BACKUP_RESTIC_PASSWORD": "pw"},
        restic_exit=None,
    )
    assert r.returncode == 1


def test_a_failed_push_fails_the_run_and_a_good_one_passes(tmp_path: Path) -> None:
    env = {"BACKUP_RESTIC_REPO": "/tmp/repo", "BACKUP_RESTIC_PASSWORD": "pw"}
    bad = _run_offsite(tmp_path / "bad", env, restic_exit=1)
    assert bad.returncode == 1
    assert "push failed" in bad.stderr
    good = _run_offsite(tmp_path / "good", env, restic_exit=0)
    assert good.returncode == 0, good.stderr


def test_local_retention_runs_before_the_push() -> None:
    """A failed push used to abort the run before retention, so a restic outage
    left one more full copy of data/ on the data disk every night."""
    src = (_ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    assert src.index("# 5. Local retention") < src.index("# 6. Optional restic push")


# --- 2026-09-25: the drill ignored a caller's FH_TAG ------------------------


def _drill_env_section() -> str:
    """The real text of the drill's .env loading, from its header comment up to
    the isolated-environment block - run under bash, not grepped."""
    src = (_ROOT / "scripts" / "restore_drill_e2e.sh").read_text(encoding="utf-8")
    start = src.index("# --- load secrets/config from .env")
    end = src.index("# --- isolated environment for every compose call")
    return (
        'set -euo pipefail\nlog() { echo "[drill] $*"; }\n'
        + src[start:end]
        + 'bash -c \'echo "CHILD_SEES=${FH_TAG-unset}"\'\n'
    )


def _run_drill_env(tmp_path: Path, dotenv: str, env: dict[str, str]):
    import os
    import shutil
    import subprocess

    (tmp_path / ".env").write_text(dotenv)
    # S603: argv is an absolute bash plus this repo's own script text - no
    # untrusted input. Same shape as the offsite tests above.
    bash = shutil.which("bash")
    assert bash, "bash is required to exercise restore_drill_e2e.sh"
    r = subprocess.run(  # noqa: S603
        [bash, "-c", _drill_env_section()],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


_DOTENV = "DB_ROOT_PASSWORD=x\nFH_TAG=v2.17.2\n"


def test_the_drill_honours_a_caller_fh_tag_over_dotenv(tmp_path: Path) -> None:
    """`FH_TAG=v2.18.0 scripts/restore_drill_e2e.sh` restored into v2.17.2 -
    the tag .env pinned - and passed, validating a schema head the release under
    test never had."""
    out = _run_drill_env(tmp_path, _DOTENV, {"FH_TAG": "v2.18.0"})
    assert "CHILD_SEES=v2.18.0" in out, out
    assert "drilling images FH_TAG=v2.18.0" in out, out


def test_the_drill_still_takes_dotenv_when_the_caller_says_nothing(tmp_path: Path) -> None:
    """The weekly timer sets no FH_TAG: it must keep drilling the version this
    host runs, which is .env's."""
    out = _run_drill_env(tmp_path, _DOTENV, {})
    assert "CHILD_SEES=v2.17.2" in out, out


def test_the_drill_defaults_like_compose(tmp_path: Path) -> None:
    """No tag anywhere, or an exported-but-empty one, resolves to `latest` -
    what compose's `${FH_TAG:-latest}` would run."""
    assert "CHILD_SEES=latest" in _run_drill_env(tmp_path, "DB_ROOT_PASSWORD=x\n", {})
    assert "CHILD_SEES=latest" in _run_drill_env(tmp_path, _DOTENV, {"FH_TAG": ""})


# --- restore.sh on a pre-update (database-only) backup ---------------------------
#
# The in-app updater's pre-update backup holds db.sql + redis.rdb and no tarballs.
# restore.sh used to wipe data/files FIRST and only then fail on the missing
# files.tar.gz - every upload destroyed on the way to an error. It now decides
# what the backup holds from the manifest, before the prompt and before `down`.
# These run the real script under bash with a stub `docker` that answers the
# redis loader's probes.

_STUB_DOCKER = """#!/bin/sh
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
  "compose ps -q redis") echo cid ;;
  *"redis-cli DBSIZE"*) echo 3 ;;
  *"CONFIG SET appendonly yes"*) echo OK ;;
  *"INFO persistence"*) printf 'aof_enabled:1\\naof_rewrite_in_progress:0\\naof_last_bgrewrite_status:ok\\n' ;;
esac
exit 0
"""


def _restore_fixture(tmp_path: Path, artifacts: dict[str, bytes]) -> tuple[Path, Path, dict[str, str]]:
    import hashlib
    import shutil

    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPTS / "restore.sh", root / "scripts" / "restore.sh")
    (root / ".env").write_text("DB_ROOT_PASSWORD=x\n")
    (root / "data" / "files").mkdir(parents=True)
    (root / "data" / "files" / "sentinel.bin").write_text("an upload\n")
    (root / "data" / "redis").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    lines = []
    for name, data in artifacts.items():
        (backup / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    (backup / "manifest.txt").write_text("".join(lines))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(_STUB_DOCKER)
    (bindir / "docker").chmod(0o755)
    log = tmp_path / "docker.log"
    log.write_text("")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "DOCKER_LOG": str(log)}
    return root, backup, env


def _run_restore(root: Path, backup: Path, env: dict[str, str]):
    import subprocess

    return subprocess.run(  # noqa: S603 - this repo's own script, stub docker on PATH
        ["/bin/bash", str(root / "scripts" / "restore.sh"), str(backup)],
        input="restore\n", env=env, capture_output=True, text=True, timeout=60,
    )


def _tarball(tmp_path: Path, tree: str, member: str) -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"restored\n"
        info = tarfile.TarInfo(f"{tree}/{member}")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_a_database_backup_restores_without_touching_the_files(tmp_path: Path) -> None:
    root, backup, env = _restore_fixture(tmp_path, {
        "db.sql": b"-- dump\n-- Dump completed\n",
        "redis.rdb": b"REDIS0012",
        "KIND": b"kind=pre-update\ncomponents=db,redis\n",
    })
    r = _run_restore(root, backup, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "data" / "files" / "sentinel.bin").read_text() == "an upload\n"
    assert "data/files and data/quarantine are NOT touched" in r.stdout
    assert "kind=pre-update" in r.stdout
    log = Path(env["DOCKER_LOG"]).read_text()
    assert "chown -R 1000:1000 /d/files" not in log
    assert "compose up -d redis" in log and "compose up -d db" in log


def test_a_full_backup_still_replaces_the_files(tmp_path: Path) -> None:
    root, backup, env = _restore_fixture(tmp_path, {
        "db.sql": b"-- dump\n",
        "files.tar.gz": _tarball(tmp_path, "files", "2026/09/a.bin"),
        "quarantine.tar.gz": _tarball(tmp_path, "quarantine", "q.bin"),
        "redis.rdb": b"REDIS0012",
    })
    r = _run_restore(root, backup, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (root / "data" / "files" / "sentinel.bin").exists()
    assert (root / "data" / "files" / "2026" / "09" / "a.bin").read_text() == "restored\n"


def test_a_backup_of_neither_shape_changes_nothing(tmp_path: Path) -> None:
    root, backup, env = _restore_fixture(tmp_path, {
        "db.sql": b"-- dump\n",
        "files.tar.gz": _tarball(tmp_path, "files", "a.bin"),
    })
    r = _run_restore(root, backup, env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert Path(env["DOCKER_LOG"]).read_text() == "", "nothing may run before the shape is known"
    assert (root / "data" / "files" / "sentinel.bin").exists()
