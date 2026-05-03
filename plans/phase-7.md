# Phase 7 — OIDC + i18n Polish + Hardening + Backups

> Master plan: `/home/mk/.claude/plans/i-want-to-create-melodic-whale.md`
> Depends on Phase 6b being complete.

## Goal

Add OIDC employee SSO (Authlib): start, callback, account linking by verified-email, group→role mapping, IdP-down behavior. Complete vue-i18n DE+EN translations across the entire UI (and email templates were done in 6a — verify coverage). New-device login alerts (now actually emailed). Real HIBP password breach check (k-anonymity API, no plaintext sent). `scripts/backup.sh` + `scripts/restore.sh` (restic-based).

## Pre-phase decisions

1. **OIDC provider** — Authentik / Keycloak / EntraID / Google Workspace / other? Affects discovery URL conventions (e.g., `https://idp.example.com/application/o/fileheron/.well-known/openid-configuration`) and group claim path.
2. **Group claim path** — env-configurable `OIDC_GROUPS_CLAIM=groups` (default for most IdPs). Some put groups in `realm_access.roles` (Keycloak); make it path-walkable.
3. **OIDC logout** — RP-initiated logout (redirect to IdP `end_session_endpoint`) or local-only logout? *Default: local-only for v1; document RP-initiated as future work.*
4. **HIBP cache TTL** — cache miss/hit per password-prefix in Redis for 1h to reduce HIBP latency on repeat checks. *Default: 1h.*
5. **Backup target** — local `./backups/` only, or also push to a remote restic repo (S3, B2, SFTP)? *Default: local; remote-push is a single env var (`BACKUP_RESTIC_REPO`) but optional.*
6. **Backup frequency** — cron expression in env, default daily 02:00. Document host crontab line; don't bake into compose.

## Acceptance criteria

