# Multi-stage: build Vite output, then serve the static SPA from nginx:alpine.
# The backend serves /api on its own container; this image only ships the SPA.

FROM node:22-alpine AS build
WORKDIR /build

# Copy manifests first for dep cache.
COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm npm install

# Copy the rest and build.
COPY frontend/ ./
RUN npm run build

# Stage 2: serve.
FROM nginx:alpine

COPY docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/healthz || exit 1
