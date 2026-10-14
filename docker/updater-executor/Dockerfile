# fileHeron updater-executor - ephemeral. Spawned per-update by the
# shim (`docker/updater-shim/`), does one job (pull images, optionally
# back up the database, fast-forward the host checkout and recreate the
# infra services the release changed, rewrite FH_TAG, `docker compose up
# -d backend worker frontend`, poll backend health), then exits. The exit code is the job status; progress
# updates land in /state/current_job.json so backend can show them
# to the admin in real time.
#
# Why split from the shim? Because the executor's logic changes
# release-to-release; the shim's doesn't. Splitting lets the executor
# evolve freely without chicken-and-egging the only container that
# can recreate the others.
FROM python:3.15.0rc3-alpine3.24

WORKDIR /app

# docker CLI + compose plugin for the up -d call. curl for the health
# probe (avoids a Python HTTP dep beyond stdlib). git to fast-forward the
# host checkout to the release (run as the checkout's owner, never root).
#
# Pinned by MAJOR (`=~N`, apk's prefix match). Alpine keeps only the newest
# build of a package in a branch, so an exact pin fails the build on the day a
# security fix lands; a major pin lets those fixes in and fails only on a major
# jump - docker 30, compose 6, git 3 - the change worth a look. ci.yml builds
# this image on every push, so a pin that stops resolving shows up there and
# not on a release tag.
RUN apk add --no-cache "docker-cli=~29" "docker-cli-compose=~5" "curl=~8" "git=~2"

ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
ENV FH_VERSION=${FH_VERSION} \
    FH_GIT_SHA=${FH_GIT_SHA}
LABEL org.opencontainers.image.version="${FH_VERSION}" \
      org.opencontainers.image.revision="${FH_GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/phoen-ix/fileHeron"

COPY docker/updater-executor/run.py /app/run.py

# Single-shot: exit code is the job status. No long-running daemon.
ENTRYPOINT ["python", "/app/run.py"]
