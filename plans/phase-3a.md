# Phase 3a — Upload Backend (tusd, file/share schema, download endpoint)

> Master plan: `/home/mk/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 1a/1b/2 being complete (auth + UI shell required to test e2e via cookies).

## Goal

Add tusd as a separate Docker service. Land the file / share / share_recipient / api_token / download_log schema. Wire the TUS hook endpoints with HMAC-validated metadata. Add API tokens (generate, list, revoke). Add the download endpoint that streams via FastAPI `FileResponse` + sendfile. Add a small-file direct-upload endpoint (<100 MB) for clients that don't want a TUS dependency. Per-user quota enforcement in the `pre-finish` hook with Redis-atomic counters.

**No frontend in this phase.** That lands in Phase 3b. End-to-end testing this phase is via `curl` + a tusd-compatible CLI client (`tusc` or `tuspy` from a script).

## Pre-phase decisions (resolve at session start)

1. **TUS metadata signing scheme** — confirm: backend issues HMAC envelope `{share_id, owner_user_id, max_size, exp}` valid 1h, embedded in `Upload-Metadata` header field `signed_meta`; tusd `pre-create` hook validates via shared secret. Reject if expired or mismatched size.
2. **API token format** — confirm `fh_<8-hex-id>_<32-base64url>`. Hash the secret half (Argon2 or SHA-256?). *Default: SHA-256 for fast lookup; secret half is high-entropy random.*
3. **File path hashing** — `share-uuid` is the on-disk filename. Confirm: don't store original filename in path (avoids encoding issues + leaks); keep mapping in `files.original_filename`.
4. **Direct-upload size cap** — confirm 100 MB.
5. **Quota enforcement model** — confirm per-user (not per-group). Atomic via Redis Lua or via `SELECT ... FOR UPDATE` on user row.

## Acceptance criteria

- `tusd` container starts, healthcheck green, `/uploads/` is reachable internally on port 1080.
- `POST /api/uploads/init` (auth or API token) returns `{tus_endpoint, signed_metadata, share_id, file_id}` with HMAC-signed metadata blob.
- An external `tuspy` script (or `curl` with TUS headers) can upload a 1 GB file end-to-end. After `post-finish`, the file is moved from `./data/uploads/` to `./data/files/{yyyy}/{mm}/{share-uuid}` and the `files` row state transitions to `ready_unscanned`.
- `GET /api/files/{id}/download` (auth required) streams the file with correct `Content-Disposition: attachment; filename="..."` and `Content-Length`. Forbidden for users not on the share.
- Per-user quota: setting `users.quota_bytes = 100MB` and attempting to upload a 200 MB file → tusd `pre-finish` returns 413 with envelope `{"code":"QUOTA_EXCEEDED"}`.
- API token flow: a user can `POST /api/account/api-tokens` to get a one-time `fh_xxx_yyy` value, list tokens (showing `last4` + name), revoke one, and use a token to call `/api/uploads/init`.
- `download_log` row written for every successful download.
- pytest covers tusd-hook auth, finalize-move, quota race, and forbidden-download cases.

## Files to create / modify

### Compose
- `docker-compose.yml` — add `tusd` service:
  ```yaml
  tusd:
    image: tusproject/tusd:latest
    command:
      - "-upload-dir=/data/uploads"
      - "-hooks-http=http://backend:8000/internal/tus-hooks"
      - "-hooks-http-forward-headers=Authorization"
      - "-hooks-enabled-events=pre-create,pre-finish,post-finish,post-terminate"
      - "-base-path=/uploads/"
      - "-behind-proxy"
    volumes:
      - ./data/uploads:/data/uploads
    networks: [internal]
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:1080/uploads/"]
      interval: 15s
      timeout: 5s
      retries: 3
  ```
- Backend gets new env: `TUS_HOOK_SECRET`, `TUS_PUBLIC_BASE` (`https://files.example.com/uploads/` or path), `MAX_DIRECT_UPLOAD_BYTES=104857600`, `STORAGE_ROOT=/data/files`.

