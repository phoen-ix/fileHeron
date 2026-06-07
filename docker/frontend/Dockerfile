# Multi-stage: build Vite output, then serve the static SPA from nginx:alpine.
# The backend serves /api on its own container; this image only ships the SPA.

FROM node:22-alpine AS build
WORKDIR /build

# Baked-in version metadata. Vite picks up VITE_* env vars at build time
# and inlines them into the bundle so the SPA can show "running version".
ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
ENV VITE_FH_VERSION=${FH_VERSION} \
    VITE_FH_GIT_SHA=${FH_GIT_SHA}

# Copy manifests first for dep cache. The committed lockfile is required (no `*`
# glob): `npm ci` installs the EXACT locked tree and fails loudly if it's missing
# or out of sync with package.json - reproducible, integrity-checked builds
# instead of resolving `^` ranges fresh on every build (audit M11).
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
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/healthz || exit 1
