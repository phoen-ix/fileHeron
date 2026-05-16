# fileHeron updater-executor — ephemeral. Spawned per-update by the
# shim (`docker/updater-shim/`), does one job (pull images, rewrite
# FH_TAG, `docker compose up -d backend worker frontend`, poll backend
# health), then exits. The exit code is the job status; progress
# updates land in /state/current_job.json so backend can show them
# to the admin in real time.
#
# Why split from the shim? Because the executor's logic changes
# release-to-release; the shim's doesn't. Splitting lets the executor
# evolve freely without chicken-and-egging the only container that
# can recreate the others.
FROM python:3.12-alpine

WORKDIR /app

# docker CLI + compose plugin for the up -d call. curl for the health
# probe (avoids a Python HTTP dep beyond stdlib).
RUN apk add --no-cache docker-cli docker-cli-compose curl

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
