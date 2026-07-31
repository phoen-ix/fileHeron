"""Assertions on the build context, the images and the compose file.

None of these surfaces had any gate at all before the 2026-07-30 audit, and
each defect here is invisible to `docker compose config` and to hadolint:

docker-4  `.dockerignore`'s `node_modules/` is ANCHORED to the context root, so
          it excluded a directory that does not exist while frontend/node_modules
          - 207 MB, root-owned on the deploy host - was shipped into every build
          context.
docker-7  `env_file: .env` handed the DB *root* password and the restic
          repository password to the two containers that process untrusted
          uploads. The app ignores them as extra keys; that is not the same as
          them not being there.
docker-10 Redis's `maxmemory` equalled its container `mem_limit`, so memory
          pressure was resolved by the kernel OOM-killing redis-server rather
          than by Redis refusing the write - which is the entire point of
          choosing `noeviction`.
docker-11 the frontend image's own HEALTHCHECK used `localhost`, the exact form
          the compose file documents as broken for this image (busybox wget
          resolves it to ::1; nginx here listens on IPv4 only).
docker-12 the backend runtime image carried gcc and the MariaDB headers, used
          only to build wheels that all ship prebuilt - a C toolchain living in
          the container that handles uploads.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def dockerignore() -> list[str]:
    return [
        ln.strip()
        for ln in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


@pytest.fixture(scope="module")
def compose() -> str:
    return (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


# --- docker-4 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"]
)
def test_heavy_directories_are_ignored_at_any_depth(dockerignore, name):
    """A bare `foo/` pattern only matches at the context root. Every one of
    these lives in a subdirectory."""
    anchored = f"{name}/"
    globbed = f"**/{name}/"
    assert globbed in dockerignore, (
        f"'{anchored}' only excludes a root-level {name}; the real one is in a "
        f"subdirectory and was copied into every build context"
    )


def test_the_build_context_excludes_the_frontend_build_output(dockerignore):
    """`dist/` is rebuilt inside the image; shipping the host's copy in means
    whatever was last built locally rides along."""
    assert "**/dist/" in dockerignore


# --- docker-12 ---------------------------------------------------------------


def test_the_backend_runtime_image_has_no_compiler():
    dockerfile = (ROOT / "docker" / "backend" / "Dockerfile").read_text(encoding="utf-8")
    installs = re.findall(r"apt-get install[^\n]*(?:\\\n[^\n]*)*", dockerfile)
    joined = " ".join(installs)
    for banned in ("gcc", "g++", "build-essential", "libmariadb-dev"):
        assert banned not in joined, (
            f"{banned} is back in the runtime image; if a dep needs to build "
            "from source the answer is a builder stage, not a compiler in "
            "production"
        )


def test_the_healthcheck_dependency_is_still_installed():
    """Control: curl backs the compose HEALTHCHECK. Removing it would make every
    backend container report unhealthy."""
    dockerfile = (ROOT / "docker" / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "curl" in dockerfile


# --- docker-11 ---------------------------------------------------------------


def test_the_frontend_image_healthcheck_uses_an_ipv4_literal():
    """busybox wget resolves `localhost` to ::1 first and nginx here listens on
    IPv4 only - which is why the compose healthcheck spells out 127.0.0.1. The
    image's built-in one did not, so it reported unhealthy while serving."""
    dockerfile = (ROOT / "docker" / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    hc = [ln for ln in dockerfile.splitlines() if "healthz" in ln]
    assert hc, "the frontend image has no healthcheck"
    for line in hc:
        assert "127.0.0.1" in line, line
        assert "localhost" not in line, line


def test_the_compose_healthcheck_agrees_with_it(compose):
    """The two used to disagree, which is how one of them stayed wrong."""
    assert "http://127.0.0.1/healthz" in compose


# --- docker-10 ---------------------------------------------------------------


def test_redis_maxmemory_leaves_headroom_under_its_container_limit(compose):
    mem = re.search(r"mem_limit:\s*\$\{REDIS_MEM_LIMIT:-(\d+)m\}", compose)
    maxmem = re.search(r"--maxmemory\s+\$\{REDIS_MAXMEMORY:-(\d+)mb\}", compose)
    assert mem and maxmem, "could not read the two limits"
    limit, cap = int(mem.group(1)), int(maxmem.group(1))
    assert cap < limit, (
        f"maxmemory {cap}mb == container limit {limit}m: pressure is resolved "
        "by the OOM killer, not by Redis refusing the write"
    )
    assert cap <= limit * 0.85, (
        "Redis needs headroom above maxmemory for copy-on-write and client "
        f"buffers; {cap}mb of {limit}m leaves too little"
    )


def test_redis_still_refuses_rather_than_evicting(compose):
    """Control: eviction would silently drop rate-limit buckets and ARQ jobs."""
    assert "--maxmemory-policy noeviction" in compose


# --- docker-7 ----------------------------------------------------------------


_HOST_ONLY_SECRETS = ["DB_ROOT_PASSWORD", "BACKUP_RESTIC_PASSWORD", "BACKUP_RESTIC_REPO"]


@pytest.mark.parametrize("key", _HOST_ONLY_SECRETS)
def test_operator_only_secrets_are_blanked_for_the_app_containers(compose, key):
    """`environment:` wins over `env_file:`, so an explicit empty value is what
    keeps these out of the containers that handle untrusted uploads."""
    # Both app services must blank it; the db service legitimately consumes
    # DB_ROOT_PASSWORD as MYSQL_ROOT_PASSWORD.
    assert compose.count(f'{key}: ""') >= 2, (
        f"{key} is not blanked for backend and worker"
    )


def test_the_database_still_gets_its_root_password(compose):
    """Control: blanking these must not break the one service that needs one."""
    assert "MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD:?" in compose