- OIDC discovery URL configurable via `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_GROUPS_CLAIM`, `OIDC_ADMIN_GROUPS`, `OIDC_EMPLOYEE_GROUPS`. Frontend reads `oidc_enabled` from `/api/config-public`; if true, login page shows "Sign in with company SSO" button.
- Click button → redirects to IdP → logs in → callback exchanges code → if local user with matching verified email exists, links them (sets `users.oidc_subject`); otherwise creates a new user with role mapped from groups; sets cookies; redirects home.
- IdP unreachable: existing OIDC users see a clear "SSO unavailable" message; existing local users (clients) can still log in.
- Switching UI language is reflected in (a) frontend strings, (b) email locale on next email, (c) date formatting (dayjs.locale + babel locale on backend).
- New-device login alert: on login from a (UA, IP-geohash) pair not in `known_devices`, email enqueued (`login_alert` template from 6a). Same-octet patch-version UA changes are suppressed (don't email on every Chrome update).
- Setting a new password triggers HIBP k-anonymity check; if breached, returns 422 `{"code":"PASSWORD_BREACHED"}`. Cached 1h in Redis.
- `scripts/backup.sh` produces a dated archive under `./backups/YYYY-MM-DD/`: mysqldump of MariaDB + tar of `./data/files/` + tar of `./data/quarantine/` + Redis RDB snapshot. Restic-encrypted (`BACKUP_RESTIC_PASSWORD` from env). Optionally pushes to `BACKUP_RESTIC_REPO` if set.
- `scripts/restore.sh` reverses it: takes a dated snapshot ID, restores DB + files + Redis, with confirm prompts.
- pytest + manual e2e tests cover all of the above.

## Files to create / modify

### Backend — new services
- `backend/app/services/oidc.py` — Authlib client. Functions:
  - `start_oidc_flow(state)` → returns redirect URL + sets state cookie
  - `handle_callback(code, state)` → exchanges, validates ID token, extracts claims, returns `(user, is_new)`
  - `_resolve_role_from_groups(groups_claim)` → admin / employee / client based on env mapping
- `backend/app/services/hibp.py` — replaces `hibp_stub.py`. K-anonymity API: SHA-1 password, send first 5 chars of hash to `https://api.pwnedpasswords.com/range/{prefix}`, check if remainder appears in response. Cache results in Redis 1h.
- `backend/app/services/login_alert.py` — `record_login_and_alert_if_new_device(user, request)`: compute UA fingerprint + IP geohash, look up `known_devices`, if new: insert + enqueue `login_alert` notification.
- Wire `record_login_and_alert_if_new_device` into the login success path in `services/auth.py`.

### Backend — new routers
- `backend/app/routers/oidc.py`:
  - `GET /api/auth/oidc/start` — redirect to IdP
  - `GET /api/auth/oidc/callback` — handle code, set cookies, redirect home (or to original `?redirect=` target)
- `backend/app/routers/health.py` (extend) — `GET /api/config-public` → `{oidc_enabled, app_name, default_locale}`. Public, no auth.

### Backend — extended
- `backend/app/dependencies.py` — `get_current_user` already fine; OIDC users authenticate via the same JWT/refresh model after callback.
- `services/auth.py:reset_password` and `account.py:change_password` — call HIBP check.

### Backend — new tests
- `backend/tests/test_oidc.py` — mock IdP discovery + token endpoint; test new-user creation, account linking, role mapping, unverified-email rejection.
- `backend/tests/test_hibp.py` — mock pwnedpasswords API; test cache hits, breached / not-breached, network-down fallback (allow if HIBP unavailable, log warning).
- `backend/tests/test_login_alert.py` — first login from new (UA, geohash) emits alert; second login from same does not; suppression on UA patch-version diff.

### Frontend — extended
- `frontend/src/api/oidc.ts` — `getPublicConfig()`, no other client-side OIDC code (everything is server-redirect).
- `frontend/src/views/Login.vue` — show "Sign in with company SSO" button conditional on `oidc_enabled`.
- `frontend/src/i18n/locales/{en,de}.json` — verify full coverage; add any keys still in EN-only. Default-locale switch tested.
- `frontend/src/api/account.ts` — pass HIBP error code through to UI; show clear breach message in PasswordStrength component.

### Backend — i18n verification
- For each email template, confirm DE + EN versions exist and render. Add missing strings.

### Backend — backup/restore scripts
- `scripts/backup.sh`:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  STAMP=$(date -u +%Y-%m-%d_%H%M%S)
  DEST="./backups/$STAMP"
  mkdir -p "$DEST"
  docker compose exec -T db mysqldump -u root -p"$DB_ROOT_PASSWORD" "$DB_NAME" > "$DEST/db.sql"
  tar -C ./data -czf "$DEST/files.tar.gz" files quarantine
  docker compose exec -T redis redis-cli SAVE
  docker cp $(docker compose ps -q redis):/data/dump.rdb "$DEST/dump.rdb"
  if [ -n "${BACKUP_RESTIC_REPO:-}" ]; then
    restic backup --repo "$BACKUP_RESTIC_REPO" --password-file <(echo "$BACKUP_RESTIC_PASSWORD") "$DEST"
  fi
  echo "Backup complete: $DEST"
  ```
- `scripts/restore.sh` — reverses (with confirms).

### Compose / env
- `.env.example` — add: `OIDC_*`, `HIBP_ENABLED=true`, `BACKUP_RESTIC_PASSWORD`, `BACKUP_RESTIC_REPO`.

## DB migrations

None new (the `users.oidc_subject` column was created in Phase 1a, populated here).

## API endpoints (added this phase)

- `GET /api/auth/oidc/start`
- `GET /api/auth/oidc/callback`
- `GET /api/config-public`

## Frontend routes

- (none new; just enable conditional UI)

## Dependencies added

**pip:** `authlib`, `httpx[http2]` (for IdP and HIBP).
**system:** `restic` (host requirement; document in README).
**npm:** none.

## Risks / pitfalls

1. **OIDC group claim variability** — different IdPs structure groups differently (`groups`, `realm_access.roles`, `roles`, etc.). Make `OIDC_GROUPS_CLAIM` path-walkable (dot-separated path in env).
2. **Email-based account linking is a security boundary** — only link if IdP claims `email_verified=true`. Reject otherwise with a clear UI error directing the user to verify their email at the IdP.
3. **OIDC unverified email + local account exists** — if a fresh OIDC login has unverified email but a verified local account exists with the same email → reject (don't auto-link, don't auto-create).
4. **HIBP latency** — 1-2s per check. Run server-side, after a quick "obviously common passwords" check, and cache results 1h in Redis to short-circuit repeats.
5. **HIBP availability** — if pwnedpasswords API is down, default behavior should be **allow** (don't block password changes due to upstream failure) but log a warning. Alternative: refuse and require admin override; *default: allow*.
6. **Login alert spam** — patch-version UA changes (e.g., Chrome 138.0.7236.50 → 138.0.7236.62) shouldn't trigger alerts. Strip patch version from UA fingerprint hash; only alert on major version or different OS/browser family.
7. **Backup script is bash + restic** — keep it lean. Test it on a real-ish dataset before announcing it works. Document recovery procedure in README.

## Verification

```bash
# Local Authentik / Keycloak test container in docker-compose.dev.yml — set OIDC_ISSUER_URL to it
# Visit /login → click SSO → log in to IdP → land on /

# Test account linking
# Pre-create a local user with email a@example.com; configure IdP user with same email + email_verified=true
# OIDC login → user_id matches the local user; users.oidc_subject is now populated

# HIBP check
curl -X POST .../api/account/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"current_password":"...","new_password":"password123"}'
# → 422 PASSWORD_BREACHED

# New-device alert (use a fresh browser / private window)
# Backend logs should show login_alert email

# Backup + restore round trip
./scripts/backup.sh
docker compose down && rm -rf data/db data/files
./scripts/restore.sh ./backups/2026-05-15_021500
docker compose up -d
# verify the test data is back

docker compose exec backend pytest -q
```

## Out of scope

- WebAuthn / passkeys → **Phase 8**
- Per-file envelope encryption → **Phase 8**
- Performance load test → **Phase 8**
- Right-to-erasure UI polish (already shipped basic in Phase 6b) → **Phase 8**