### Backend — new models
- `backend/app/models/file.py` — `id UUID PK`, `share_id` FK, `original_filename`, `mime_type`, `size_bytes BIGINT`, `storage_path`, `sha256_hex` (nullable in 3a, populated in 3b/5), `state` enum (`uploading`, `ready_unscanned`, `clean`, `infected`, `deleted`), `uploaded_by` FK, `created_at`, `tus_upload_id` (nullable after finalize).
- `backend/app/models/share.py` — `id UUID PK`, `created_by` FK, `kind` enum (`outbound`, `inbound`), `subject` (nullable text), `message` (nullable text), `expires_at`, `state` enum (`active`, `expired`, `revoked`, `deleted`), `created_at`. Note: `expires_at` is required (driven by user input).
- `backend/app/models/share_recipient.py` — `share_id` FK, `recipient_user_id` FK (nullable), `recipient_group_id` BIGINT (nullable in P3a, becomes real FK in P4 once `groups` exists). CHECK exactly one of the two NOT NULL. Indexed on both.
- `backend/app/models/api_token.py` — `id UUID PK`, `owner_user_id` FK, `name`, `last4`, `secret_hash` (SHA-256 hex), `created_at`, `last_used_at` (nullable), `revoked_at` (nullable).
- `backend/app/models/download_log.py` — `id BIGINT`, `file_id` FK, `share_id` FK, `accessed_by_user_id` FK (nullable for public-link in P5), `accessed_at`, `ip`, `ua_fingerprint_hash`, `bytes_served` (nullable, for partial-content), `via` enum (`auth`, `public`, `api_token`).

### Backend — new services
- `backend/app/services/file.py` — `create_file_record(share, filename, size)`, `finalize_file(file_id, tus_upload_id)` (move file, update state), `delete_file(file_id, reason)` (hard-delete from disk + audit).
- `backend/app/services/share.py` — `create_outbound_share(uploader, recipients, expiry, ...)`, `create_inbound_share(...)`, `is_authorized_to_download(user, share)`, `expire_share(share)`.
- `backend/app/services/api_token.py` — `create_token(user, name)` (returns plaintext once), `verify_token(token_str)`, `list_tokens(user)`, `revoke(token_id)`.
- `backend/app/services/quota.py` — `check_quota(user, additional_bytes)` (Redis-atomic), `compute_used(user)` (DB SUM, used by tests + a refresh job).
- `backend/app/services/tus_signing.py` — `sign_metadata(payload, secret)`, `verify_metadata(blob, secret)` → returns parsed payload or raises.
- `backend/app/services/tus_hooks.py` — handlers for `pre-create`, `pre-finish`, `post-finish`, `post-terminate`.

### Backend — extended dependencies
- `backend/app/dependencies.py` — add `get_actor_via_api_token_or_session(...)` (returns the same `User` regardless of source, but tags context with the source for audit).

### Backend — new routers
- `backend/app/routers/tus_hooks.py` — single `POST /internal/tus-hooks` endpoint. Authenticates via shared-secret header (`X-Tus-Hook-Secret`). Dispatches to handler by `Hook-Name`.
- `backend/app/routers/files.py` — `GET /api/files/{id}/download`, `DELETE /api/files/{id}`.
- `backend/app/routers/shares.py` (initial CRUD; enriched in P4):
  - `POST /api/shares` — create draft outbound share (no recipients yet → recipients added in P4 picker; for now accept a single recipient `user_id`).
  - `GET  /api/shares?box=outbox|inbox&page=...`
  - `GET  /api/shares/{id}`
  - `DELETE /api/shares/{id}`
- `backend/app/routers/uploads.py`:
  - `POST /api/uploads/init` — body `{share_id, filename, size_bytes, mime_type}` → returns TUS endpoint + signed metadata.
  - `POST /api/uploads/direct` — multipart for files <100 MB.
- `backend/app/routers/account.py` (extend with API tokens):
  - `POST /api/account/api-tokens` (body `{name}`) → `{token, last4}` once
  - `GET  /api/account/api-tokens`
  - `DELETE /api/account/api-tokens/{id}`

### Backend — new tests
- `backend/tests/test_tus_hooks.py` — HMAC validation (forged secret → 403), pre-finish quota check, post-finish file move, post-terminate cleanup.
- `backend/tests/test_uploads_e2e.py` — uses `tuspy` against the real tusd container in CI (or mocks tusd if integration too heavy).
- `backend/tests/test_api_tokens.py` — generate, verify, revoke, last4 visibility.
- `backend/tests/test_download_auth.py` — recipient can download, sender can download, admin can download, unrelated user can't.
- `backend/tests/test_quota.py` — atomic Redis check, race condition test.

