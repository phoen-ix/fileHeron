# Sample Traefik configuration for fileHeron

The recommended deploy runs Traefik **on the host** (not in this
compose file) so it can serve multiple apps from one TLS endpoint.
This directory holds copy-paste-ready configs for that setup.

The compose stack publishes on `127.0.0.1:${APP_BACKEND_PORT}` (default
8000) and `127.0.0.1:${APP_FRONTEND_PORT}` (default 8080) - Traefik
proxies external HTTPS to those local ports.

---

## Critical operator rules

1. **Never expose `/api/internal/*`** to the public internet. The
   tusd webhook receiver lives there with HMAC envelope auth +
   optional source-IP allowlist (see `TUS_HOOK_ALLOWED_IPS`), but
   defense-in-depth requires the proxy to also refuse the path.
2. **TLS terminates at Traefik.** Secure cookies come from `COOKIE_SECURE`,
   which `ENVIRONMENT=production` forces to true - the backend never reads
   `X-Forwarded-Proto` for that decision, so stripping the header does NOT
   break login. (This doc said it did; it never has.) The header still
   matters, but for tusd: nginx maps it into `$fh_proto` and tusd builds its
   upload `Location` from it, so stripping it makes TUS PATCH redirect
   http->https mid-upload and large uploads fail. Forward it (the snippet
   below does).
3. **Body-size limits matter for uploads - but scope them to the
   direct-upload path only.** The SPA uploads <100 MB files via
   `POST /api/uploads/direct`; cap that with the `buffering` middleware
   at `MAX_DIRECT_UPLOAD_BYTES + 10%`. (The backend also enforces the
   cap with a `413`, so this is defense-in-depth.)
4. **NEVER put the `buffering` middleware on `/api/` or `/uploads/`.**
   Traefik's `buffering` middleware buffers **responses** too
   (`maxResponseBodyBytes` defaults to *unlimited*), so a multi-GB
   download is spooled to disk on the Traefik host **before the first
   byte reaches the client** - minutes of latency and flaky/aborted
   downloads. TUS (`/uploads/`) must likewise stream, not buffer.
   Attach `fileheron-large-body` ONLY to a dedicated
   `Path('/api/uploads/direct')` router (see below).
5. **Don't let clients spoof their IP via `X-Forwarded-For` (audit H5).**
   The backend trusts the left-most `X-Forwarded-For` value (uvicorn
   `--proxy-headers --forwarded-allow-ips=*`) for the per-IP login
   rate-limit + account-lockout, the `login_attempts` / `audit_log` IPs,
   `known_devices`, and `TUS_HOOK_ALLOWED_IPS`. Traefik MUST set that header
   to the real client IP and MUST NOT trust an incoming, client-supplied one:
   - Do **NOT** set `forwardedHeaders.trustedIPs` or
     `forwardedHeaders.insecure` on the public entrypoint. That makes Traefik
     trust + pass through a client's own `X-Forwarded-For`, so an attacker can
     rotate it per request to defeat the rate-limit/lockout and poison the
     audit log. Traefik's default (overwrite with the connecting IP) is the
     safe behaviour - keep it.
   - Defense-in-depth: pin the backend's trust to the proxy rather than `*` -
     set `FORWARDED_ALLOW_IPS=<traefik/docker-bridge CIDR>` and change the
     backend CMD's `--forwarded-allow-ips=*` to that value, so uvicorn only
     trusts `X-Forwarded-For` from the proxy peer even if the backend port is
     ever reachable past Traefik.
   - Verify: `curl -H 'X-Forwarded-For: 1.2.3.4' https://<host>/api/auth/login ...`
     then confirm `login_attempts.ip` recorded YOUR real IP, not `1.2.3.4`.

---

## `traefik/dynamic/fileheron.yml` - dynamic config example

