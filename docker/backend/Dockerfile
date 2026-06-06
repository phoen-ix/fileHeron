# Production image — minimal, non-root, slim Python.
FROM python:3.12-slim

WORKDIR /app

# Baked-in version metadata. Defaults match version.py's source-tree
# fallbacks so an arg-less `docker compose build` keeps working for dev.
# The Actions release workflow passes both args on v* tag push.
ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
ENV FH_VERSION=${FH_VERSION} \
    FH_GIT_SHA=${FH_GIT_SHA}
LABEL org.opencontainers.image.version="${FH_VERSION}" \
      org.opencontainers.image.revision="${FH_GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/phoen-ix/fileHeron"

# System deps for pymysql/cryptography wheels + healthcheck (urllib via stdlib)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libmariadb-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (cache layer)
COPY backend/pyproject.toml ./pyproject.toml
RUN --mount=type=cache,target=/root/.cache/pip pip install .

# App code
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts ./scripts

# Import-smoke against the exact dependency closure `pip install .` produced.
# Fails the build if any module reachable from the app (uvicorn) or the worker
# (arq) is imported in code but absent from pyproject [project].dependencies —
# the gap that shipped v1.25.0/.1 without nh3 and crash-looped prod. Pure import
# resolution: ENVIRONMENT defaults to development, engines/clients connect lazily,
# so it needs no DB/Redis/network. This image backs both the backend and worker.
RUN python -c "import app.main; import app.workers.worker"

# Entrypoint (must be +x AND owned by appuser)
COPY docker/backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Non-root. UID 1000 is conventional for the first user on most
# Linux distros — chosen so bind-mounted volumes (./data/*) end up
# owned by the host's first user without extra chown gymnastics.
# If your host's UID != 1000, override at build time with
# `--build-arg APP_UID=<n>` (then surface it here as a numeric).
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
# --proxy-headers tells uvicorn to honor X-Forwarded-For/Proto from the
# reverse proxy. --forwarded-allow-ips="*" trusts any source — safe here
# because the backend port is bound to 127.0.0.1 in compose; external
# clients can never reach uvicorn directly, so any incoming connection
# is by definition from a trusted proxy (Traefik on the host, or the
# nginx-spa container on the internal network).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
