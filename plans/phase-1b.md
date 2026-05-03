# Phase 1b — Auth Hardening (TOTP, lockout, error envelope formalization)

> Master plan: `/home/mk/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 1a being complete.

## Goal

Add TOTP 2FA (setup + challenge + recovery codes), per-IP login rate limit + per-account consecutive-failure soft lockout, formal error envelope + request-ID middleware, security headers. Push pytest coverage on `services/auth.py`, `services/totp.py`, `routers/auth.py` to ≥80%.

## Pre-phase decisions (resolve at session start)

1. **TOTP secret encryption at rest** — derive a key from `JWT_SECRET` via HKDF and use Fernet/AES-GCM, or store base32 plaintext (dependent on threat model)? *Default: HKDF-derived key + Fernet, document rotation plan in CLAUDE.md.*
2. **Recovery code count** — confirm 10. Confirm format (e.g., 4-4-4 hex groups vs 12-char alphanumeric).
3. **Lockout email throttling** — emit warning email on each lockout, or deduplicate to once per N hours? *Default: deduplicate via `audit_log` lookup — at most once per 6h per user.*

## Acceptance criteria

- A user can enable TOTP via `POST /api/account/2fa/setup` (returns `{secret_b32, otpauth_uri, qr_svg}`) and `POST /api/account/2fa/enable` with a valid code; on enable, response includes 10 single-use recovery codes shown once.
- Login flow: with 2FA on, `POST /api/auth/login` with valid password but missing `totp_code` returns 401 with `code: "TOTP_REQUIRED"`. Adding `totp_code` succeeds. `POST /api/auth/login/recovery` accepts a recovery code in lieu; using one consumes it (verifiable in `user_recovery_codes`).
- `POST /api/account/2fa/disable` requires `{password, code}` and emits `audit_log(2fa_disabled)`.
- 11th failed login attempt within 15 min from one IP is rate-limited (429).
- 5 consecutive failed attempts on a single account → 15-min soft lockout; warning email sent (or skipped per dedup window); subsequent successful logins from the user clear the counter.
- Error envelope shape `{"error","code","details"}` is consistent across every 4xx/5xx in the app. Validated by a test that hits 5+ different error paths.
- `X-Request-Id` header present on every response; same UUID appears in logs for that request.
- Security headers present on every response (verified in test).
- `pytest -q` ≥80% coverage on auth + totp services and routers.

## Files to create

### Backend — new models
- `backend/app/models/user_totp.py` — `user_id PK`, `secret_encrypted` (Fernet ciphertext), `enabled_at` (nullable), `last_used_counter` (anti-replay).
- `backend/app/models/user_recovery_code.py` — `id`, `user_id`, `code_hash` (Argon2), `used_at` (nullable), `created_at`.
- `backend/app/models/login_attempt.py` — `id`, `email_hash` (nullable, for unknown-email path), `ip`, `attempted_at`, `outcome` enum (`success`, `bad_password`, `bad_totp`, `locked`, `unknown_email`).
- `backend/app/models/known_device.py` — `id`, `user_id`, `ua_fingerprint_hash`, `ip_geohash` (5-char), `first_seen`, `last_seen`. (Used in Phase 7 for new-device alerts; rows are *recorded* this phase but no email yet.)
- Add `users.failed_login_count INT DEFAULT 0`, `users.locked_until TIMESTAMP NULL` columns via migration.

### Backend — new services
- `backend/app/services/totp.py` — `generate_secret`, `enable_totp(user, code)`, `disable_totp(user, password, code)`, `verify_code(user, code)`, `consume_recovery_code(user, code)`, `regenerate_recovery_codes(user)`. Encrypt/decrypt via HKDF-derived key from `JWT_SECRET`.
- `backend/app/services/rate_limit.py` — Redis-backed; `check_login_rate(ip)` (sliding window 15 min, 10 attempts), `register_attempt(ip, email_hash, outcome)`, `record_account_failure(user)`, `clear_account_failures(user)`, `check_account_locked(user)`.

### Backend — extended services
- `backend/app/services/auth.py` — extend `login` to handle TOTP challenge, `login_with_recovery`. Refactor for clarity: `_verify_password`, `_verify_2fa`, `_finalize_login`. Add device-fingerprint recording (`known_device` row) but defer the email part to Phase 7.

### Backend — extended routers
- `backend/app/routers/auth.py` — `/api/auth/login` now returns `TOTP_REQUIRED` when applicable; new `/api/auth/login/recovery`.
- `backend/app/routers/account.py` — new endpoints listed below.

### Backend — middleware (formalize)
- Already created in Phase 1a, this phase adds full test coverage and tightens security headers.
- `backend/app/middleware/errors.py` — ensure `AppError` covers: `INVALID_CREDENTIALS`, `TOTP_REQUIRED`, `INVALID_TOTP`, `ACCOUNT_LOCKED`, `RATE_LIMITED`, `TOKEN_REUSE`, `INVITE_EXPIRED`, `INVITE_USED`, `EMAIL_NOT_VERIFIED`, `PASSWORD_BREACHED` (stub for now).

### Backend — new utils
- `backend/app/utils/geohash.py` — short helper (5-char geohash from `ip` via maxminddb-lite or a tiny prefix mapping). Keep it dependency-free if possible (e.g., hash IP /24 → 5 chars). Document it's an approximation, not a real geo lookup.

### Backend — new tests
- `backend/tests/test_totp.py` — full TOTP enable / verify / disable / recovery-code consume cycle.
- `backend/tests/test_rate_limit.py` — sliding window login rate, account lockout, dedup of lockout emails.
- `backend/tests/test_error_envelope.py` — hit 6+ error paths and assert envelope shape consistency.
- `backend/tests/test_security_headers.py` — assert header presence on a sample of routes.
- `backend/tests/test_request_id.py` — request-id round-trip + log correlation.

### Root docs
- `CLAUDE.md` — extend "Auth flow" with 2FA, "Sicherheitsmaßnahmen" with rate-limit + lockout + headers.

## DB migrations

1. `users_add_lockout_columns` — `failed_login_count`, `locked_until`
2. `user_totp`
3. `user_recovery_codes`
4. `login_attempts`
5. `known_devices`

## API endpoints (added this phase)

- `POST /api/account/2fa/setup` → `{secret_b32, otpauth_uri, qr_svg}` (server-rendered SVG to keep secret out of bundle)
- `POST /api/account/2fa/enable` — body `{code}` → `{recovery_codes: [10]}` (one-time response)
- `POST /api/account/2fa/disable` — body `{password, code_or_recovery}`
- `POST /api/account/2fa/recovery-codes/regenerate` — body `{password, code_or_recovery}` → `{recovery_codes: [10]}`
- `GET  /api/account/sessions` — list active refresh tokens
- `DELETE /api/account/sessions/{id}` — revoke one
- `POST /api/auth/login/recovery` — body `{email, password, recovery_code}`

## Frontend

None this phase. Phase 2 builds the UI for these endpoints.

## Dependencies added

**pip:** `pyotp`, `qrcode[pil]` (for SVG QR rendering), `cryptography` (Fernet for TOTP secret encryption).
**npm:** none.

## Risks / pitfalls

1. **TOTP clock drift** — accept ±1 step (30s window before/after). pyotp default is 0 ; explicitly set `valid_window=1`.
2. **Replay protection** — track `last_used_counter` (the time-step counter that was last accepted) to prevent replay within the window. Refuse codes with counter ≤ stored value.
3. **Recovery code one-time display** — never readable after enable/regenerate; confirm UI design covers this in Phase 2.
4. **Rate-limit Redis keys** — namespace as `fh:rl:login:ip:{hash}` and `fh:rl:login:user:{id}`; set TTL = window. Don't keep indefinitely.
5. **Lockout vs rate-limit interaction** — IP rate limit is a soft block (returns 429); per-account lockout is harder (returns 423 Locked with retry-after). Document the distinction; tests cover both paths.
6. **Geohash crudeness** — hashing `ip /24` is not real geolocation. Don't over-promise in the new-device alert email later.

## Verification

```bash
# Enable 2FA on the bootstrapped admin
curl -X POST http://127.0.0.1:8000/api/account/2fa/setup \
  -H "Authorization: Bearer $TOKEN"
# scan QR or manually enter secret in Authy/etc., then:
curl -X POST http://127.0.0.1:8000/api/account/2fa/enable \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"code":"123456"}'

# Login now requires TOTP
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -d '{"email":"admin@example.com","password":"..."}'
# → 401 TOTP_REQUIRED
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -d '{"email":"admin@example.com","password":"...","totp_code":"123456"}'
# → 200

# Trigger lockout
for i in {1..5}; do curl -X POST .../api/auth/login -d '{"email":"admin@example.com","password":"wrong"}'; done
# next attempt → 423 ACCOUNT_LOCKED

# Trigger rate limit
for i in {1..11}; do curl -X POST .../api/auth/login -d '{"email":"x","password":"x"}'; done
# 11th → 429 RATE_LIMITED

docker compose exec backend pytest -q --cov=app --cov-report=term-missing
```

## Out of scope

- New-device email alerts (rows recorded, email deferred to Phase 7)
- WebAuthn / passkeys (Phase 8)
- Frontend UI (Phase 2)