```yaml
http:
  routers:
    # Highest priority: refuse external requests to internal-only paths.
    # A separate router beats a middleware here - stripPrefix would ALIAS
    # /api/internal/* onto other paths rather than refuse it.
    #
    # CAVEAT, and it matters: this matches on the path as the entrypoint
    # presents it. If your entrypoint permits encoded slashes (Traefik v3
    # `encodedCharacters.allowEncodedSlash: true`, or `sanitizePath: false`),
    # then `/api/internal%2Ftus-hooks` does NOT match this rule, while uvicorn
    # decodes it and routes it to the real endpoint - so the deny is bypassed.
    # Treat this as defence-in-depth only. The controls that do not depend on
    # proxy path handling are the HMAC envelope (always on) and
    # TUS_HOOK_ALLOWED_IPS - set the latter.
    fileheron-block-internal:
      rule: "Host(`files.example.com`) && PathPrefix(`/api/internal/`)"
      entryPoints:
        - websecure
      service: noop@internal
      priority: 1000
      tls:
        certResolver: letsencrypt

    fileheron:
      rule: "Host(`files.example.com`)"
      entryPoints:
        - websecure
      service: fileheron-spa
      tls:
        certResolver: letsencrypt
      middlewares:
        - fileheron-headers

    # Dedicated, higher-priority router for the ONE endpoint that needs a
    # request-body cap. The buffering middleware lives ONLY here so it can
    # never buffer download/streaming responses (see operator rule 4).
    fileheron-api-upload:
      rule: "Host(`files.example.com`) && Path(`/api/uploads/direct`)"
      entryPoints:
        - websecure
      service: fileheron-backend
      priority: 60          # > fileheron-api so this exact path wins
      tls:
        certResolver: letsencrypt
      middlewares:
        - fileheron-headers
        - fileheron-large-body

    fileheron-api:
      rule: "Host(`files.example.com`) && PathPrefix(`/api/`)"
      entryPoints:
        - websecure
      service: fileheron-backend
      tls:
        certResolver: letsencrypt
      # NO fileheron-large-body here - buffering would spool every download
      # response to disk before the first byte (operator rule 4).
      middlewares:
        - fileheron-headers

    fileheron-uploads:
      rule: "Host(`files.example.com`) && PathPrefix(`/uploads/`)"
      entryPoints:
        - websecure
      # MUST be fileheron-spa, not fileheron-backend: tusd is reached through
      # the SPA container's nginx, which proxies /uploads/ to tusd:8080. The
      # backend has no /uploads/ route, so pointing this at it 404s every
      # resumable upload.
      service: fileheron-spa
      # TUS streams 5 MB chunks and tusd enforces its own limits - NO buffering
      # here, or large resumable uploads (and any download) stall.
      tls:
        certResolver: letsencrypt
      middlewares:
        - fileheron-headers

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

    fileheron-large-body:
      # Adjust to MAX_DIRECT_UPLOAD_BYTES + headroom (~110 MiB for the
      # 100 MB default). Attach ONLY to fileheron-api-upload - Traefik's
      # buffering middleware ALSO buffers responses (memResponseBodyBytes
      # 1 MB in RAM, the rest spooled to disk, maxResponseBodyBytes
      # unlimited by default), which would break large downloads. The
      # direct-upload response is tiny JSON, so buffering it is harmless.
      buffering:
        maxRequestBodyBytes: 115343360  # ~110 MiB
        memRequestBodyBytes: 6291456    # 6 MiB in RAM before spilling to disk
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
`/api/` router defined above. This is already included in the dynamic-config
example above; it is repeated here because it is the one rule most
hand-written configs get wrong.

### This rule alone is not sufficient

Router rules match the path **as the entrypoint presents it**, while
uvicorn/Starlette percent-decode before routing. If your entrypoint permits
encoded slashes - Traefik v3 `encodedCharacters.allowEncodedSlash: true`, or
`sanitizePath: false` - then:

    GET /api/internal/tus-hooks     -> 418/404   (blocked, rule matches)
    GET /api/internal%2Ftus-hooks   -> reaches the backend  (rule does NOT match)

The same trick sidesteps a `Path()`-scoped body cap such as
`fileheron-api-upload`. If you run a shared entrypoint you may not be free to
change that setting, so do not rely on the proxy for this:

1. **Set `TUS_HOOK_ALLOWED_IPS`** to the tusd container's address. It is
   enforced in the application (`routers/tus_hooks.py`), so no proxy path
   handling can bypass it.
2. The **HMAC envelope** signed with `TUS_HOOK_SECRET` is always required and is
   the load-bearing control. Keep that secret strong; the backend refuses to
   boot in production if it is still a placeholder.

Verify with both forms after any proxy change:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://files.example.com/api/internal/tus-hooks
curl -s -o /dev/null -w '%{http_code}\n' https://files.example.com/api/internal%2Ftus-hooks
```

Anything that reaches the app returns the JSON error envelope with a
`request_id`; a blocked request does not.

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
      **only if you added the headers middleware below.** Neither the sample
      `dynamic.yml` in README.md nor the frontend nginx emits HSTS, so this
      check failed on a correctly-installed stack until you add it
      (audit 2026-07-30):

      ```yaml
      http:
        middlewares:
          fh-hsts:
            headers:
              stsSeconds: 31536000
              stsIncludeSubdomains: true
      ```
      then reference `fh-hsts` from both routers.
      header present
- [ ] Login from a browser; check that the `fh_refresh` cookie is
      `Secure; HttpOnly; SameSite=Lax`
- [ ] Upload a file >100 MB (forces TUS path); confirm completion
- [ ] Upload a file <100 MB (forces direct path); confirm completion
- [ ] **Download a multi-GB file and confirm the save dialog appears
      within ~1-2s** (not minutes). If it stalls, a `buffering`
      middleware is wrongly attached to `/api/` (operator rule 4):
      `curl -o /dev/null -w '%{time_starttransfer}\n' <download-url>`
- [ ] If `BACKUP_RESTIC_*` is set, run `scripts/backup.sh` once and
      confirm the snapshot lands in your repo
