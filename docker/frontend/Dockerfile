# Multi-stage: build Vite output, then serve the static SPA from nginx:alpine.
# The backend serves /api on its own container; this image only ships the SPA.

FROM node:24-alpine AS build
WORKDIR /build

# Baked-in version metadata. Vite picks up VITE_* env vars at build time
# and inlines them into the bundle so the SPA can show "running version".
ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
# Empty default -> the SPA falls back to its built-in 100 MB direct/resumable
# split (see useUpload.ts). e2e sets it to 1 to force the resumable path.
ARG VITE_DIRECT_UPLOAD_THRESHOLD=
ENV VITE_FH_VERSION=${FH_VERSION} \
    VITE_FH_GIT_SHA=${FH_GIT_SHA} \
    VITE_DIRECT_UPLOAD_THRESHOLD=${VITE_DIRECT_UPLOAD_THRESHOLD}

# Copy manifests first for dep cache. The committed lockfile is required (no `*`
# glob): `npm ci` installs the EXACT locked tree and fails loudly if it's missing
# or out of sync with package.json - reproducible, integrity-checked builds
# instead of resolving `^` ranges fresh on every build (audit M11).
#
# There is deliberately no .npmrc any more. It carried legacy-peer-deps, needed
# only because the app stayed on pinia 2 while vue-router 5 declared an optional
# peer on pinia 3. pinia 4 satisfies that peer, so the flag went - and with it
# the blanket suppression of EVERY peer conflict, not just that one.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

# Copy the rest and build.
COPY frontend/ ./
RUN npm run build

# Stage 2: serve.
FROM nginx:alpine

ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
LABEL org.opencontainers.image.version="${FH_VERSION}" \
      org.opencontainers.image.revision="${FH_GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/phoen-ix/fileHeron"

COPY docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
# 127.0.0.1, not `localhost`: nginx in this image listens on IPv4 only, and
# busybox wget resolves `localhost` to ::1 first - which is exactly why the
# compose healthcheck for this service spells it out. The image's own built-in
# HEALTHCHECK kept the broken form, so anyone running the image outside this
# compose file (or reading it to learn the right invocation) got a container
# that reports unhealthy while serving perfectly (audit 2026-07-30, docker-11).
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1/healthz || exit 1
