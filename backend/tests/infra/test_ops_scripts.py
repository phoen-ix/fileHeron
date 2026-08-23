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
