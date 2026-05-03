# Phase 1a — Foundation + Backend Auth Core (no 2FA)

> Master plan: `/home/mk/.claude/plans/i-want-to-create-melodic-whale.md`
> Reference projects: `/home/mk/claude/prater-zeiterfassung-v2/` (FastAPI patterns), `/home/mk/claude/reclaim/` (JWT + refresh rotation patterns)

## Goal

Stand up the docker-compose skeleton (MariaDB + Redis + FastAPI + nginx-static placeholder) with Alembic migrations, fail-fast config, audit-log infrastructure, and a working invite-only register / login / refresh-with-rotation / logout / forgot-password / email-verify flow. **No TOTP yet** — that lands in Phase 1b. Frontend is an empty SPA placeholder.

## Pre-phase decisions (resolve at session start)

1. **Argon2 cost params** — confirm `(time=3, memory=64MiB, parallelism=2)` or adjust for VPS spec.
2. **Refresh-token storage** — Argon2 vs SHA-256 in DB? Argon2 is overkill for 64-byte high-entropy random tokens; SHA-256 saves ~50ms per refresh. *Default recommendation: SHA-256.*
3. **HIBP scope this phase** — interface only (stub returns OK), real implementation in Phase 7.

## Acceptance criteria

- `docker compose -f docker-compose.yml -f docker-compose.dev.yml up` brings up healthy `db`, `redis`, `backend`. `nginx-spa` placeholder serves a "Coming in Phase 2" page.
- `GET /api/health` returns `{"status":"ok"}` and is wired to the backend's healthcheck.
- An admin can log in with `ADMIN_BOOTSTRAP_EMAIL` + the bootstrap password, receives access token in body + refresh cookie scoped to `/api/auth`. Calling `/api/auth/refresh` rotates the refresh token; using the old one a second time triggers `TOKEN_REUSE` → revoke entire family + audit-log.
- A seed script creates an `invite_tokens` row; `POST /api/auth/register-from-invite` consumes it and creates a verified user.
- `audit_log` rows present for: `login_success`, `login_failure`, `password_reset`, `refresh_token_rotated`, `refresh_token_reused`, `invite_created`, `invite_consumed`, `user_registered`, `email_verified`.
- `pytest -q` green; coverage on `services/auth.py` and `routers/auth.py` ≥80%.

## Files to create

### Compose & docker
- `docker-compose.yml` — services: db (mariadb:11 + healthcheck.sh), redis (redis:7-alpine + redis-cli ping), backend (build), nginx-spa (nginx:alpine, placeholder index.html). `internal` bridge network. JSON logging on all. Required env vars use `${VAR:?msg}`.
- `docker-compose.dev.yml` — override: backend hot-reload (`uvicorn --reload`), expose `db` on 127.0.0.1:3306, mount `./backend/app:/app/app`.
- `.env.example` — DB_*, REDIS_*, JWT_SECRET, JWT_ACCESS_MINUTES=15, JWT_REFRESH_DAYS=7, EMAIL_PEPPER, COOKIE_SECURE=true, ADMIN_BOOTSTRAP_EMAIL, TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD, SMTP_HOST/PORT/USER/PASSWORD/FROM_EMAIL/FROM_NAME, APP_URL, LOG_LEVEL, RATE_LIMIT_LOGIN, RATE_LIMIT_REGISTER, COMPOSE_PROJECT_NAME=fileheron.
- `docker/backend/Dockerfile` — multi-stage Python 3.12-slim, install pyproject deps, run `uvicorn`. Match `prater-zeiterfassung-v2/docker/backend/Dockerfile` shape.
- `docker/backend/Dockerfile.dev` — adds `watchdog`, source mount, `uvicorn --reload`.
- `docker/backend/entrypoint.sh` — wait-for-mariadb (a small healthcheck loop), `alembic upgrade head`, run admin-bootstrap, `exec` uvicorn.
- `docker/mariadb/init.sql` — collation/charset only (utf8mb4_general_ci).

### Backend — config & app
- `backend/pyproject.toml` — deps: fastapi[standard], uvicorn[standard], sqlalchemy>=2, pymysql, alembic, pydantic>=2, pydantic-settings, argon2-cffi, pyjwt, jinja2, python-multipart, redis>=5, arq, python-json-logger, aiosmtplib, python-dotenv. Dev: pytest, pytest-asyncio, pytest-cov, ruff, mypy, aiosqlite, httpx.
- `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`
- `backend/app/__init__.py`
- `backend/app/main.py` — FastAPI app factory with `lifespan` (DB pool + Redis pool init), middleware mount, router registration. Port from `prater-zeiterfassung-v2/backend/app/main.py` minus the SPA-mount (we serve SPA from nginx).
- `backend/app/config.py` — `Settings(BaseSettings)`, fail-fast `sys.exit()` on missing prod secrets. Match `prater-zeiterfassung-v2/backend/app/config.py`.
- `backend/app/database.py` — engine `pool_pre_ping=True`, `pool_recycle=3600`; `Base(DeclarativeBase)`; `get_db()` generator. Port verbatim from `prater-zeiterfassung-v2/backend/app/database.py`.
- `backend/app/dependencies.py` — `get_db`, `get_current_user`, `get_current_admin`, `require_role(role)`.

