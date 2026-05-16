# fileHeron self-update sidecar. Owns /var/run/docker.sock and runs
# `docker compose pull` + `up -d` on behalf of the backend, which can't
# (and shouldn't) reach the host Docker daemon itself.
#
# Trust model: the backend POSTs HMAC-signed requests to this container
# over the internal compose network. No Traefik route, no published
# port — only reachable from inside the compose project.
FROM python:3.12-alpine

WORKDIR /app

# docker CLI + compose plugin are needed because the Python `docker` SDK
# doesn't speak compose; the cleanest "match what an operator would
# type" approach is to shell out to the same binaries.
RUN apk add --no-cache docker-cli docker-cli-compose curl

ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
ENV FH_VERSION=${FH_VERSION} \
    FH_GIT_SHA=${FH_GIT_SHA}
LABEL org.opencontainers.image.version="${FH_VERSION}" \
      org.opencontainers.image.revision="${FH_GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/phoen-ix/fileHeron"

COPY docker/updater/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY docker/updater/app.py /app/app.py

EXPOSE 9000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:9000/health || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
