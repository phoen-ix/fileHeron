"""No "Aborted connection" in MariaDB's log on a deploy (v2.25.2).

Every update and restart of the reference instance left a burst of them
(2026-09-30 09:24, 10-02 06:57, 10-02 13:46): the entrypoint's readiness probe
was a bare TCP connect-and-close ("closed normally without authentication"), and
no process disposed its SQLAlchemy engine, so each pooled connection was dropped
at exit ("Got an error reading communication packets"). The probe now logs in
and closes; uvicorn, the arq worker and the bootstrap scripts dispose the engine.
"""
from __future__ import annotations

import pathlib

import pymysql
import pytest

from scripts import wait_for_db

_ENTRYPOINT = pathlib.Path(__file__).resolve().parents[2] / "docker" / "backend" / "entrypoint.sh"


class _Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_the_probe_logs_in_and_closes(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(pymysql, "connect", lambda **_kw: conn)
    assert wait_for_db.probe() is True
    assert conn.closed, "a probe that does not close is the warning it exists to end"


@pytest.mark.parametrize(
    ("code", "reachable"),
    [
        (2003, False),  # nothing listening (yet)
        (2013, False),  # dropped during the handshake: still starting
        (1045, True),  # access denied - the SERVER said so; alembic reports it next
        (1049, True),  # unknown database - likewise
    ],
)
def test_the_probe_waits_for_reachability_not_for_a_login(code, reachable, monkeypatch):
    def _refuse(**_kw):
        raise pymysql.err.OperationalError(code, "x")

    monkeypatch.setattr(pymysql, "connect", _refuse)
    assert wait_for_db.probe() is reachable


def test_wait_for_db_gives_up_after_its_attempts(monkeypatch):
    calls = []
    monkeypatch.setattr(wait_for_db, "probe", lambda: calls.append(1) or False)
    monkeypatch.setattr(wait_for_db.time, "sleep", lambda _s: None)
    assert wait_for_db.main(["wait_for_db", "3"]) == 1
    assert len(calls) == 3
    monkeypatch.setattr(wait_for_db, "probe", lambda: True)
    assert wait_for_db.main(["wait_for_db", "3"]) == 0


def test_the_entrypoint_probes_with_a_login_not_a_bare_socket():
    text = _ENTRYPOINT.read_text()
    assert "python -m scripts.wait_for_db" in text
    assert "socket.socket()" not in text


class _Engine:
    def __init__(self):
        self.disposed = 0

    def dispose(self):
        self.disposed += 1


@pytest.mark.asyncio
async def test_uvicorn_shutdown_disposes_the_engine(monkeypatch):
    from app import main as main_mod
    from app.services import maintenance

    fake = _Engine()
    monkeypatch.setattr(main_mod, "engine", fake)
    monkeypatch.setattr(main_mod, "configure_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(maintenance, "clear_maintenance_after_update", lambda _db: None)
    async with main_mod.lifespan(main_mod.app):
        assert fake.disposed == 0
    assert fake.disposed == 1


@pytest.mark.asyncio
async def test_the_worker_disposes_the_engine_on_shutdown(monkeypatch):
    from app.workers import worker

    fake = _Engine()
    monkeypatch.setattr(worker, "engine", fake)
    assert worker.WorkerSettings.on_shutdown is worker.shutdown
    await worker.WorkerSettings.on_shutdown({})
    assert fake.disposed == 1


def test_create_admin_disposes_the_engine(monkeypatch):
    from scripts import create_admin

    fake = _Engine()
    monkeypatch.setattr(create_admin, "engine", fake)
    monkeypatch.setattr(create_admin, "bootstrap_admin_if_configured", lambda _db: None)
    assert create_admin.main() == 0
    assert fake.disposed == 1