### Backend — middleware
- `backend/app/middleware/errors.py` — `AppError` exception + FastAPI exception handler emitting `{"error","code","details"}` envelope. Port shape from `reclaim/backend/src/middleware/errorHandler.ts`.
- `backend/app/middleware/request_id.py` — UUID per request, attached to log context + `X-Request-Id` response header.
- `backend/app/middleware/security_headers.py` — HSTS (prod), X-Frame-Options=DENY, X-Content-Type-Options=nosniff, Referrer-Policy=strict-origin-when-cross-origin, CSP relaxed for Element Plus inline styles (document the relaxation in `CLAUDE.md`).

### Backend — utils
- `backend/app/utils/crypto.py` — `argon2_hash`, `argon2_verify`, `random_token(n)` (urlsafe_b64), `sha256_hex`, `hmac_sign(payload, secret)` (for tusd metadata, used in Phase 3a but lives here).
- `backend/app/utils/logger.py` — python-json-logger setup; reads `LOG_LEVEL` from config.
- `backend/app/utils/emailing.py` — aiosmtplib helper with logs-fallback if `SMTP_HOST` empty. Port pattern from `reclaim/backend/src/services/email.ts`.

### Backend — models (this phase)
- `backend/app/models/__init__.py`
- `backend/app/models/user.py` — `id`, `email_hash` (HMAC-SHA256, unique, indexed), `email_hint` (first letter + domain), `password_hash` (Argon2), `display_name`, `role` enum (admin/employee/client), `locale` enum (de/en), `email_verified` bool, `is_disabled` bool, `created_at`, `last_login_at`, `oidc_subject` (nullable, unique-when-set, populated in Phase 7), `quota_bytes` (nullable=unlimited), `created_by` FK→users (nullable, the inviter).
- `backend/app/models/invite_token.py` — `id`, `token_hash`, `email_hint`, `target_role`, `created_by` FK, `created_at`, `expires_at` (24h), `used_at` (nullable), `used_user_id` FK.
- `backend/app/models/email_verify_token.py` — `id`, `user_id`, `token_hash`, `expires_at`, `used_at`.
- `backend/app/models/password_reset_token.py` — `id`, `user_id`, `token_hash`, `expires_at`, `used_at`. On consume → revoke all refresh tokens.
- `backend/app/models/refresh_token.py` — `id`, `user_id`, `token_hash` (sha256_hex per pre-phase decision), `expires_at`, `revoked_at`, `replaced_by` (self-FK), `created_ip`, `created_ua`.
- `backend/app/models/audit_log.py` — `id BIGINT`, `created_at`, `actor_user_id` (nullable), `event_type` (string enum), `target_type`, `target_id`, `request_id`, `ip`, `metadata_json`.

### Backend — schemas
- `backend/app/schemas/common.py` — `APIResponse`, `PaginatedResponse{items,total,page,page_size,has_more}`.
- `backend/app/schemas/auth.py` — `RegisterFromInviteRequest`, `LoginRequest`, `LoginResponse`, `RefreshResponse`, `ForgotPasswordRequest`, `ResetPasswordRequest`, `VerifyEmailRequest`.
- `backend/app/schemas/account.py` — `MeResponse`, `ChangePasswordRequest`, `InviteRequest`.

### Backend — services
- `backend/app/services/auth.py` — `register_from_invite`, `login` (sans 2FA), `issue_tokens`, `rotate_refresh` (with reuse-detection family-revoke), `logout`, `forgot_password`, `reset_password`, `verify_email`. Reuse-detection algorithm ported from `reclaim/backend/src/services/auth.ts`.
- `backend/app/services/audit.py` — single function `record_audit_event(db, actor_id, event_type, target_id=None, metadata=None, request=None)`.
- `backend/app/services/email.py` — wraps `utils/emailing.py`, renders Jinja templates by `User.locale`.
- `backend/app/services/admin_bootstrap.py` — `promote_by_email` pattern from `reclaim/backend/src/services/admin-bootstrap.ts`. Run on backend startup.
- `backend/app/services/invite.py` — `create_invite`, `consume_invite` (idempotent token consumption).
- `backend/app/services/hibp_stub.py` — interface returning `{"breached": False}`. Real implementation lands in Phase 7.

### Backend — routers
- `backend/app/routers/health.py` — `GET /api/health` returns service health summary.
- `backend/app/routers/auth.py` — register-from-invite, login (no 2FA challenge yet), refresh, logout, forgot-password, reset-password, verify-email, resend-verification. Cookie scoping to `/api/auth`.
- `backend/app/routers/account.py` — `GET /api/account/me`, `POST /api/account/change-password`, `POST /api/account/invite` (employees+admins).

### Backend — email templates (text-only stubs in this phase)
- `backend/app/templates/email/{en,de}/verify.txt.j2`
- `backend/app/templates/email/{en,de}/reset_password.txt.j2`
- `backend/app/templates/email/{en,de}/invite.txt.j2`

