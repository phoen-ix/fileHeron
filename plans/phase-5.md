# Phase 5 — Public Links + Antivirus

> Master plan: `REDACTED/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 4 being complete.

## Goal

Add ClamAV as a separate Docker service. Land an ARQ `av_scan` worker dispatched from the `post-finish` hook that transitions files from `ready_unscanned` to `clean` or `infected`. Quarantine flow: infected files moved to `./data/quarantine/`, share auto-revoked, admin alerted (email logged in dev). Add public-link generation (token + optional password + optional download-count limit + optional email-on-download). Add the public download page at `/d/{token}`.

## Pre-phase decisions

1. **Public-link domain** — same hostname under `/d/{token}` (recommended, simpler) vs separate (`dl.example.com`)? *Default: same.*
2. **Download-while-unscanned policy** — block downloads during `ready_unscanned` (return 425 Too Early) or allow with a warning banner? *Default: block — safer for recipients; user gets a clear "scan in progress" message.*
3. **Download notification frequency** — literal each download, first-only, or daily digest if `notify_on_download=true`? *Default recommendation: literal each, with a per-link rate limit (max 5 emails/min/link) to prevent obvious spam.*
4. **AV test fixture** — ship EICAR string in `backend/tests/fixtures/eicar.com` (some host AV will quarantine it; CI may also) vs mock `clamdscan` in tests. *Default: mock + a separate manual EICAR runbook.*

## Acceptance criteria

- ClamAV container starts; `clamdscan` available; signatures auto-update via `freshclam` (background process inside the container, configured via image defaults).
- After tusd `post-finish`, ARQ `av_scan` task runs; on EICAR test → `state=infected`, file moved to `./data/quarantine/{share-uuid}/{filename}`, share marked `state=revoked`, admin email enqueued (or logged), audit row written.
- On clean file → `state=clean`. Downloads now allowed.
- Sender can `POST /api/shares/{id}/public-link` with `{password?, download_limit?, notify_on_download?}` → `{url}` shown once. Subsequent `GET` returns metadata only (no token).
- `GET /d/{token}` (no auth) returns landing page metadata. If password is set, page shows password prompt; submitting `POST /d/{token}/unlock` with valid password sets a short-lived signed cookie scoped to `/d/{token}`. Download via `GET /d/{token}/files/{file_id}/download` decrements counter atomically.
- Counter reaches 0 → link auto-revoked, returns `410 Gone`.
- All public-link interactions logged to `audit_log` and `download_log` (with `via=public`, `accessed_by_user_id=NULL`).
- Password brute-force rate-limited per (link, IP): 5 attempts / minute; 10 failures locks the link for 15 min and emails the owner.
- pytest covers AV scan happy path, EICAR-mock infection, public-link with password / count limit / both, brute-force lockout.

## Files to create / modify

### Compose
- `docker-compose.yml` — add `clamav` service:
  ```yaml
  clamav:
    image: clamav/clamav:stable
    volumes:
      - ./data/files:/data/files:ro
      - ./data/quarantine:/data/quarantine:rw
      - clamav-defs:/var/lib/clamav   # signature DB
    networks: [internal]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "clamdtop", "--version"]   # or nc -z localhost 3310
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s   # initial signature load is slow
    logging:
      driver: json-file
      options: { max-size: "50m", max-file: "3" }
  ```
- Volume `clamav-defs` is a named volume (signatures don't need bind-mount).
- Backend gets new env: `CLAMAV_HOST=clamav`, `CLAMAV_PORT=3310`, `QUARANTINE_DIR=/data/quarantine`, `PUBLIC_LINK_BASE_PATH=/d`.

### Backend — new models
- `backend/app/models/public_link.py` — `id UUID PK`, `share_id` FK, `token_hash` (SHA-256 hex over the raw token), `password_hash` (Argon2, nullable), `download_limit` (nullable int), `downloads_remaining` (nullable int), `notify_on_download` bool, `revoked_at` (nullable), `created_by` FK, `created_at`. Inherits expiry from share.
- `backend/app/models/public_link_password_attempt.py` — `id`, `public_link_id`, `ip`, `attempted_at`, `outcome` enum (`success`, `failure`, `locked`).

### Backend — new services
- `backend/app/services/public_link.py` — `create_link`, `verify_token`, `verify_password`, `decrement_counter` (atomic: `UPDATE public_links SET downloads_remaining = downloads_remaining - 1 WHERE id=:id AND (downloads_remaining IS NULL OR downloads_remaining > 0)`; check affected_rows), `revoke`, `record_download`.
- `backend/app/services/av_scan.py` — `scan_file(file_id)` runs `clamdscan` against the file path inside the ClamAV container (via socket or subprocess), returns `clean` / `infected`.
- `backend/app/services/quarantine.py` — `quarantine_file(file_id)` moves the file to `./data/quarantine/{share-uuid}/{filename}`, updates DB state, revokes share + all its public links, audits.

### Backend — workers
- `backend/app/workers/av_scan.py` — ARQ task `av_scan_file(file_id)`. Dispatched from `tus_hooks.post_finish_handler`. On infected → calls `quarantine_file`. Retries on transient failure (e.g., clamav unavailable).

### Backend — extended routers
- `backend/app/routers/shares.py` — extend with public-link sub-resources:
  - `POST /api/shares/{id}/public-link` — body `{password?, download_limit?, notify_on_download?}` → `{url}` (token shown once)
  - `GET  /api/shares/{id}/public-link` — metadata only (no token)
  - `DELETE /api/shares/{id}/public-link` — revoke
- `backend/app/routers/public.py` (new) — public-facing endpoints, no auth:
  - `GET /d/{token}` — returns share metadata + `requires_password: bool` + file list
  - `POST /d/{token}/unlock` — body `{password}` → sets short-lived cookie `fh_dl_unlock`
  - `GET /d/{token}/files/{file_id}/download` — uses unlock cookie if password required; streams file via FileResponse
- Block downloads when `file.state == 'ready_unscanned'` with HTTP 425.
- Block downloads when `file.state == 'infected'` with HTTP 410 + audit log.

### Backend — new tests
- `backend/tests/test_av_scan.py` — mock `clamdscan` to return clean / infected; assert state transitions + quarantine + audit.
- `backend/tests/test_public_link.py` — create / unlock / download / counter / revoke / brute-force lockout.
- `backend/tests/fixtures/clamav_mock.py` — mock subprocess for tests.

### Frontend — new
- `frontend/src/api/publicLinks.ts` — create / get / revoke from authenticated UI.
- `frontend/src/views/PublicShare.vue` — public-facing landing page. Light theme (no Element Plus heavy chrome), branding minimal. Password prompt + file list + download buttons.
- `frontend/src/components/PublicLinkPanel.vue` — used in `ShareDetail.vue`. Generate link UI (with password / count limit / notify toggles), one-time-display modal, revoke button.
- `frontend/src/router/index.ts` — add `/d/:token` as a public route mounting `PublicShare.vue`.

## DB migrations

1. `public_links`
2. `public_link_password_attempts`
3. (optional) `download_log_public_link_id` — nullable FK column on `download_log`.

## API endpoints (added this phase)

- `POST   /api/shares/{id}/public-link`
- `GET    /api/shares/{id}/public-link`
- `DELETE /api/shares/{id}/public-link`
- `GET    /d/{token}`
- `POST   /d/{token}/unlock`
- `GET    /d/{token}/files/{file_id}/download`

## Dependencies added

**pip:** `pyclamd` (preferred) or call `clamdscan` via `subprocess.run` (no dep, slightly slower handshake).
**npm:** none.

## Risks / pitfalls

1. **ClamAV memory** — needs ~1.5 GB RAM minimum (signature DB in memory). Document in README quickstart so people don't run on a 1 GB VPS and wonder why it OOMs.
2. **Signature update timing** — `freshclam` runs on a schedule inside the container; first boot takes 30-90 seconds. Healthcheck `start_period: 120s` accommodates this.
3. **Counter atomicity** — public-link counter must decrement atomically: `UPDATE … SET downloads_remaining = downloads_remaining - 1 WHERE id=:id AND downloads_remaining > 0`, check `affected_rows`. Never `SELECT then UPDATE`.
4. **Brute-force on password** — rate-limit per `(public_link_id, ip)`. After 10 failures in 15 min, lock the link for 15 min + send owner an email + audit-log.
5. **Download race during scan** — file uploaded → `ready_unscanned` → user attempts download → returns 425. Once scan finishes → `clean` → next attempt succeeds. Document the brief unavailability window.
6. **EICAR in tests** — the standard EICAR signature triggers your own pipeline (good for an integration test) but may also be quarantined by host AV during test runs. Default to mocking unless explicitly running an integration test.
7. **Public-link token format** — 32-byte urlsafe-base64 (43 chars). Hashed with SHA-256 in DB (high-entropy → no need for Argon2). Token search uses indexed hash lookup.

## Verification

```bash
# Upload a clean file (should transition to clean)
# (use Phase 3 flow)
# Then check state:
curl -H "Authorization: Bearer $TOKEN" .../api/shares/$SHARE_ID
# files[].state should be "clean"

# Upload EICAR (manual test only)
# echo 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > eicar.com
# upload via Uppy
# files[].state should be "infected", share state "revoked", admin email logged

# Public link with password + count
curl -X POST .../api/shares/$SHARE_ID/public-link \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"password":"secret","download_limit":3,"notify_on_download":true}'
# → {"url": "https://files.example.com/d/abc123..."}

# As anonymous:
curl https://.../d/abc123                                # 200 with requires_password=true
curl -X POST https://.../d/abc123/unlock -d '{"password":"secret"}' -c c.txt  # 200, sets cookie
curl https://.../d/abc123/files/$FILE_ID/download -b c.txt -O                  # 200 + file
# 4th download attempt → 410 Gone

docker compose exec backend pytest -q
```

## Out of scope

- Email actually sending (SMTP delivery + retry) → **Phase 6a**
- Admin quarantine review UI → **Phase 6b**
- Audit log viewer UI → **Phase 6b**
- Per-file encryption (envelope crypto) → **Phase 8**