### Root docs
- `CLAUDE.md` — extend Project structure with `services/file.py`, `services/share.py`, `services/quota.py`, `services/tus_signing.py`, `routers/tus_hooks.py`. Add an "Upload pipeline" section explaining the hook flow.

## DB migrations

1. `files`
2. `shares`
3. `share_recipients` (with `recipient_group_id` as plain BIGINT for now; FK comes in P4)
4. `api_tokens`
5. `download_log`

## API endpoints (this phase)

- `POST /internal/tus-hooks` (HMAC-secret authenticated, dispatches by `Hook-Name`)
- `POST /api/uploads/init` — auth or API-token; body `{share_id, filename, size_bytes, mime_type}` → `{tus_endpoint, signed_metadata, file_id, expires_at}`
- `POST /api/uploads/direct` — multipart, files <100 MB
- `POST /api/shares` (single-recipient skeleton; multi-recipient in P4)
- `GET  /api/shares?box=outbox|inbox`
- `GET  /api/shares/{id}`
- `DELETE /api/shares/{id}`
- `GET  /api/files/{id}/download`
- `DELETE /api/files/{id}`
- `POST/GET/DELETE /api/account/api-tokens[/{id}]`

## Frontend

None. Phase 3b adds the UI.

## Dependencies added

**pip:** `python-magic` (mime-sniffing for direct-upload validation), `redis-lua` (or implement Lua script directly via `redis.execute_command`).
**npm:** none.

## Risks / pitfalls

1. **tusd hook auth via shared secret** — bake the secret into both backend and tusd via env. Never log the hook payload at INFO level (file metadata can include sensitive filenames).
2. **Atomic move on `post-finish`** — `os.rename()` is atomic only within a single filesystem. Ensure `./data/uploads/` and `./data/files/` are on the same volume (single bind-mount root). Document this in CLAUDE.md as a deployment requirement.
3. **Quota race** — two simultaneous uploads can each pass the per-user check separately. Use a Redis Lua atomic INCRBY with bound check, or `SELECT ... FOR UPDATE` on the user row in the `pre-finish` hook (hold the lock briefly).
4. **API-token leakage in logs** — token format `fh_xxxx_xxxxx` is grep-able; configure log scrubbing for any string matching `fh_[a-zA-Z0-9_-]+_`.
5. **MIME sniff** — never trust the browser-supplied MIME. Sniff with `python-magic` after upload completes; if it disagrees significantly (e.g., user said `image/png` but file is `application/x-msdownload`), flag in audit log + mark suspicious for manual review (the AV in P5 catches actual malware).
6. **Big-file `Content-Length`** — don't forget to set it on the download response; some clients (and proxies) misbehave without it.
7. **Range requests** — Starlette's `FileResponse` doesn't natively handle `Range:` headers. Either use `aiofiles` + manual byte-range parsing, or accept that resumable downloads are deferred (most clients re-download from scratch on failure for files this size; tolerable for v1).

## Verification

```bash
# Generate an API token (after logging in as admin)
TOKEN=$(curl -X POST http://127.0.0.1:8000/api/account/api-tokens -d '{"name":"ci"}' | jq -r .token)

# Initiate an upload
RESP=$(curl -X POST http://127.0.0.1:8000/api/uploads/init \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"share_id":"<id from POST /api/shares>","filename":"big.bin","size_bytes":1073741824,"mime_type":"application/octet-stream"}')

# Use tuspy or any TUS client to upload to RESP.tus_endpoint with RESP.signed_metadata as Upload-Metadata
python -c "import tuspy; ..."

# Download
curl -O http://127.0.0.1:8000/api/files/<file_id>/download -b cookies.txt

docker compose exec backend pytest -q backend/tests/test_tus_hooks.py
```

## Out of scope

- Browser Uppy integration → **Phase 3b**
- Multi-recipient share / group recipients → **Phase 4**
- Antivirus scan after finalize → **Phase 5**
- Public links → **Phase 5**
- Quota UI for admin → **Phase 6b**
