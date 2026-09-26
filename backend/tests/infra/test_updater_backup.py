"""The updater's pre-update backup (MariaDB dump + Redis snapshot) and its
retention.

Docker is faked by swapping the executor's `subprocess` for a stand-in that
answers `mariadb-dump`, `redis-cli SAVE`, the size query and `docker cp` - the
artifacts, manifest, modes and failure paths are then checked on disk, and the
manifest with the real `sha256sum -c` a restore uses.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

PASSWORD = "s3cret-root-pw"  # noqa: S105 - a test fixture value
DUMP = b"-- MariaDB dump\nCREATE TABLE users (id int);\n-- Dump completed on 2026-09-26 12:00:00\n"
RDB = b"REDIS0012\xfa\x09redis-ver"


class FakeDocker:
    """Stands in for the executor's `subprocess` module."""

    TimeoutExpired = subprocess.TimeoutExpired
    CompletedProcess = subprocess.CompletedProcess
    SubprocessError = subprocess.SubprocessError
    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []
        self.dump = DUMP
        self.dump_rc = 0
        self.save_reply = "OK\n"
        self.db_bytes = "1048576"

    def run(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if "SAVE" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.save_reply, "")
        if "information_schema.tables" in " ".join(cmd):
            return subprocess.CompletedProcess(cmd, 0, self.db_bytes + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def Popen(self, cmd, **kw):  # noqa: N802 - mirrors subprocess.Popen
        self.calls.append((cmd, kw))
        assert "mariadb-dump" in cmd
        kw["stdout"].write(self.dump)
        return SimpleNamespace(returncode=self.dump_rc, communicate=lambda timeout=None: (None, b""),
                               kill=lambda: None)


@pytest.fixture
def docker(executor, monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(executor, "subprocess", fake)
    monkeypatch.setattr(executor, "_compose_config", lambda _f, _t: ({
        "services": {"db": {"image": "mariadb:11.8.3", "environment": {
            "MYSQL_ROOT_PASSWORD": PASSWORD, "MYSQL_DATABASE": "fileheron"}}}
    }, ""))
    monkeypatch.setattr(executor, "_container_id", lambda s: f"cid-{s}")
    monkeypatch.setattr(executor, "_inspect", lambda _c, _f: "mariadb:11.8.3")

    def run_capture(cmd, env=None):
        fake.calls.append((cmd, {"env": env}))
        if cmd[:2] == ["docker", "cp"]:
            Path(cmd[3]).write_bytes(RDB)
        return 0

    monkeypatch.setattr(executor, "run_capture", run_capture)
    (executor.WORKSPACE / ".git").mkdir()
    executor.STATE_FILE.write_text(json.dumps({"id": "j", "status": "pulling"}))
    return fake


def _backup(executor):
    return executor.take_pre_update_backup("v1.0.0", "v1.1.0", "202609240001")


def test_a_backup_holds_the_dump_the_snapshot_and_a_verifiable_manifest(executor, docker):
    rel = _backup(executor)
    assert rel and rel.startswith("backups/pre-update/") and rel.endswith("_v1.0.0-to-v1.1.0")
    d = executor.WORKSPACE / rel
    assert sorted(p.name for p in d.iterdir()) == ["KIND", "db.sql", "manifest.txt", "redis.rdb"]
    assert (d / "db.sql").read_bytes() == DUMP and (d / "redis.rdb").read_bytes() == RDB
    # S603/S607: the same check restore.sh runs, on the test's own files.
    check = subprocess.run(["sha256sum", "-c", "manifest.txt"], cwd=d,  # noqa: S603, S607
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    kind = dict(line.split("=", 1) for line in (d / "KIND").read_text().splitlines())
    assert kind["kind"] == "pre-update" and kind["components"] == "db,redis"
    assert (kind["from_tag"], kind["to_tag"]) == ("v1.0.0", "v1.1.0")
    assert kind["alembic_head"] == "202609240001" and kind["db_image"] == "mariadb:11.8.3"
    # the dump holds password hashes: owner-only
    assert d.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in d.iterdir())
    assert not list(executor.BACKUP_ROOT.glob(".partial-*"))


def test_the_root_password_never_appears_in_an_argv(executor, docker):
    assert _backup(executor)
    for cmd, _kw in docker.calls:
        assert PASSWORD not in " ".join(cmd)
    dumps = [kw for cmd, kw in docker.calls if "mariadb-dump" in cmd]
    assert dumps and dumps[0]["env"]["MYSQL_PWD"] == PASSWORD
    log = json.loads(executor.STATE_FILE.read_text())["log_tail"]
    assert not any(PASSWORD in line for line in log)


def test_a_dump_without_its_completion_marker_fails_and_leaves_nothing(executor, docker):
    docker.dump = b"-- MariaDB dump\nCREATE TABLE users (id int);\n"  # cut short
    assert _backup(executor) is None
    assert list(executor.BACKUP_ROOT.iterdir()) == []


def test_a_failing_dump_fails(executor, docker):
    docker.dump_rc = 2
    assert _backup(executor) is None


def test_a_redis_save_that_does_not_answer_ok_fails(executor, docker):
    docker.save_reply = "ERR Background save already in progress\n"
    assert _backup(executor) is None
    assert list(executor.BACKUP_ROOT.iterdir()) == []


def test_too_little_disk_fails_before_dumping(executor, docker, monkeypatch):
    monkeypatch.setattr(executor.shutil, "disk_usage", lambda _p: SimpleNamespace(free=10 * 1024 ** 2))
    assert _backup(executor) is None
    assert not any("mariadb-dump" in cmd for cmd, _ in docker.calls)


def test_missing_credentials_fail(executor, docker, monkeypatch):
    monkeypatch.setattr(executor, "_compose_config", lambda _f, _t: ({"services": {"db": {}}}, ""))
    assert _backup(executor) is None


def test_a_dangling_backups_symlink_is_refused(executor, docker, tmp_path):
    (executor.WORKSPACE / "backups").symlink_to("/definitely/not/here")
    assert _backup(executor) is None


# --- retention ------------------------------------------------------------------


def _mk(executor, when: datetime, label="v1.0.0-to-v1.1.0", manifest=True) -> Path:
    d = executor.BACKUP_ROOT / f"{when.strftime('%Y-%m-%d_%H%M%S')}_{label}"
    d.mkdir(parents=True)
    if manifest:
        (d / "manifest.txt").write_text("x  db.sql\n")
    return d


def _left(executor) -> list[str]:
    return sorted(p.name for p in executor.BACKUP_ROOT.iterdir())


NOW = datetime.now(UTC)


def test_retention_keeps_the_newest_n(executor):
    made = [_mk(executor, NOW - timedelta(days=i)) for i in range(5)]
    executor.prune_pre_update_backups(3, 0)
    assert _left(executor) == sorted(p.name for p in made[:3])


def test_retention_drops_old_ones_but_never_the_newest(executor):
    newest = _mk(executor, NOW - timedelta(days=90))
    _mk(executor, NOW - timedelta(days=120))
    executor.prune_pre_update_backups(0, 30)
    assert _left(executor) == [newest.name]


def test_retention_by_age_and_count_together(executor):
    fresh = [_mk(executor, NOW - timedelta(days=i)) for i in (1, 2)]
    _mk(executor, NOW - timedelta(days=45))
    executor.prune_pre_update_backups(3, 30)
    assert _left(executor) == sorted(p.name for p in fresh)


def test_zero_limits_keep_everything(executor):
    made = [_mk(executor, NOW - timedelta(days=400 + i)) for i in range(4)]
    executor.prune_pre_update_backups(0, 0)
    assert len(_left(executor)) == len(made)


def test_retention_touches_only_its_own_complete_backups(executor):
    _mk(executor, NOW)
    no_manifest = _mk(executor, NOW - timedelta(days=200), manifest=False)
    stranger = executor.BACKUP_ROOT / "keep-me-2020"
    stranger.mkdir()
    executor.prune_pre_update_backups(1, 1)
    assert no_manifest.name in _left(executor) and stranger.name in _left(executor)


def test_the_just_taken_backup_is_protected(executor):
    _mk(executor, NOW + timedelta(minutes=1), label="clock-skewed")
    mine = _mk(executor, NOW)
    executor.prune_pre_update_backups(1, 0, protect=mine.name)
    assert mine.name in _left(executor)


def test_abandoned_partials_are_swept_after_three_hours(executor):
    old = executor.BACKUP_ROOT / ".partial-old"
    old.mkdir(parents=True)
    stamp = time.time() - 4 * 3600
    os.utime(old, (stamp, stamp))
    young = executor.BACKUP_ROOT / ".partial-young"
    young.mkdir()
    executor.prune_pre_update_backups(3, 30)
    assert _left(executor) == [".partial-young"]
