"""fileHeron updater-executor - single-shot update worker.

Spawned by the shim (`docker/updater-shim/shim.sh`) per update request.
Reads `/state/current_job.json` for the target tag + action, does the
work, writes incremental status back, exits 0 (success) or non-zero
(failure). The shim picks up the exit code; the backend reads the
status file to show live progress in the admin UI.

The executor IS itself recreated each run (it's spawned with
`docker run --rm`); the shim is recreated at the very end of a successful
update, after the terminal status is written (see main()).

An update may also back up the database first and bring the infra services
(db, redis, clamav, tusd) to the release: it fast-forwards the host checkout
to the release commit and recreates only the services whose definition
changed. Nothing on the host changes before that backup has succeeded, and
anything that makes the infra step unsafe skips it with the manual command in
the log while the app update proceeds as it always did.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path(os.environ.get("EXECUTOR_STATE_FILE", "/state/current_job.json"))
WORKSPACE = Path(os.environ.get("EXECUTOR_WORKSPACE", "/workspace"))
COMPOSE_FILE = WORKSPACE / "docker-compose.yml"
ENV_FILE = WORKSPACE / ".env"
ROLLBACK_FILE = STATE_FILE.parent / "rollback_target.json"
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "fileheron")
GHCR_OWNER = os.environ.get("GHCR_OWNER", "phoen-ix")
BACKEND_HEALTH_URL = os.environ.get(
    "EXECUTOR_BACKEND_HEALTH_URL", "http://backend:8000/api/health"
)
HEALTH_TIMEOUT_SEC = int(os.environ.get("EXECUTOR_HEALTH_TIMEOUT_SEC", "90"))

# Services compose recreates per update, in this order, while the job is
# in-flight. The shim is NOT one of them - recreating it mid-job would have its
# replacement's startup sweep mark this very job failed. It is recreated at the
# end instead, after the terminal status is written; see the note in main().
SERVICES = ["backend", "worker", "frontend"]
# Every `compose up` below passes --no-deps, and that is load-bearing. `up`
# without it brings up a service's depends_on too, so whenever compose decided
# db or redis needed recreating it yanked the DATABASE out from under the
# still-running old backend - which then answered live requests with its own 500
# envelope until its replacement came up. Measured on the reference instance:
# the 2026-09-05 update took 23:35:25 -> 23:35:55, a 30s window carrying 11
# app-served 500s and 18 proxy 502s, against 6s and zero 500s for an update that
# left db and redis alone. `depends_on: service_healthy` does not help: it orders
# STARTUP, it does not stop a dependency being restarted beneath a running
# container. Maintenance mode cannot cover it either - the flag lives in
# app_settings, so reading it needs the database that is the thing going away.
#
# An update only ever swaps these three images; it has no business recreating the
# data layer. If db or redis are genuinely down, --no-deps lets the health check
# fail and the auto-rollback fire, which is the correct outcome - starting a
# database as a side effect of an image swap is not.
# Images we pull. Includes the updater images so subsequent updates
# don't have to re-pull them on a slow link.
IMAGES_TO_PULL = SERVICES + ["updater-shim", "updater-executor"]

# Infra services an update may bring to the release, in this order. A separate
# constant from SERVICES on purpose: SERVICES is what every update swaps, these
# are recreated only when their definition changed (plan_infra), one at a time,
# each health-gated, and still with --no-deps.
INFRA_SYNC_ORDER = ("db", "redis", "clamav", "tusd")
# Recreating one of these takes the data layer away from the app, so backend and
# worker are stopped around it and a failure is fatal. clamav and tusd failing
# only degrades scanning/uploads and ends the update with a warning.
DATA_SERVICES = frozenset({"db", "redis"})
# Health budget floors per infra service; the real budget also derives from the
# service's own healthcheck. A MariaDB major upgrade runs mariadb-upgrade before
# the server reports healthy, and clamav's first start syncs signatures.
HEALTH_FLOOR_SEC = {"db": 900, "redis": 120, "clamav": 600, "tusd": 90}
BACKUP_ROOT = WORKSPACE / "backups" / "pre-update"
# The commit this executor image was built from (server-release.yml passes the
# tag's commit). The checkout is only ever fast-forwarded to exactly this.
RELEASE_SHA = os.environ.get("FH_GIT_SHA", "unknown")
REPO_URL = os.environ.get("EXECUTOR_REPO_URL") or f"https://github.com/{GHCR_OWNER}/fileHeron.git"
# Compose files docker compose loads on its own next to docker-compose.yml. The
# executor always passes -f, so it would recreate infra WITHOUT them while a
# host `docker compose up` applies them - it refuses the infra step instead.
_OVERRIDE_FILES = (
    "docker-compose.override.yml", "docker-compose.override.yaml",
    "compose.override.yml", "compose.override.yaml",
)
# Options the backend puts in the job. Missing (a backend older than this
# executor - the update that INSTALLS this feature) or invalid values fall back
# to these, and these defaults are what protect that first update: infra sync
# on, and a backup forced when the release changes the database.
_OPTION_DEFAULTS: dict[str, bool | int] = {
    "backup": False,
    "backup_on_db_change": True,
    "backup_keep": 3,
    "backup_max_age_days": 30,
    "infra_sync": True,
}
_OPTION_BOUNDS = {"backup_keep": (0, 100), "backup_max_age_days": (0, 3650)}
_BACKUP_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}_[A-Za-z0-9._-]+")
_PARTIAL_MAX_AGE_SEC = 180 * 60
_MAX_WARNINGS = 20


def utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


def read_job() -> dict:
    if not STATE_FILE.exists():
        raise SystemExit("no /state/current_job.json")
    return json.loads(STATE_FILE.read_text())


def _write_state_text(text: str) -> None:
    """Atomically replace the state file, mode 0644.

    Mode: the file is read by the BACKEND (uid 1000 appuser) while the executor
    runs as root, so without an explicit chmod the mode is whatever the last
    writer left - and a mktemp+mv default of 0600 silently breaks the backend's
    _read_state on every poll thereafter.

    Atomicity: this used to be a plain `write_text`, which truncates and then
    writes. The backend polls this file every second or two DURING the update,
    so it regularly read a half-written file and got a JSONDecodeError - once
    per log line, which is when the file is rewritten. Writing a sibling temp
    file and os.replace()ing it makes every read see one complete version or
    the other (audit 2026-07-30, flow-selfupdate-8). The temp file must be in
    the SAME directory: /state is a bind mount, and os.replace across
    filesystems raises."""
    tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.tmp")
    try:
        tmp.write_text(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        # Last resort: a non-atomic write beats losing the status entirely.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        STATE_FILE.write_text(text)
        try:
            os.chmod(STATE_FILE, 0o644)
        except OSError:
            pass


def write_job_field(**kwargs) -> None:
    """Read-modify-write the state file with new fields. The shim and
    backend also read this file; the executor is the only writer during
    its run, so no locking is needed."""
    if not STATE_FILE.exists():
        return
    data = json.loads(STATE_FILE.read_text())
    data.update(kwargs)
    _write_state_text(json.dumps(data, indent=2))


def log_line(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    print(f"[{utcnow_iso()}] {line}", flush=True)
    if not STATE_FILE.exists():
        return
    data = json.loads(STATE_FILE.read_text())
    log_tail = data.get("log_tail", [])
    log_tail.append(f"[{utcnow_iso()}] {line}")
    if len(log_tail) > 200:
        log_tail = log_tail[-200:]
    data["log_tail"] = log_tail
    _write_state_text(json.dumps(data, indent=2))


def run_capture(cmd: list[str], env: dict[str, str] | None = None) -> int:
    """Run a subprocess, stream stdout+stderr into the log tail, return
    exit code."""
    log_line("$ " + " ".join(shlex.quote(c) for c in cmd))
    merged = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(WORKSPACE),
        env=merged,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log_line(line)
    proc.wait()
    log_line(f"(exit {proc.returncode})")
    return proc.returncode


def read_current_tag() -> str:
    """Returns the FH_TAG from .env (or 'latest' if unset)."""
    if not ENV_FILE.exists():
        return "latest"
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("FH_TAG="):
            return line.split("=", 1)[1].strip()
    return "latest"


def write_current_tag(new_tag: str) -> None:
    """Idempotent: replace FH_TAG line if present, append if not."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"FH_TAG={new_tag}\n")
        return
    lines = ENV_FILE.read_text().splitlines()
    found = False
    for i, raw in enumerate(lines):
        if raw.strip().startswith("FH_TAG="):
            lines[i] = f"FH_TAG={new_tag}"
            found = True
            break
    if not found:
        lines.append(f"FH_TAG={new_tag}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _compose_env(tag: str) -> dict[str, str]:
    """Env for a `docker compose` invocation: pin FH_TAG (which image to
    use) and pin every host-path variable so they resolve against the HOST
    compose dir, not the executor's /workspace.

    All three matter, and forgetting the last two made the updater
    SINGLE-USE. `docker-compose.yml` defines them as::

        UPDATER_HOST_WORKSPACE: ${UPDATER_HOST_WORKSPACE:-${PWD:-/opt/fileHeron}}
        UPDATER_HOST_STATE:     ${UPDATER_HOST_STATE:-${PWD:-/opt/fileHeron}/data/updater}

    so when compose runs from in here, `$PWD` is `/workspace` and the
    updater-shim this very command recreates is left believing the host state
    directory is `/workspace/data/updater`. The shim's own `/state` mount still
    resolves correctly (that line uses COMPOSE_HOST_ROOT), so nothing looks
    wrong - until the NEXT update, when the shim spawns an executor with
    `-v /workspace/data/updater:/state`, Docker helpfully creates that path
    empty and root-owned on the host, and the executor exits 1 with
    "no /state/current_job.json" before it can write a status.

    Net effect: every SUCCESSFUL update broke the one after it. Observed
    v2.9.0 -> v2.10.0; the v2.8.0 -> v2.9.0 update worked only because that
    shim had been created by a host-side `docker compose up`.

    Derived from COMPOSE_HOST_ROOT rather than read from our own environment,
    because the shim that launched us may predate this fix and pass neither.
    """
    env = {"FH_TAG": tag}
    host_root = os.environ.get("COMPOSE_HOST_ROOT")
    if host_root:
        env["COMPOSE_HOST_ROOT"] = host_root
        # Prefer an explicit value if a newer shim supplied one; otherwise
        # reconstruct it the same way docker-compose.yml's default does.
        env["UPDATER_HOST_WORKSPACE"] = (
            os.environ.get("UPDATER_HOST_WORKSPACE") or host_root
        )
        env["UPDATER_HOST_STATE"] = (
            os.environ.get("UPDATER_HOST_STATE") or f"{host_root}/data/updater"
        )
    return env


_REV_RE = re.compile(r"^([0-9a-f]+)")

# Validate INSIDE the privileged boundary, not only at the API and not only in
# the shim. This process runs as root with the host docker socket AND the host
# workspace mounted, and everything it acts on comes from /state, which the
# backend container can write. The shim checks `target_tag` before spawning us,
# but (a) it checks with `grep -Eq '^...$'`, and grep is LINE-oriented, so a
# value containing a newline passes, and (b) it validates, flips the job to
# `claiming`, then BLOCKS on `docker pull` - and we re-read the file after that,
# so the value we act on is not provably the value it checked.
#
# `fullmatch` on a `\A...\Z`-free pattern is the same anchoring the backend's
# RELEASE_TAG_RE call sites use; `$` alone would accept a trailing newline,
# which is exactly the shape that matters here.
_TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
_ACTIONS = frozenset({"update", "rollback"})
_HEAD_RE = re.compile(r"[0-9a-f]+")


def _is_valid_tag(value: object) -> bool:
    return isinstance(value, str) and _TAG_RE.fullmatch(value) is not None


def _is_valid_head(value: object) -> bool:
    """An alembic revision id. `_parse_alembic_revision` already constrains what
    we WRITE to this shape; this constrains what we READ BACK, because the file
    it comes from is writable by the backend container."""
    return isinstance(value, str) and _HEAD_RE.fullmatch(value) is not None


def _parse_alembic_revision(text: str) -> str | None:
    """First revision id on the first real line of `alembic current`
    output, e.g. '202606090001 (head)' -> '202606090001'. Skips empty
    lines and alembic's INFO/log preamble."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("INFO") or line.startswith("["):
            continue
        m = _REV_RE.match(line)
        if m:
            return m.group(1)
    return None


def capture_alembic_head() -> str | None:
    """Read the DB's current alembic revision from the RUNNING (pre-update,
    old-image) backend so a rollback can stamp the pointer back to it.
    Returns None on failure - auto_rollback then skips the stamp and warns.
    Safe even against a future broken image: alembic/env.py imports only
    app.config, never app.main (the layer that an nh3-style miss breaks)."""
    # Retried, and with a longer ceiling than the original single 30 s attempt.
    # This runs during the drain, when the box is at its busiest; one slow
    # `alembic current` was enough to lose the head, and losing it silently
    # downgrades a one-click Rollback into "boots into the migration trap"
    # (audit #2).
    last = ""
    for attempt in (1, 2, 3):
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE),
                 "exec", "-T", "backend", "alembic", "current"],
                capture_output=True, text=True, cwd=str(WORKSPACE), timeout=60,
            )
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            log_line(f"WARN alembic head capture attempt {attempt}/3 failed: {last}")
            continue
        if result.returncode != 0:
            last = f"exit {result.returncode}: {result.stderr.strip()[:200]}"
            log_line(f"WARN alembic head capture attempt {attempt}/3 failed: {last}")
            continue
        rev = _parse_alembic_revision(result.stdout)
        if rev:
            log_line(f"captured pre-update alembic head: {rev}")
            return rev
        last = "no parseable revision"
        log_line(f"WARN alembic head capture attempt {attempt}/3: {last}")
    log_line(f"WARN could not capture alembic head after 3 attempts ({last}) - "
             "a rollback across a migration will need a manual `alembic stamp`")
    return None


def _write_rollback_file(tag: str, alembic_head: str | None) -> None:
    """Record the tag a rollback should return to. Same atomic-replace shape as
    the state file: the backend reads this to decide whether to offer the
    Rollback control at all."""
    payload = json.dumps({"tag": tag, "alembic_head": alembic_head})
    tmp = ROLLBACK_FILE.with_name(f".{ROLLBACK_FILE.name}.tmp")
    try:
        tmp.write_text(payload)
        os.chmod(tmp, 0o644)
        os.replace(tmp, ROLLBACK_FILE)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        ROLLBACK_FILE.write_text(payload)
        try:
            os.chmod(ROLLBACK_FILE, 0o644)
        except OSError:
            pass


def _read_rollback_file() -> dict:
    if not ROLLBACK_FILE.exists():
        return {}
    try:
        return json.loads(ROLLBACK_FILE.read_text())
    except Exception:
        return {}


def resolve_running_version() -> str | None:
    """Ask the running backend which version it actually is.

    `.env` ships `FH_TAG=latest` (install.sh writes it, .env.example documents
    it), and `:latest` is re-pointed at every release - so recording "latest" as
    the rollback anchor recorded nothing. Rollback then pulled `:latest`, which
    IS the version being fled, brought it back up, waited for
    `running_version == "latest"` (which never matches a real version string),
    and reported failure after moving the DB pointer backwards under running
    code (audit #2). Resolve the floating tag to the concrete version first.
    """
    try:
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "5", BACKEND_HEALTH_URL],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            running = json.loads(result.stdout).get("running_version")
            if running and running not in ("latest", "unknown", ""):
                return str(running)
    except Exception as e:
        log_line(f"WARN could not resolve running version: {type(e).__name__}: {e}")
    return None


def wait_for_backend_health(expected_tag: str | None, *, not_tag: str | None = None) -> bool:
    """Poll backend /api/health until running_version == expected_tag
    or timeout. Returns True on success.

    `expected_tag=None` means "the version is unknown": accept any running
    version other than `not_tag` - the auto-rollback case when the floating
    `latest` could not be resolved to a real version."""
    deadline = time.time() + HEALTH_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["curl", "-fsS", "--max-time", "5", BACKEND_HEALTH_URL],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                parsed = json.loads(result.stdout)
                running = parsed.get("running_version")
                if running == expected_tag or (
                    expected_tag is None and running and running != not_tag
                ):
                    log_line(f"backend reports running_version={running}")
                    return True
                want = expected_tag or f"anything but {not_tag}"
                log_line(f"backend running_version={running} (want {want})")
        except Exception as e:
            log_line(f"health probe failed: {type(e).__name__}: {e}")
        time.sleep(3)
    log_line("TIMEOUT waiting for backend to report new version")
    return False


def auto_rollback(
    previous_tag: str,
    previous_head: str | None,
    target_tag: str,
    reason: str,
    *,
    expected_version: str | None,
) -> int:
    """Self-heal after a failed UPDATE: restore the previous (known-good)
    version with no backend/GUI dependency. Ordering is load-bearing:
    stamp the DB pointer back FIRST using the NEW image (its alembic tree
    is the superset, so it can resolve the current revision), THEN flip
    FH_TAG to the old tag, THEN compose up, THEN re-verify health. `stamp`
    only moves the version pointer (never runs downgrade()), so additive
    new tables stay in place - harmless under the old code and reconciled
    by the migrations' `_has_table` guards on the next forward upgrade.

    Returns 0 when prod is healthy again on previous_tag; non-zero if the
    rollback itself failed (operator must intervene). It NEVER reports
    success while prod is on a known-broken tag.

    `expected_version` is what the restored backend will REPORT, which is not
    `previous_tag` when that is the floating `latest`: `running_version` is the
    baked FH_VERSION, so waiting for "latest" timed out on every install still
    on the shipped default and wrote "auto-rollback FAILED ... did not become
    healthy" about a stack that had recovered. None = unresolved; accept any
    version but the one that just failed."""
    log_line(f"AUTO-ROLLBACK: update to {target_tag} failed ({reason}); restoring {previous_tag}")
    write_job_field(status="rolling_back", rollback_reason=reason)

    # (i) Move the DB version pointer back. .env still holds target_tag here,
    # so this one-shot runs the NEW image (the superset tree).
    if previous_head:
        if run_capture(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "run", "--rm",
             "--no-deps", "--entrypoint", "alembic", "backend", "stamp", previous_head],
            env=_compose_env(target_tag),
        ) != 0:
            write_job_field(
                status="failed",
                error=f"auto-rollback FAILED: could not stamp DB back to {previous_head} "
                      f"(update to {target_tag} had failed: {reason})",
                finished_at=utcnow_iso(),
            )
            return 10
    else:
        log_line("WARN no pre-update alembic head captured - skipping DB stamp "
                 "(rollback may hit the migration trap if a migration was applied)")

    # (ii) Restore the previous tag, then (iii) bring prod back up on it.
    write_current_tag(previous_tag)
    if run_capture(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--no-deps"] + SERVICES,
        env=_compose_env(previous_tag),
    ) != 0:
        write_job_field(
            status="failed",
            error=f"auto-rollback FAILED: `compose up` on {previous_tag} failed "
                  f"(update to {target_tag} had failed: {reason})",
            finished_at=utcnow_iso(),
        )
        return 11

    # (iv) Re-verify health on the restored tag.
    if not wait_for_backend_health(expected_version, not_tag=target_tag):
        write_job_field(
            status="failed",
            error=f"auto-rollback FAILED: {previous_tag} did not become healthy "
                  f"(update to {target_tag} had failed: {reason})",
            finished_at=utcnow_iso(),
        )
        return 12

    write_job_field(
        status="rolled_back",
        error=f"update to {target_tag} failed ({reason}); automatically rolled back to {previous_tag}",
        finished_at=utcnow_iso(),
    )
    log_line(f"AUTO-ROLLBACK complete - prod healthy on {previous_tag}")
    return 0


# --- Job options, phases, warnings -------------------------------------------


def set_phase(phase: str) -> None:
    """Progress detail for the admin UI. It lives in its own field because the
    STATUS values must stay the ones every older shim, backend and SPA know: the
    shim that supervises this run is the previous release's, and an unknown
    status would trip its in-flight and terminal checks."""
    write_job_field(phase=phase)
    log_line(f"phase: {phase}")


def add_warning(message: str) -> None:
    """Record a problem that does not fail the update; the SPA lists these."""
    log_line(f"WARN {message}")
    if not STATE_FILE.exists():
        return
    data = json.loads(STATE_FILE.read_text())
    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    if len(warnings) < _MAX_WARNINGS:
        warnings.append(message[:500])
    data["warnings"] = warnings
    _write_state_text(json.dumps(data, indent=2))


def _read_options(job: dict) -> dict[str, bool | int]:
    """The backend's options, validated here like every other job field: the
    file is writable by the backend container and this process is root."""
    opts = dict(_OPTION_DEFAULTS)
    raw = job.get("options")
    if raw is None:
        return opts
    if not isinstance(raw, dict):
        log_line("WARN job options are not an object; using the defaults")
        return opts
    for key, default in _OPTION_DEFAULTS.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        else:
            lo, hi = _OPTION_BOUNDS[key]
            ok = isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi
        if ok:
            opts[key] = value
        else:
            log_line(f"WARN job option {key} is invalid; using the default")
    return opts


def _manual_command(tag: str) -> str:
    return (f"git fetch --tags && git merge --ff-only {tag} && "
            f"docker compose up -d --no-deps {' '.join(INFRA_SYNC_ORDER)}")


def _pull(ref: str, *, quiet: bool = False) -> int:
    # EXECUTOR_PULL_MISSING_ONLY is for a local rehearsal with images that exist
    # only on this host (a tag that was never pushed cannot be pulled). The shim
    # passes an explicit env list, so no real update can set it.
    if os.environ.get("EXECUTOR_PULL_MISSING_ONLY") == "1" and subprocess.run(
        ["docker", "image", "inspect", ref], capture_output=True, timeout=60,
    ).returncode == 0:
        log_line(f"{ref} is present locally; not pulling (EXECUTOR_PULL_MISSING_ONLY)")
        return 0
    return run_capture(["docker", "pull", "-q", ref] if quiet else ["docker", "pull", ref])


def _env_file_value(name: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


# --- docker helpers ----------------------------------------------------------

_MISSING_VAR_RE = re.compile(r"required variable (\w+) is missing a value")


def _compose_config(compose_file: Path, tag: str) -> tuple[dict | None, str]:
    """`docker compose config` as JSON, plus a hint when it fails.

    The output carries every secret in .env (database passwords, JWT_SECRET), so
    NEITHER it nor compose's error text is ever logged; the only thing passed on
    is the name of a missing required variable. `--project-directory` is fixed
    so a copy of a compose file in a temp dir resolves exactly like the real
    one."""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "--project-directory", str(WORKSPACE),
             "config", "--format", "json"],
            capture_output=True, text=True, cwd=str(WORKSPACE), timeout=60,
            env={**os.environ, **_compose_env(tag)},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    if result.returncode != 0:
        m = _MISSING_VAR_RE.search(result.stderr)
        return None, (f"required variable {m.group(1)} is not set in .env" if m else "")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, ""
    return (data, "") if isinstance(data, dict) else (None, "")


def _container_id(service: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-a", "-q", service],
            capture_output=True, text=True, cwd=str(WORKSPACE), timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.split()
    return lines[0] if result.returncode == 0 and lines else None


def _inspect(container: str, fmt: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", fmt, container],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _normalize_image(ref: str) -> str:
    """`mariadb:12.3.3`, `docker.io/library/mariadb:12.3.3` and
    `index.docker.io/library/mariadb:12.3.3` are the same image."""
    ref = ref.strip()
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break
    if ref.startswith("library/"):
        ref = ref[len("library/"):]
    if ref and "@" not in ref and ":" not in ref.rsplit("/", 1)[-1]:
        ref += ":latest"
    return ref


_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_DURATION_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}


def _duration_sec(value: object) -> float:
    """A compose/Go duration ('1m30s', '10s') or a number of seconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    return sum(float(n) * _DURATION_UNITS[u] for n, u in _DURATION_RE.findall(value))


def _health_budget(service: str, svc_cfg: dict) -> int:
    hc = svc_cfg.get("healthcheck") or {}
    interval = _duration_sec(hc.get("interval")) or 30.0
    raw_retries = hc.get("retries")
    retries = raw_retries if isinstance(raw_retries, int) else 3
    derived = _duration_sec(hc.get("start_period")) + interval * (retries + 1) + 30
    return max(HEALTH_FLOOR_SEC.get(service, 120), int(derived))


def _bind_files(svc_cfg: dict) -> set[str]:
    """Repo-relative paths of the checkout files a service bind-mounts, except
    /docker-entrypoint-initdb.d/ scripts - those run only on an empty datadir,
    so a change to one is no reason to recreate a running database."""
    roots = [r for r in (os.environ.get("COMPOSE_HOST_ROOT"), str(WORKSPACE)) if r]
    found: set[str] = set()
    for vol in svc_cfg.get("volumes") or []:
        if not isinstance(vol, dict) or vol.get("type") != "bind":
            continue
        source, target = str(vol.get("source", "")), str(vol.get("target", ""))
        if target.startswith("/docker-entrypoint-initdb.d/"):
            continue
        for root in roots:
            prefix = root.rstrip("/") + "/"
            if source.startswith(prefix):
                found.add(source[len(prefix):])
                break
    return found


# --- git, as the checkout's owner --------------------------------------------

_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_URL_CREDENTIALS_RE = re.compile(r"://[^/@\s]+@")


def _checkout_owner() -> tuple[int, int] | None:
    try:
        st = (WORKSPACE / ".git").stat()
    except OSError:
        return None
    return st.st_uid, st.st_gid


def _git(args: list[str], owner: tuple[int, int], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run git in the host checkout AS ITS OWNER, never as root.

    As root, every file a fast-forward writes would become root-owned in the
    operator's checkout, and the next `git pull` or backup run on the host would
    fail on it. Running as the owner also means the repo's own config and hooks
    can do nothing the owner could not already do; hooks are switched off
    anyway, and the global/system config (not the owner's, and unreachable here)
    is ignored."""
    cmd = ["git", "-c", f"safe.directory={WORKSPACE}", "-c", "protocol.ext.allow=never",
           "-c", "core.hooksPath=/dev/null", "-C", str(WORKSPACE), *args]
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    try:
        if os.geteuid() == 0 and owner[0] != 0:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                                  cwd=str(WORKSPACE), user=owner[0], group=owner[1], extra_groups=[])
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                              cwd=str(WORKSPACE))
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, 255, "", f"{type(e).__name__}: {e}")


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    return _URL_CREDENTIALS_RE.sub("://***@", lines[-1] if lines else f"exit {result.returncode}")[:300]


def _resolve_tag_commit(tag: str, owner: tuple[int, int]) -> str | None:
    ref = f"refs/tags/{tag}"

    def lookup() -> str | None:
        r = _git(["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], owner)
        out = r.stdout.strip()
        return out if r.returncode == 0 and _SHA_RE.fullmatch(out) else None

    found = lookup()
    if found:
        return found
    origin = _git(["remote", "get-url", "origin"], owner)
    url = origin.stdout.strip() if origin.returncode == 0 else ""
    sources: list[tuple[str, str]] = []
    # The executor image has no ssh client, so an ssh origin falls through to the
    # public https URL. Never a forced (`+`) refspec: an existing tag is never
    # overwritten.
    if url.startswith(("https://", "file://", "/")):
        sources.append(("origin", "origin"))
    sources.append(("the public repository", REPO_URL))
    for label, source in sources:
        r = _git(["fetch", "--quiet", "--no-tags", source, f"{ref}:{ref}"], owner, timeout=300)
        if r.returncode == 0:
            found = lookup()
            if found:
                return found
        log_line(f"WARN could not fetch {tag} from {label}: {_git_error(r)}")
    return None


def _git_paths(args: list[str], owner: tuple[int, int]) -> list[str] | None:
    r = _git(args, owner)
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.split("\0") if p]


def _dirty_paths(owner: tuple[int, int]) -> set[str] | None:
    """Tracked files with local modifications (staged or not)."""
    r = _git(["status", "--porcelain=v1", "-z", "--untracked-files=no"], owner)
    if r.returncode != 0:
        return None
    tokens = r.stdout.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            if i < len(tokens) and tokens[i]:
                paths.add(tokens[i])
            i += 1
    return paths


def _owner_can_write_dir(path: Path, owner: tuple[int, int]) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if owner[0] == 0:
        return True
    if st.st_uid == owner[0]:
        return bool(st.st_mode & 0o200)
    if st.st_gid == owner[1]:
        return bool(st.st_mode & 0o020)
    return bool(st.st_mode & 0o002)


def _unwritable_for_ff(paths: list[str], owner: tuple[int, int]) -> str | None:
    """First path a fast-forward would have to write but the owner cannot.

    git writes a checkout file by unlinking and recreating it, so what matters
    is the nearest existing directory. Checked up front because git applies a
    fast-forward file by file: failing halfway leaves a half-updated tree.
    Deletions are not checked - git only warns when it cannot unlink one (the
    old uid-999 `data/redis/.gitkeep` on upgraded hosts) and carries on."""
    for rel in paths:
        parent = (WORKSPACE / rel).parent
        while not parent.exists() and parent != WORKSPACE:
            parent = parent.parent
        if not _owner_can_write_dir(parent, owner):
            return rel
    return None


# --- the infra plan -----------------------------------------------------------


class InfraPlan:
    """What plan_infra decided. A plain class rather than a dataclass: tests load
    this script by path, and @dataclass needs the module in sys.modules."""

    def __init__(self, *, ff_commit: str | None = None, owner: tuple[int, int] | None = None,
                 skip_reason: str | None = None) -> None:
        self.services: list[str] = []
        self.images: dict[str, str] = {}
        self.reasons: dict[str, str] = {}
        self.budgets: dict[str, int] = {}
        self.ff_commit = ff_commit
        self.owner = owner
        self.skip_reason = skip_reason


def _skip(reason: str, tag: str) -> InfraPlan:
    add_warning(f"infra sync skipped: {reason}. The app update continues; to bring "
                f"db/redis/clamav/tusd to {tag} by hand: {_manual_command(tag)}")
    return InfraPlan(skip_reason=reason)


def plan_infra(target_tag: str, opts: dict[str, bool | int], previous_tag: str) -> InfraPlan:
    """Decide which infra services the update recreates. READ-ONLY: nothing on
    the host changes here (a fetch only adds the release tag to .git), because
    the backup that must precede any change has not run yet.

    A service changes when its running image differs from the release's, when
    its `compose config` definition differs, or when a checkout file it
    bind-mounts is touched by the fast-forward. Compose's own recreate decision
    (the config-hash label) is never used: it already differs between host-
    and executor-created containers for identical definitions."""
    if not opts["infra_sync"]:
        log_line("infra sync is switched off (updates.infra_sync); db/redis/clamav/tusd are left alone")
        return InfraPlan(skip_reason="switched off")
    present = [n for n in _OVERRIDE_FILES if (WORKSPACE / n).exists()]
    if present:
        return _skip(f"{present[0]} exists and the updater cannot apply it", target_tag)
    if _env_file_value("COMPOSE_FILE"):
        return _skip("COMPOSE_FILE is set in .env", target_tag)
    if not _SHA_RE.fullmatch(RELEASE_SHA):
        return _skip("this updater image does not record the release commit", target_tag)
    owner = _checkout_owner()
    if owner is None or not (WORKSPACE / ".git").is_dir():
        return _skip("the install is not a git checkout", target_tag)
    shallow = _git(["rev-parse", "--is-shallow-repository"], owner)
    if shallow.returncode != 0:
        return _skip(f"git cannot read the checkout ({_git_error(shallow)})", target_tag)
    if shallow.stdout.strip() == "true":
        return _skip("the checkout is a shallow clone", target_tag)
    tag_commit = _resolve_tag_commit(target_tag, owner)
    if tag_commit is None:
        return _skip(f"{target_tag} could not be fetched", target_tag)
    if tag_commit != RELEASE_SHA:
        return _skip(f"{target_tag} in the checkout is not the commit this release was built from",
                     target_tag)
    head_r = _git(["rev-parse", "HEAD"], owner)
    head = head_r.stdout.strip()
    if head_r.returncode != 0 or not _SHA_RE.fullmatch(head):
        return _skip(f"git cannot resolve HEAD ({_git_error(head_r)})", target_tag)

    current_cfg, _hint = _compose_config(COMPOSE_FILE, previous_tag)
    if current_cfg is None:
        return _skip("docker compose cannot read the current docker-compose.yml", target_tag)
    current_services = current_cfg.get("services") or {}
    mounted = sorted({p for s in INFRA_SYNC_ORDER if isinstance(current_services.get(s), dict)
                      for p in _bind_files(current_services[s])})

    touched: set[str] = set()
    ff_commit: str | None = None
    desired_cfg: dict | None = current_cfg
    if head == tag_commit:
        log_line(f"checkout is at {target_tag}")
    elif _git(["merge-base", "--is-ancestor", head, tag_commit], owner).returncode == 0:
        changed = _git_paths(["diff", "--name-only", "-z", head, tag_commit], owner)
        written = _git_paths(["diff", "--name-only", "-z", "--diff-filter=d", head, tag_commit], owner)
        dirty = _dirty_paths(owner)
        if changed is None or written is None or dirty is None:
            return _skip("git cannot compare the checkout with the release", target_tag)
        touched = set(changed)
        overlap = sorted(touched & dirty)
        if overlap:
            return _skip("local changes to " + ", ".join(overlap[:5])
                         + " would be overwritten by the release", target_tag)
        blocked = _unwritable_for_ff(written, owner)
        if blocked:
            return _skip(f"{blocked} is not writable by the checkout's owner", target_tag)
        if "docker-compose.yml" in touched:
            show = _git(["show", f"{tag_commit}:docker-compose.yml"], owner)
            if show.returncode != 0:
                return _skip(f"git cannot read {target_tag}'s docker-compose.yml", target_tag)
            with tempfile.TemporaryDirectory() as tmp:
                desired_file = Path(tmp) / "docker-compose.yml"
                desired_file.write_text(show.stdout)
                desired_cfg, hint = _compose_config(desired_file, previous_tag)
            if desired_cfg is None:
                return _skip(f"{target_tag}'s docker-compose.yml does not resolve against this "
                             f"host's .env" + (f" ({hint})" if hint else ""), target_tag)
        ff_commit = tag_commit
        log_line(f"checkout is behind {target_tag}; {len(touched)} file(s) change on fast-forward")
    elif _git(["merge-base", "--is-ancestor", tag_commit, head], owner).returncode == 0:
        # Ahead (a host that tracks main): what `docker compose up` on the host
        # would apply is the checkout itself, so that is safe to sync from - but
        # only while it carries no infra change the release does not, or the
        # updater would deploy unreleased infra.
        infra_diff = _git(["diff", "--quiet", tag_commit, head, "--", "docker-compose.yml", *mounted], owner)
        if infra_diff.returncode != 0:
            return _skip(f"the checkout is ahead of {target_tag} and carries infra changes it does not",
                         target_tag)
        log_line(f"checkout is ahead of {target_tag} with the same infra definitions")
    else:
        return _skip(f"the checkout has diverged from {target_tag}", target_tag)

    assert desired_cfg is not None
    desired_services = desired_cfg.get("services") or {}
    plan = InfraPlan(ff_commit=ff_commit, owner=owner)
    for svc in INFRA_SYNC_ORDER:
        new = desired_services.get(svc)
        if not isinstance(new, dict):
            continue
        cid = _container_id(svc)
        if cid is None:
            add_warning(f"{svc} has no container; the infra sync leaves it alone")
            continue
        want = _normalize_image(str(new.get("image", "")))
        running = _normalize_image(_inspect(cid, "{{.Config.Image}}") or "")
        reasons = []
        if want and running != want:
            reasons.append(f"image {running or 'unknown'} -> {want}")
        if _definition(current_services.get(svc)) != _definition(new):
            reasons.append("its compose definition changed")
        files = sorted(_bind_files(new) & touched)
        if files:
            reasons.append("changed " + ", ".join(files))
        if reasons:
            plan.services.append(svc)
            plan.images[svc] = str(new.get("image", ""))
            plan.reasons[svc] = "; ".join(reasons)
            plan.budgets[svc] = _health_budget(svc, new)
            log_line(f"infra plan: {svc}: {plan.reasons[svc]}")
    if not plan.services:
        log_line("infra plan: db, redis, clamav and tusd already match the release")
    return plan


def _definition(svc_cfg: object) -> str:
    """A service's compose definition minus `image`, which the image-drift check
    already reports (and compares against what is RUNNING, not the old file)."""
    if not isinstance(svc_cfg, dict):
        return json.dumps(svc_cfg)
    return json.dumps({k: v for k, v in svc_cfg.items() if k != "image"}, sort_keys=True)


def fast_forward_checkout(plan: InfraPlan, target_tag: str) -> bool:
    assert plan.ff_commit is not None and plan.owner is not None
    result = _git(["merge", "--ff-only", plan.ff_commit], plan.owner, timeout=300)
    for line in (result.stdout + result.stderr).splitlines()[-20:]:
        log_line(f"git: {_URL_CREDENTIALS_RE.sub('://***@', line)}")
    head = _git(["rev-parse", "HEAD"], plan.owner).stdout.strip()
    if result.returncode == 0 and head == plan.ff_commit:
        log_line(f"checkout fast-forwarded to {target_tag}")
        return True
    add_warning(f"the checkout could not be fast-forwarded to {target_tag} ({_git_error(result)}); "
                f"infra was left alone. By hand: {_manual_command(target_tag)}")
    return False


# --- infra sync -----------------------------------------------------------------


def _redis_ready(deadline: float) -> bool:
    """Polls DBSIZE for an INTEGER: redis answers PING while still LOADING the
    dataset, and redis-cli exits 0 on an error reply (the repo's readiness rule
    for restore.sh and the drill)."""
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "redis", "redis-cli", "DBSIZE"],
                capture_output=True, text=True, cwd=str(WORKSPACE), timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip().isdigit():
                log_line(f"redis: dataset loaded ({result.stdout.strip()} keys)")
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(3)
    log_line("TIMEOUT waiting for redis to load its dataset")
    return False


# A just-recreated service that restarted this often is crash-looping under
# `restart: unless-stopped`; waiting out the whole budget (15 min for db) would
# only keep the app down longer before the same verdict.
_CRASH_LOOP_RESTARTS = 3


def wait_service_healthy(service: str, image: str, budget: int) -> bool:
    deadline = time.time() + budget
    want = _normalize_image(image)
    last = ""
    running_since: float | None = None
    while time.time() < deadline:
        cid = _container_id(service)
        info = _inspect(cid, "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"
                             "|{{.Config.Image}}|{{.RestartCount}}") if cid else None
        if info:
            status, health, running, restarts = (info.split("|") + ["", "", "", ""])[:4]
            if info != last:
                log_line(f"{service}: {status}{'/' + health if health else ''} on {running}"
                         + (f" (restarted {restarts}x)" if restarts not in ("", "0") else ""))
                last = info
            if restarts.isdigit() and int(restarts) >= _CRASH_LOOP_RESTARTS:
                log_line(f"{service} is crash-looping ({restarts} restarts); not waiting any longer")
                return False
            on_release = _normalize_image(running) == want
            # A service without a healthcheck counts once it has stayed up 10s.
            waiting = on_release and status == "running" and not health
            running_since = (running_since or time.time()) if waiting else None
            no_healthcheck_ok = running_since is not None and time.time() - running_since >= 10
            if on_release and status == "running" and (health == "healthy" or no_healthcheck_ok):
                if service == "redis" and not _redis_ready(deadline):
                    return False
                if service == "db":
                    run_capture(["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "db",
                                 "mariadb", "--version"])
                return True
        time.sleep(3)
    log_line(f"TIMEOUT waiting for {service} to become healthy ({budget}s)")
    return False


def sync_infra(plan: InfraPlan, previous_tag: str, backup_dir: str | None) -> int:
    """Recreate the planned infra services, in order, each health-gated.

    For db and redis the backend and worker are stopped first (raw `docker
    stop`, which cannot cascade the way `compose stop` might) - the app would
    otherwise serve 500s against a missing database. There is no automatic
    infra rollback: MariaDB and Redis majors cannot go back in place, which is
    what the pre-update backup is for."""
    env = _compose_env(previous_tag)
    stopped: list[str] = []
    if DATA_SERVICES & set(plan.services):
        stopped = [c for c in (_container_id(s) for s in ("backend", "worker")) if c]
        if stopped:
            log_line("stopping backend and worker while the data layer is recreated")
            run_capture(["docker", "stop", "-t", "30", *stopped])
    for svc in plan.services:
        log_line(f"infra: recreating {svc} ({plan.reasons[svc]})")
        # --force-recreate: compose's hash does not cover the CONTENT of a
        # bind-mounted file (clamd.conf), so a changed one would not restart.
        # --pull never: the image was pulled before anything went down.
        rc = run_capture(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--no-deps",
             "--force-recreate", "--pull", "never", svc],
            env=env,
        )
        if rc == 0 and wait_service_healthy(svc, plan.images[svc], plan.budgets[svc]):
            continue
        run_capture(["docker", "compose", "-f", str(COMPOSE_FILE), "logs", "--no-color", "--tail", "50", svc],
                    env=env)
        if svc not in DATA_SERVICES:
            add_warning(f"{svc} did not come up healthy on {plan.images[svc]}; the update continues - "
                        f"check `docker compose logs {svc}`")
            continue
        if stopped:
            log_line("starting the previous backend and worker again")
            run_capture(["docker", "start", *stopped])
        where = (f"the pre-update backup is {backup_dir}" if backup_dir
                 else "no pre-update backup was taken")
        write_job_field(
            status="failed",
            error=(f"{svc} did not become healthy on {plan.images[svc]}; the app stays on {previous_tag} "
                   f"and {where}. See README › Restoring a pre-update backup."),
            finished_at=utcnow_iso(),
        )
        return 20
    return 0


# --- pre-update backup ----------------------------------------------------------


def _db_credentials(cfg: dict) -> tuple[str, str] | None:
    env = ((cfg.get("services") or {}).get("db") or {}).get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
    if not isinstance(env, dict):
        return None
    password = env.get("MARIADB_ROOT_PASSWORD") or env.get("MYSQL_ROOT_PASSWORD")
    name = env.get("MARIADB_DATABASE") or env.get("MYSQL_DATABASE") or "fileheron"
    if not isinstance(password, str) or not password or not isinstance(name, str) or name.startswith("-"):
        return None
    return name, password


def _owner_of(*candidates: Path) -> tuple[int, int] | None:
    for path in candidates:
        try:
            st = path.stat()
        except OSError:
            continue
        return st.st_uid, st.st_gid
    return None


def _secure_tree(root: Path, owner: tuple[int, int] | None) -> None:
    """0700/0600 (the dump holds password hashes and encrypted secrets), owned by
    the checkout's owner so the host user who runs backup.sh and restore.sh can
    read and delete it - the executor runs as root."""
    for path in [root, *root.rglob("*")]:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        if owner is not None and os.geteuid() == 0:
            os.chown(path, owner[0], owner[1])


def _du(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _db_size_bytes(db_name: str, password: str) -> int | None:
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "-e", "MYSQL_PWD", "db",
             "mariadb", "-uroot", "-N", "-B", "-e",
             "SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.tables "
             "WHERE table_schema = DATABASE()", db_name],
            capture_output=True, text=True, cwd=str(WORKSPACE), timeout=120,
            env={**os.environ, "MYSQL_PWD": password},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = result.stdout.strip()
    return int(out) if result.returncode == 0 and out.isdigit() else None


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:40] or "unknown"


def _dump_database(dest: Path, db_name: str, password: str) -> bool:
    """mariadb-dump with the flags scripts/backup.sh uses. The password travels
    as MYSQL_PWD in the environment (`-e MYSQL_PWD` forwards it), never in argv."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "-e", "MYSQL_PWD", "db",
           "mariadb-dump", "-uroot", "--single-transaction", "--quick", "--lock-tables=false", db_name]
    log_line("$ " + " ".join(shlex.quote(c) for c in cmd) + " > db.sql")
    deadline = time.time() + 2 * 3600
    with open(dest, "wb") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.PIPE, cwd=str(WORKSPACE),
                                env={**os.environ, "MYSQL_PWD": password})
        while True:
            try:
                _, err = proc.communicate(timeout=60)
                break
            except subprocess.TimeoutExpired:
                if time.time() > deadline:
                    proc.kill()
                    proc.communicate()
                    log_line("ERROR mariadb-dump ran for more than 2 hours; stopped")
                    return False
                log_line(f"backup: db.sql {dest.stat().st_size // (1024 * 1024)} MiB so far")
    for line in err.decode(errors="replace").splitlines()[-5:]:
        log_line(f"mariadb-dump: {line}")
    if proc.returncode != 0:
        log_line(f"ERROR mariadb-dump exited {proc.returncode}")
        return False
    with open(dest, "rb") as f:
        f.seek(max(0, dest.stat().st_size - 4096))
        if b"-- Dump completed" not in f.read():
            log_line("ERROR db.sql does not end with mariadb-dump's completion marker")
            return False
    return True


def _snapshot_redis(dest: Path) -> bool:
    try:
        save = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "redis", "redis-cli", "SAVE"],
            capture_output=True, text=True, cwd=str(WORKSPACE), timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log_line(f"ERROR redis SAVE failed: {type(e).__name__}")
        return False
    if save.returncode != 0 or save.stdout.strip() != "OK":
        log_line(f"ERROR redis SAVE answered {save.stdout.strip()[:100]!r}")
        return False
    cid = _container_id("redis")
    if cid is None or run_capture(["docker", "cp", f"{cid}:/data/dump.rdb", str(dest)]) != 0:
        log_line("ERROR could not copy redis's dump.rdb")
        return False
    with open(dest, "rb") as f:
        if f.read(5) != b"REDIS":
            log_line("ERROR redis.rdb does not start with the REDIS magic")
            return False
    return True


