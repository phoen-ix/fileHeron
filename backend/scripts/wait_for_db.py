"""Wait until the database answers. Called by docker/backend/entrypoint.sh
before migrations, in the backend and the worker container alike.

The entrypoint used to probe with a bare TCP connect and close, which MariaDB
logs on every container start as "Aborted connection ... (This connection
closed normally without authentication)". This logs in and closes properly -
and still waits only for REACHABILITY: a server that answers but refuses the
login is reachable, and alembic, which runs next, reports the real problem
instead of a misleading "DB never became reachable".

Usage: python -m scripts.wait_for_db [attempts]   (exit 0 = reachable)
"""
from __future__ import annotations

import os
import sys
import time

import pymysql

# The client could not reach a server at all, or the server dropped the
# connection during the handshake (still starting up). Every other error came
# FROM the server, so it is up.
_NOT_UP = frozenset({2003, 2013})


def probe() -> bool:
    try:
        pymysql.connect(
            host=os.environ.get("DB_HOST", "db"),
            port=int(os.environ.get("DB_PORT", "3306")),
            user=os.environ.get("DB_USER", ""),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME") or None,
            connect_timeout=2,
        ).close()
        return True
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        return code not in _NOT_UP
    except OSError:
        return False


def main(argv: list[str]) -> int:
    attempts = int(argv[1]) if len(argv) > 1 else 30
    for i in range(attempts):
        if probe():
            return 0
        if i + 1 < attempts:
            time.sleep(1)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