### Backend — scripts
- `backend/scripts/create_admin.py` — CLI: create initial admin via env or args (called by entrypoint.sh).
- `backend/scripts/seed_dev.py` — CLI: seed dev test account if `TEST_ACCOUNT_EMAIL` set; refuses to run when `ENVIRONMENT=production`.
- `backend/scripts/promote_user.py` — CLI: promote a user to admin (idempotent).

### Backend — tests
- `backend/tests/conftest.py` — aiosqlite in-memory DB, DI overrides, async `httpx.AsyncClient` fixture.
- `backend/tests/test_auth_flow.py` — register-from-invite, login (no 2FA), refresh rotation, refresh-reuse → family revoke + audit, logout.
- `backend/tests/test_invite.py` — invite create/consume, expiry, single-use enforcement.
- `backend/tests/test_audit.py` — audit rows present for each event type.
- `backend/tests/test_admin_bootstrap.py` — env-driven bootstrap idempotent.

### Root docs
- `CLAUDE.md` — extend skeleton: Quickstart, Project structure, Auth flow, Database schema (Phase 1a tables only).
- `README.md` — already-skeletoned; this phase adds Quickstart commands + env-var quick reference.
- `pyproject.toml` (root) — workspace ruff/mypy globals if useful.

## DB migrations (Alembic)

Naming pattern: `{YYYYMMDDHHMM}_{slug}.py`. Order:

1. `users`
2. `invite_tokens`
3. `email_verify_tokens`
4. `password_reset_tokens`
5. `refresh_tokens`
6. `audit_log`

## API endpoints (this phase)

- `GET  /api/health`
- `POST /api/auth/register-from-invite` — `{token, password, display_name, locale}`
- `POST /api/auth/login` — `{email, password}` → 200 `{access_token}` + cookies (no TOTP yet)
- `POST /api/auth/refresh` — cookie-only — rotates and returns new access token
- `POST /api/auth/logout` — invalidates current refresh
- `POST /api/auth/forgot-password` — always 200 (no enumeration)
- `POST /api/auth/reset-password` — `{token, new_password}` — invalidates all sessions of that user
- `POST /api/auth/verify-email` — `{token}`
- `POST /api/auth/resend-verification` — auth required
- `GET  /api/account/me`
- `POST /api/account/change-password`
- `POST /api/account/invite` — employees+admins only, body `{email, display_name, target_role, initial_groups: []}` (groups field land in Phase 4 as opaque IDs; ignore until then)

## Frontend

None this phase. `nginx-spa` serves a static "Coming in Phase 2" placeholder so users hitting the root URL see something sensible.

## Dependencies added

**pip:** see `backend/pyproject.toml` above.
**npm:** none.

## Risks / pitfalls

1. **Argon2 memory cost** — defaults can OOM small VPS under concurrency. Pin `(time=3, memory=64MiB, parallelism=2)` and document the tuning knob.
2. **Refresh-token rotation race** — two parallel refresh calls can both pass the "revoked?" check. Use a conditional UPDATE: `UPDATE refresh_tokens SET revoked_at=now(), replaced_by=:new_id WHERE id=:id AND revoked_at IS NULL`. If `affected_rows == 0` → assume reuse → revoke entire family + audit `refresh_token_reused`.
3. **Cookie scoping** — refresh cookie scoped to `/api/auth` (not `/`) so future tusd/upload routes never carry the cookie. Match `prater-zeiterfassung-v2/backend/app/routers/auth.py` cookie config.
4. **Email-hint leakage** — store first-letter + domain only (`"j****@example.com"`); never plaintext email queryable. Use HMAC for the lookup index.
5. **Alembic + SQLAlchemy 2.0** — autogenerate sometimes misses index renames; review every generated migration manually before commit.
6. **Bootstrap credential exposure** — admin's bootstrap password should be either generated and printed once (with a clear "save this" warning) or set explicitly via env. Don't ship a default password.

## Verification

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
docker compose ps                               # all healthy
curl http://127.0.0.1:8000/api/health           # {"status":"ok"}

# Admin login (after first boot, watch backend logs for the bootstrap message)
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"<bootstrap pw>"}' \
  -c /tmp/fh.cookies.txt

# Refresh rotation
curl -X POST http://127.0.0.1:8000/api/auth/refresh \
  -b /tmp/fh.cookies.txt -c /tmp/fh.cookies.txt

# Reuse detection (run the same call twice with the same cookies snapshot — second should fail and revoke family)
cp /tmp/fh.cookies.txt /tmp/fh.cookies.copy.txt
curl -X POST http://127.0.0.1:8000/api/auth/refresh -b /tmp/fh.cookies.copy.txt    # this is reuse — should 401

# Tests
docker compose exec backend pytest -q
```

## Out of scope (this phase)

- TOTP 2FA setup + login flow → **Phase 1b**
- Login rate limiter + per-account lockout → **Phase 1b**
- New-device alerts → **Phase 7**
- Real HIBP check → **Phase 7**
- Frontend UI → **Phase 2**
- File handling → **Phase 3a**
