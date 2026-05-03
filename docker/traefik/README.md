# Sample Traefik configuration for fileHeron

The recommended deploy runs Traefik **on the host** (not in this
compose file) so it can serve multiple apps from one TLS endpoint.
This directory holds copy-paste-ready configs for that setup.

The compose stack publishes on `127.0.0.1:${APP_BACKEND_PORT}` (default
8000) and `127.0.0.1:${APP_FRONTEND_PORT}` (default 8080) — Traefik
proxies external HTTPS to those local ports.

---

## Critical operator rules

1. **Never expose `/api/internal/*`** to the public internet. The
   tusd webhook receiver lives there with HMAC envelope auth +
   optional source-IP allowlist (see `TUS_HOOK_ALLOWED_IPS`), but
   defense-in-depth requires the proxy to also refuse the path.
2. **TLS terminates at Traefik.** The backend reads
   `X-Forwarded-Proto` to decide whether to issue Secure cookies —
   if the proxy strips that header, login silently breaks. Make
   sure Traefik forwards it (the snippet below does).
3. **Body-size limits matter for uploads.** TUS streams chunks (5 MB
   default) but the SPA also uses the direct-upload path for
   <100 MB files. Set Traefik's max body to at least
   `MAX_DIRECT_UPLOAD_BYTES + 10%`.

---

## `traefik/dynamic/fileheron.yml` — dynamic config example

```yaml
http:
  routers:
    fileheron:
      rule: "Host(`files.example.com`)"
      entryPoints:
        - websecure
      service: fileheron-spa
      tls:
        certResolver: letsencrypt
      middlewares:
        - fileheron-headers
        - fileheron-deny-internal

    fileheron-api:
      rule: "Host(`files.example.com`) && PathPrefix(`/api/`)"
      entryPoints:
        - websecure
      service: fileheron-backend
      tls:
        certResolver: letsencrypt
      middlewares:
        - fileheron-headers
        - fileheron-deny-internal
        - fileheron-large-body

    fileheron-uploads:
      rule: "Host(`files.example.com`) && PathPrefix(`/uploads/`)"
      entryPoints:
        - websecure
      service: fileheron-backend
      # tusd is reached via the SPA container's nginx proxy — same
      # backend service as /api but with body-size + timeout
      # appropriate for large streaming uploads.
      tls:
        certResolver: letsencrypt
      middlewares:
        - fileheron-headers
        - fileheron-large-body

  services:
    fileheron-spa:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8080"
    fileheron-backend:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8000"

  middlewares:
    fileheron-headers:
      headers:
        # Defense-in-depth on top of the SecurityHeadersMiddleware
        # the backend already sends.
        stsSeconds: 31536000
        stsIncludeSubdomains: true
        stsPreload: true
        contentTypeNosniff: true
        frameDeny: true
        referrerPolicy: "strict-origin-when-cross-origin"
        # Forward the original scheme so the backend can correctly
        # decide whether to issue Secure cookies.
        customRequestHeaders:
          X-Forwarded-Proto: "https"

    fileheron-deny-internal:
      # Defence-in-depth: refuse any external request to internal
      # paths even if a router accidentally matches them.
      stripPrefix:
        prefixes:
          - "/api/internal"
      # Note: stripPrefix would alias them to other paths — better
      # is to use plugin or chain to return 404. The cleanest
      # approach is to put a separate router with a higher priority:
      #   rule: "PathPrefix(`/api/internal/`)"
      #   service: noop
      # Or use the official `denyHeader`/`denyRouter` patterns from
      # your Traefik version.

    fileheron-large-body:
      # Adjust to MAX_DIRECT_UPLOAD_BYTES + headroom; default is
      # 100 MB direct upload + tusd chunked, so 110 MB is plenty
      # for the API path. The /uploads/ TUS path streams 5 MB
      # chunks so its body limit is much smaller.
      buffering:
        maxRequestBodyBytes: 115343360  # ~110 MiB
```

---

## Block `/api/internal/*` separately (preferred)

Because middlewares like `stripPrefix` mutate the request rather
than rejecting it, the cleanest approach is a high-priority router
that 404s any external attempt at `/api/internal/`:

```yaml
http:
  routers:
    fileheron-block-internal:
      rule: "Host(`files.example.com`) && PathPrefix(`/api/internal/`)"
      entryPoints: [websecure]
      service: noop@internal
      priority: 1000
      tls:
        certResolver: letsencrypt
```

`noop@internal` is Traefik's built-in service that returns 404.
Higher priority means this router wins over the broader
`/api/` router defined above.

---

## Static config sketch (entrypoints + cert resolver)

```yaml
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"

certificatesResolvers:
  letsencrypt:
    acme:
      email: ops@example.com
      storage: /etc/traefik/acme.json
      httpChallenge:
        entryPoint: web

providers:
  file:
    directory: /etc/traefik/dynamic
    watch: true
```

---

## Sanity checks before going live

- [ ] `curl https://files.example.com/api/internal/tus-hooks` → 404
- [ ] `curl -I https://files.example.com/` → `Strict-Transport-Security`
      header present
- [ ] Login from a browser; check that the `fh_refresh` cookie is
      `Secure; HttpOnly; SameSite=Lax`
- [ ] Upload a file >100 MB (forces TUS path); confirm completion
- [ ] Upload a file <100 MB (forces direct path); confirm completion
- [ ] If `BACKUP_RESTIC_*` is set, run `scripts/backup.sh` once and
      confirm the snapshot lands in your repo
