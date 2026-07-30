#!/bin/sh
# fileHeron MariaDB init: charset + collation only. Schema is Alembic's job.
#
# Was init.sql with the database name hardcoded as `fileheron`. DB_NAME is a
# documented operator knob, and on a first boot with any other value the ALTER
# hit a database that did not exist, which aborts MariaDB's init sequence - so
# the stack never came up AND the utf8mb4 collation was never applied
# (audit 2026-07-30). A .sh init script can read the environment; a .sql one
# cannot. Both are executed by the official image's entrypoint.
set -eu
: "${MARIADB_DATABASE:?MARIADB_DATABASE must be set}"
mariadb -u root -p"$MARIADB_ROOT_PASSWORD" -e \
  "ALTER DATABASE \`${MARIADB_DATABASE}\` CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci;"