def take_pre_update_backup(previous_tag: str, target_tag: str, alembic_head: str | None) -> str | None:
    """Dump MariaDB and snapshot Redis into backups/pre-update/<stamp>_<from>-to-<to>/.

    Under pre-update/ on purpose: backup.sh's keep-7 counts only backups/*/ with a
    manifest (the parent has none), the drill picks backups/20*, and restic
    pushes backups/<stamp> - so these are never mistaken for, or evict, a nightly
    backup. restore.sh restores one as DB+Redis only (manifest without the
    tarballs). Returns the workspace-relative directory, or None on failure,
    with nothing left behind."""
    cfg, _hint = _compose_config(COMPOSE_FILE, previous_tag)
    creds = _db_credentials(cfg) if cfg else None
    if creds is None:
        log_line("ERROR backup: cannot read the database name and root password from the compose config")
        return None
    db_name, password = creds
    backups = WORKSPACE / "backups"
    for path in (backups, BACKUP_ROOT):
        if path.is_symlink() and not path.exists():
            log_line(f"ERROR backup: {path.relative_to(WORKSPACE)} is a symlink the updater cannot follow "
                     "from inside its container; point it inside the checkout or turn off "
                     "updates.backup_on_db_change and back up by hand")
            return None
    owner = _owner_of(backups, WORKSPACE / ".git", WORKSPACE)
    created_backups = not backups.exists()
    try:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log_line(f"ERROR backup: cannot create backups/pre-update ({e})")
        return None
    for path in ([backups] if created_backups else []) + [BACKUP_ROOT]:
        if owner is not None and os.geteuid() == 0:
            os.chown(path, owner[0], owner[1])
        os.chmod(path, 0o700 if path == BACKUP_ROOT else 0o755)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    name = f"{stamp}_{_safe_label(previous_tag)}-to-{_safe_label(target_tag)}"
    partial = BACKUP_ROOT / f".partial-{name}"
    final = BACKUP_ROOT / name
    try:
        partial.mkdir(mode=0o700)
        db_bytes = _db_size_bytes(db_name, password)
        redis_bytes = _du(WORKSPACE / "data" / "redis")
        if db_bytes is None:
            log_line("WARN backup: could not size the database; requiring 1 GiB free")
            needed = 1024 ** 3
        else:
            needed = int(db_bytes * 1.2) + redis_bytes + 1024 ** 3
        free = shutil.disk_usage(partial).free
        log_line(f"backup: {free // 1024 ** 2} MiB free, {needed // 1024 ** 2} MiB required")
        if free < needed:
            log_line("ERROR backup: not enough free disk space for the pre-update backup")
            return None
        if not _dump_database(partial / "db.sql", db_name, password):
            return None
        if not _snapshot_redis(partial / "redis.rdb"):
            return None
        db_cid = _container_id("db")
        kind = {
            "kind": "pre-update",
            "components": "db,redis",
            "from_tag": previous_tag,
            "to_tag": target_tag,
            "alembic_head": alembic_head or "",
            "db_image": (_inspect(db_cid, "{{.Config.Image}}") if db_cid else None) or "",
            "created_at": utcnow_iso(),
        }
        (partial / "KIND").write_text("".join(f"{k}={v}\n" for k, v in kind.items()))
        lines = []
        for artifact in ("db.sql", "redis.rdb", "KIND"):
            digest = hashlib.sha256()
            with open(partial / artifact, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            lines.append(f"{digest.hexdigest()}  {artifact}\n")
        (partial / "manifest.txt").write_text("".join(lines))
        os.replace(partial, final)
    except OSError as e:
        log_line(f"ERROR backup: {type(e).__name__}: {e}")
        return None
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
    _secure_tree(final, owner)
    rel = f"backups/pre-update/{name}"
    log_line(f"backup: wrote {rel} ({_du(final) // 1024 ** 2} MiB)")
    return rel


def prune_pre_update_backups(keep: int, max_age_days: int, protect: str | None = None) -> None:
    """Retention for pre-update backups (updates.backup_keep /
    updates.backup_max_age_days, 0 = no limit). The newest one is never
    deleted, whatever its age: it is the only way back from the last update.
    Runs on every update, so between updates at most that one lingers."""
    if not BACKUP_ROOT.is_dir() or BACKUP_ROOT.is_symlink():
        return
    now = datetime.now(timezone.utc)
    entries = sorted(
        (p for p in BACKUP_ROOT.iterdir()
         if p.is_dir() and not p.is_symlink() and _BACKUP_NAME_RE.fullmatch(p.name)
         and (p / "manifest.txt").is_file()),
        key=lambda p: p.name, reverse=True,
    )
    for index, path in enumerate(entries):
        if index == 0 or path.name == protect:
            continue
        too_many = keep > 0 and index >= keep
        try:
            created = datetime.strptime(path.name[:17], "%Y-%m-%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        too_old = max_age_days > 0 and now - created > timedelta(days=max_age_days)
        if too_many or too_old:
            shutil.rmtree(path, ignore_errors=True)
            log_line(f"backup retention: removed backups/pre-update/{path.name}"
                     f" ({'over the limit of ' + str(keep) if too_many else f'older than {max_age_days} days'})")
    for path in BACKUP_ROOT.glob(".partial-*"):
        try:
            stale = time.time() - path.lstat().st_mtime > _PARTIAL_MAX_AGE_SEC
        except OSError:
            continue
        if stale and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
            log_line(f"backup retention: removed an abandoned {path.name}")


def main() -> int:
    job = read_job()
    target_tag = job.get("target_tag")
    action = job.get("action", "update")
    if action not in _ACTIONS:
        # Anything else fell through to the update path with auto-rollback
        # switched off (every recovery branch below tests `== "update"`). The
        # job file is backend-writable, so it is validated here like the tag.
        log_line("ERROR job action is neither update nor rollback")
        write_job_field(status="failed", error="invalid action", finished_at=utcnow_iso())
        return 1
    if not target_tag:
        log_line("ERROR no target_tag in state file")
        write_job_field(status="failed", error="no target_tag", finished_at=utcnow_iso())
        return 1
    if not _is_valid_tag(target_tag):
        # Never interpolate the value into the message: it lands in the state
        # file the admin UI renders, and it is the untrusted thing here.
        log_line(f"ERROR target_tag is not a release tag (len={len(str(target_tag))})")
        write_job_field(
            status="failed", error="invalid target_tag", finished_at=utcnow_iso()
        )
        return 1

    previous_tag = read_current_tag()
    # A floating tag is not a rollback anchor - see resolve_running_version.
    # `previous_tag` is still used for the .env rewrite (it is what the stack is
    # currently running under); only the RECORDED rollback target is pinned.
    rollback_anchor = previous_tag
    if previous_tag in ("latest", ""):
        resolved = resolve_running_version()
        if resolved:
            log_line(f"resolved floating tag {previous_tag!r} to {resolved} for rollback")
            rollback_anchor = resolved
        else:
            log_line(
                "WARN running under a floating tag and the version could not be "
                "resolved; a rollback would redeploy the same image"
            )
    # What an auto-rollback should wait to see: the concrete version, or None
    # when the floating tag could not be resolved (see auto_rollback).
    restored_version = None if rollback_anchor in ("latest", "") else rollback_anchor
    # Capture the DB's current head from the still-running OLD backend, so a
    # rollback (auto or manual) can stamp the version pointer back across any
    # migration the new image applies.
    previous_head = capture_alembic_head()
    write_job_field(
        status="pulling",
        previous_tag=previous_tag,
        previous_alembic_head=previous_head,
        started_at=utcnow_iso(),
    )
    log_line(f"previous tag={previous_tag} alembic_head={previous_head}; target {target_tag} (action={action})")
    opts = _read_options(job)

    # Which infra services this update brings to the release. Read-only, and
    # updates only: a rollback never touches git or infra (infra is
    # roll-forward only, and a rollback runs the OLDER release's executor
    # anyway).
    plan = InfraPlan(skip_reason="rollback")
    if action == "update":
        set_phase("planning")
        plan = plan_infra(target_tag, opts, previous_tag)

    # Pull every image (including updater-shim/-executor) so the local
    # cache stays warm for the next click - and the planned infra images, so
    # none of them is downloaded while the data layer is down.
    set_phase("pulling")
    for img in IMAGES_TO_PULL:
        ref = f"ghcr.io/{GHCR_OWNER}/fileheron-{img}:{target_tag}"
        if _pull(ref) != 0:
            write_job_field(
                status="failed",
                error=f"pull failed for {ref}",
                finished_at=utcnow_iso(),
            )
            return 2
    for svc in plan.services:
        if _pull(plan.images[svc], quiet=True) != 0:
            write_job_field(
                status="failed",
                error=f"pull failed for {plan.images[svc]} ({svc}); nothing was changed",
                finished_at=utcnow_iso(),
            )
            return 2

    # The backup runs after the pulls (so the window of writes it cannot see is
    # the dump itself, not a download) and BEFORE anything on the host changes:
    # if it fails, the update stops with the old version untouched.
    backup_dir: str | None = None
    if action == "update":
        set_phase("backing_up")
        forced = "db" in plan.services and bool(opts["backup_on_db_change"])
        if opts["backup"] or forced:
            if forced and not opts["backup"]:
                log_line("the release changes the database service; backing up first "
                         "(updates.backup_on_db_change)")
            backup_dir = take_pre_update_backup(previous_tag, target_tag, previous_head)
            if backup_dir is None:
                write_job_field(
                    status="failed",
                    error="pre-update backup failed; nothing was changed (see the log)",
                    finished_at=utcnow_iso(),
                )
                return 6
            write_job_field(backup_dir=backup_dir)
        else:
            log_line("pre-update backup not requested")
        prune_pre_update_backups(int(opts["backup_keep"]), int(opts["backup_max_age_days"]),
                                 protect=backup_dir.rsplit("/", 1)[-1] if backup_dir else None)
        if plan.ff_commit:
            set_phase("syncing_checkout")
            if not fast_forward_checkout(plan, target_tag):
                plan = InfraPlan(skip_reason="fast-forward failed")

    # Manual rollback: reconcile the DB pointer to the target's recorded head
    # using the CURRENT (new) image (superset tree) BEFORE swapping .env down -
    # otherwise the old image's boot-time `alembic upgrade head` dies with
    # "Can't locate revision". .env still holds previous_tag (the new image)
    # here. stamp is non-destructive (see auto_rollback).
    if action == "rollback":
        rb_head = _read_rollback_file().get("alembic_head")
        if rb_head and not _is_valid_head(rb_head):
            log_line("ERROR recorded rollback head is not a revision id")
            write_job_field(
                status="failed",
                error="rollback: invalid recorded alembic head",
                finished_at=utcnow_iso(),
            )
            return 5
        if rb_head:
            log_line(f"rollback: stamping DB back to {rb_head} using current image ({previous_tag})")
            if run_capture(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "run", "--rm",
                 "--no-deps", "--entrypoint", "alembic", "backend", "stamp", rb_head],
                env=_compose_env(previous_tag),
            ) != 0:
                write_job_field(status="failed", error="rollback: alembic stamp failed",
                                finished_at=utcnow_iso())
                return 5
        else:
            log_line("WARN rollback target has no alembic_head (legacy) - skipping stamp "
                     "(pre-fix behavior; may hit the migration trap)")

    # Infra before the app, so the new app is health-checked against the
    # release's infra. A db/redis failure returns here, BEFORE FH_TAG and the
    # rollback target move: the old app is started again on the old tag.
    if plan.services:
        write_job_field(status="restarting")
        set_phase("syncing_infra")
        rc = sync_infra(plan, previous_tag, backup_dir)
        if rc != 0:
            return rc

    # Persist FH_TAG before up -d so a mid-recreate crash leaves the
    # next `docker compose up` consistent with the requested tag.
    write_current_tag(target_tag)
    # Record where a later rollback should go, including the pre-update alembic
    # head so it can stamp the DB pointer back across any migration this update
    # applied.
    #
    # A ROLLBACK also has to update it, and used to not: after rolling B -> A
    # the file still said "roll back to A", so the SPA offered a Rollback button
    # that would have redeployed the version the operator was already on, while
    # the version they had just fled (B) was no longer recorded anywhere (audit
    # 2026-07-30, flow-selfupdate-10). The rollback target after rolling back is
    # the tag we just left.
    if previous_tag != target_tag:
        try:
            _write_rollback_file(rollback_anchor, previous_head)
            log_line(f"rollback target recorded: {rollback_anchor} (head={previous_head})")
        except Exception as e:
            log_line(f"WARN rollback-target write failed: {e}")

    write_job_field(status="restarting")
    set_phase("restarting_app")
    # _compose_env forwards COMPOSE_HOST_ROOT so bind-mount sources resolve
    # against the HOST compose dir, not the executor's /workspace (omitting
    # it auto-creates shadow data dirs at /workspace/data/* and forks the
    # data layer).
    if run_capture(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--no-deps"] + SERVICES,
        env=_compose_env(target_tag),
    ) != 0:
        # An UPDATE that won't even start self-heals to the previous tag; a
        # ROLLBACK that won't start must NOT auto-forward to the version it's
        # fleeing - fail loudly for the operator (DB already stamped to the
        # target's head, .env left at the requested target).
        if action == "update":
            return auto_rollback(previous_tag, previous_head, target_tag,
                                 reason="docker compose up -d failed",
                                 expected_version=restored_version)
        write_job_field(status="failed", error="rollback: docker compose up -d failed",
                        finished_at=utcnow_iso())
        return 3

    set_phase("verifying")
    if not wait_for_backend_health(expected_tag=target_tag):
        if action == "update":
            return auto_rollback(previous_tag, previous_head, target_tag,
                                 reason="backend health check timed out",
                                 expected_version=restored_version)
        write_job_field(status="failed", error="rollback: backend health check timed out",
                        finished_at=utcnow_iso())
        return 4

    write_job_field(status="healthy", finished_at=utcnow_iso())

    # Recreate the shim LAST, after the terminal status is written.
    #
    # "The perpetual shim never updates itself" held right up until v2.5.0
    # shipped a fix TO the shim - which then could not reach a single
    # instance, because neither the in-app update nor the release's host step
    # recreates it. The release notes said it was fixed (audit #2).
    #
    # Ordering is load-bearing: the new shim's startup sweep marks any
    # non-terminal job failed, so this must not run while the job is still
    # in-flight. And the executor is a `docker run` sibling, not a compose
    # child of the shim, so replacing its parent does not kill it. A failure
    # here is logged, never fatal - the update itself has already succeeded.
    try:
        rc = run_capture(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--no-deps",
             "updater-shim"],
            env=_compose_env(target_tag),
        )
        log_line(
            "updater-shim recreated" if rc == 0
            else f"WARN could not recreate updater-shim (exit {rc}); it stays on the old image"
        )
    except Exception as e:
        log_line(f"WARN could not recreate updater-shim: {type(e).__name__}: {e}")

    log_line(f"DONE - running on {target_tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
