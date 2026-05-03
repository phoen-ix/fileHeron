# Phase 8 (optional) — Advanced Security & Performance

> Master plan: `REDACTED/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 7. **Optional** — fileHeron is fully functional after Phase 7. Phase 8 hardens it further. Treat each item below as standalone; pick which to ship.

## Goal

Four independent improvements:
1. **WebAuthn / passkeys** as an alternative second factor (in addition to TOTP).
2. **Per-file envelope encryption** at rest (AES-256 per file, master key from env).
3. **GDPR right-to-erasure UI polish** + receipt PDF.
4. **Performance load test** (Locust) and tuning passes.

## Pre-phase decisions

1. **WebAuthn scope** — platform authenticators only (e.g., Touch ID), cross-platform only (USB security keys), or both? *Default: both.*
2. **Encryption key management** — single master key from env, or KMS-style with per-tenant keys? *Default: single master key from env (we are single-org). Document key-rotation procedure.*
3. **Encryption performance budget** — accept ~10-20% throughput hit on download? *Default: yes, given the security gain.*
4. **Locust target** — single-host concurrent uploads (50 × 100 MB) and downloads (100 × 100 MB)? *Default: yes.*

## Sub-deliverables

### 8.1 WebAuthn / Passkeys

#### Acceptance
- Account settings shows a WebAuthn section: "Add a security key / passkey" → triggers `navigator.credentials.create(...)` flow → registers credential.
- Login page: after password (and TOTP, if enabled), if any WebAuthn credential is registered, offer "Use security key" button → `navigator.credentials.get(...)`.
- Remove credential flow.
- Multiple credentials per user (e.g., laptop Touch ID + USB key).

#### Files
- `backend/app/models/user_webauthn_credential.py` — `id`, `user_id` FK, `credential_id` (bytes), `public_key`, `sign_count`, `transports`, `name`, `created_at`, `last_used_at`.
- `backend/app/services/webauthn.py` — wraps `webauthn` Python lib (or `fido2`).
- `backend/app/routers/account.py` (extend) — `/api/account/webauthn/register/begin`, `.../register/complete`, `.../authenticate/begin`, `.../authenticate/complete`, `DELETE /api/account/webauthn/{id}`.
- `frontend/src/views/AccountWebAuthn.vue`, `frontend/src/composables/useWebAuthn.ts`.

#### Deps
- pip: `webauthn` (or `fido2`).
- npm: none (uses native `navigator.credentials`).

### 8.2 Per-file envelope encryption

#### Acceptance
- Each finalized file is encrypted with a random AES-256 file key. The file key is encrypted under a master KEK (Key Encryption Key) from env; the encrypted file key + IV are stored alongside `files` row.
- Download endpoint streams via `aes_decrypt_stream(file_path, file_key)` — kernel `sendfile` no longer applies; we read + decrypt + write in 1MB chunks.
- Upload finalize step encrypts the file in place (or to a sibling and atomic-renames).
- Master key rotation procedure documented: re-encrypt all file keys under new master key (one-time migration script).

#### Files
- `backend/app/services/encryption.py` — `encrypt_file_inplace(path, file_key)`, `decrypt_stream(path, file_key)` async generator.
- `backend/app/services/file.py` — extend `finalize_file` to encrypt; extend `serve_file` to decrypt.
- `backend/app/scripts/rotate_master_key.py` — re-encrypt all file keys.
- `backend/app/models/file.py` — add `file_key_encrypted` (bytes), `iv` (bytes), `is_encrypted` bool.

#### Deps
- pip: `cryptography` (already in 1b).

#### Migration concern
If turning on encryption mid-life (with files already on disk unencrypted), provide a migration script `encrypt_existing_files.py`. New deployments simply enable from day one.

### 8.3 GDPR right-to-erasure UI polish

#### Acceptance
- The admin erasure dialog shows a pre-flight summary: total file count, total bytes, share count, and which other users will see "[erased]" recipient labels.
- Two-step confirmation: type the user's email-hint to confirm.
- After erasure: download a PDF receipt (signed by the admin's request-id + timestamp) confirming what was deleted. Store the receipt in `audit_log.metadata_json` as a verifiable record.

#### Files
- `backend/app/services/erasure.py` (extend) — `compute_erasure_summary(user_id)`, `generate_receipt_pdf(audit_event)`.
- `backend/app/routers/admin.py` (extend) — preflight endpoint, receipt download endpoint.
- `frontend/src/views/AdminUserDetail.vue` (extend) — preflight dialog UI.

#### Deps
- pip: `reportlab` (PDF generation).

### 8.4 Performance load test

#### Acceptance
- Locust scripts in `backend/tests/perf/`:
  - `upload_burst.py` — 50 concurrent users uploading 100 MB each via TUS.
  - `download_burst.py` — 100 concurrent users downloading 100 MB each.
  - `mixed_workload.py` — auth + share creation + upload + download mixed.
- Run produces a baseline report (req/s, p50/p95/p99 latency, error rate). Document numbers in CLAUDE.md performance section.
- Tune uvicorn worker count + Argon2 cost to hit the documented targets without OOMing.

#### Files
- `backend/tests/perf/upload_burst.py`, `download_burst.py`, `mixed_workload.py`, `conftest.py`.
- `CLAUDE.md` — add a "Performance baseline" section with the documented numbers.

#### Deps
- pip (dev): `locust`.

## DB migrations

If 8.1 and/or 8.2 ship:
- `user_webauthn_credentials`
- `files_add_encryption_columns`

## API endpoints (added if 8.1 ships)

- `POST /api/account/webauthn/register/begin`
- `POST /api/account/webauthn/register/complete`
- `POST /api/account/webauthn/authenticate/begin`
- `POST /api/account/webauthn/authenticate/complete`
- `DELETE /api/account/webauthn/{id}`

## Risks / pitfalls

1. **WebAuthn origin / RPID** — must match exactly what's in the browser URL. Get this wrong and registration fails silently. Document `WEBAUTHN_RP_ID` env.
2. **Encryption breaks zero-copy** — kernel `sendfile()` no longer applies once we decrypt in userspace. Throughput drops; document the tradeoff.
3. **Master key loss = data loss** — explicitly. The master key MUST be backed up separately from the data backup. Document this loudly.
4. **PDF receipts can leak metadata** — careful what you put in them; they may be exported.
5. **Locust against your own server** — don't run against production; spin up a load-test environment that mirrors prod but is isolated.

## Verification

(See per-sub-deliverable acceptance.)

## Out of scope (now and forever, unless requirements change)

- Multi-tenancy
- File preview / thumbnail generation
- Real-time chat in shares
- Mobile native apps
