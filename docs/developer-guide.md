# Developer guide

Part of the [file:Heron](../README.md) documentation.

Detail-level invariants live in `CLAUDE.md` (the cross-cutting rules, the source of
truth for AI-assisted sessions) and `docs/engineering/<area>.md` (one deep dive per
subsystem, listed in CLAUDE.md's documentation map); this is the human walkthrough.

## Code layout

```
backend/app/
├── main.py            # FastAPI factory + middleware + exception handlers
├── config.py          # Pydantic Settings - fail-fast on insecure prod defaults
├── database.py        # SQLAlchemy 2.0 engine + Base + get_db
├── dependencies.py    # get_current_user/_admin, get_actor, require_scope, require_2fa_complete
├── middleware/        # errors (envelope + error capture), request_id, security_headers
├── models/            # SQLAlchemy models, one per domain
├── schemas/           # Pydantic request/response shapes
├── routers/           # routers (admin/* is a sub-package); mounted in main.py
├── services/          # business logic - routers stay thin
├── workers/           # ARQ worker config + cron functions
├── templates/email/{en,de}/   # Jinja2 templates + subjects.json
└── utils/             # crypto, geohash, ua_fingerprint, http_range, timeutil, …
alembic/versions/      # migrations (idempotent helpers in env.py)
tests/                 # pytest + pytest-asyncio (SQLite; autouse mocks for Redis/SSE)

frontend/src/
├── views/             # Vue Router pages
├── components/        # reusable widgets (NotificationBell, Pager, RichTextEditor, …)
├── composables/       # useUpload, useApiError, useDebouncedSearch, useSiteDateFormat, …
├── api/               # axios clients per domain
├── stores/            # Pinia (auth, ui, notifications)
├── config/            # adminNav.ts (admin sidebar map)
├── router/index.ts    # routes + guards (silent refresh, requires-2fa, requires-admin)
├── i18n/locales/{en,de}.json
└── styles/            # tokens.css, global.css
```

## Request flow (typical)

```
SPA → axios (refresh interceptor + paramsSerializer { indexes: null })
    → Traefik (host) → FastAPI router (Depends(get_actor): JWT or fh_<id>_<sec> bearer)
    → service module (logic + audit + notification dispatch)
    → SQLAlchemy session → MariaDB
```

Array query params need `paramsSerializer: { indexes: null }` → `?state=active&state=expired`
(FastAPI `Query(default=[])`), not `?state[]=active`.

## Auth specifics

- Access JWT: HS256, 15 min, `{sub, iat, exp, jti, type:"access"}`. Refresh: 64 random bytes, SHA-256 in DB, 7 d, httpOnly cookie scoped `/api/auth`, `SameSite=Lax`, `Secure` in prod.
- Rotation on every refresh; reuse of a rotated token revokes the **entire user family** (`refresh_token_reused`). `services/jwt_session.py::create_refresh_token` → `enforce_session_cap` enforces the session cap across all login flows (password / recovery / OIDC / WebAuthn / passkey / register-from-invite).
- **A session ends `auth.session_max_age_days` (default 30, `0` = never) after its sign-in**, however active it stays: rotation carries the sign-in time forward, refuses to refresh past the cap (`401 INVALID_REFRESH`, audited `session_max_age_reached`, the rest of the user's sessions untouched) and never issues a token that outlives it. The browser and the desktop client then ask to sign in again. Editable on the Sessions settings page.
- **API-token scopes** are deny-by-default: every `get_actor` route carries `Depends(require_scope(...))`, enforced only for `auth_via == "api_token"` (JWT/session + NULL-scope pass through).
- **Passkey sign-in** (`auth.passkey_signin`, off by default): `POST /api/auth/passkey/begin` + `/complete` sign in with a passkey alone - no account named in the options, user verification required, every failure one `401 PASSKEY_SIGNIN_FAILED`, and none counts towards the account lockout (limited per IP instead). While it is on, a passkey also counts as 2FA: a password sign-in answers `401 WEBAUTHN_REQUIRED` and finishes through `/api/auth/webauthn/*`, and an SSO sign-in is finished at `POST /api/auth/2fa/passkey/begin` + `/complete`.
- **Step-up re-authentication.** These routes also require the caller's own current password in the request body (`password`; a form field on the import), even with a valid session or API token: `POST /api/admin/backup/export`, `POST /api/admin/backup/import`, `POST /api/admin/users/{id}/erase`, `POST /api/account/api-tokens`, `POST /api/admin/api-tokens`, `POST /api/account/webauthn/register/begin`, the self-update routes `POST /api/admin/system/update`, `/rollback` and `/update/now`, `PUT /api/admin/settings/auto-update` when it turns automatic updates on or changes them while they are on (turning them off needs no password), and `PUT /api/admin/settings/encryption` whenever it switches encryption at rest on or off. A wrong password answers **`403 INVALID_PASSWORD`** (never 401 - the caller IS authenticated), is rate-limited per user and audited as `step_up_failed`. SSO-only accounts have no password and cannot pass it; the CLI escape hatch is the recovery. The SMTP/IMAP test buttons ask for it only when a stored secret would travel to a host other than the saved one.
- **Approving a share** (`POST /api/shares/{id}/approve`) requires a `content_fingerprint` - the digest of the files as the approver saw them; a stale one answers `409 CONTENT_CHANGED`.
- **Self-update only moves forward.** `POST /api/admin/system/update` refuses a tag older than the running version (`409 DOWNGRADE_REFUSED`); go back with Rollback, which also restores the database schema pointer.

## Upload pipeline

```
client → POST /api/uploads/init (HMAC envelope; files row state=uploading)
       → POST /uploads/ (TUS; Upload-Metadata = fh_payload + fh_sig)
         → tusd → pre-create hook → /api/internal/tus-hooks (HMAC verify, Redis Lua quota reserve)
         → tusd writes ./data/uploads/<tus-id>
         → post-finish hook → backend finalises (shutil.move → ./data/files/yyyy/mm/<uuid>.bin,
           state=ready_unscanned) → enqueue av_scan_file → clean | infected
```

Direct uploads (≤ `MAX_DIRECT_UPLOAD_BYTES`) skip tusd via `POST /api/uploads/direct`.
Finalize uses `shutil.move` (rename fast path, else copy+unlink) - portable across
bind mounts; **don't** revert to `os.rename` (cross-device in containers).

## ARQ workers + cron

Config: `backend/app/workers/worker.py::WorkerSettings` (queue `fileheron:default`,
`max_tries=5` - `send_email_job` registers its own, see `workers/send_email.py` - all idempotent). A minute **dispatcher** (`workers/cron_dispatch.py`)
reads the per-cron schedule registry (`services/cron_schedule.py` + `cron.<name>.*`
settings) and enqueues jobs that are due; admins re-time them on
[Scheduled tasks](admin-guide.md#scheduled-tasks-adminscheduled-tasks). Defaults reproduce the
historical cadence.

<details>
<summary>Current cron jobs (defaults)</summary>

**Hourly-ish:** `expire_files`, `share_expiring_24h_warning`, `quota_reconcile`,
`cleanup_stale_uploads`, `cleanup_abandoned_uploads`, `cleanup_expired_tokens`,
`ops_check` (cron + Redis health → `ops_alert`), `disk_check` (low-storage flag),
`anomaly_check` (heuristic alerts), `rescan_inbound_attachments`,
`release_check` (~daily; filters backend `vX.Y.Z` tags, matched in full).

**Every 5 min:** `imap_poll` (self-gated on `imap.enabled`/mode/interval), `expire_secrets` (ends and shreds expired secrets and any nobody can read any more, and closes unanswered secret requests past their time).
**Every minute:** `drain_pending_update` (applies a postponed update once transfers drain, and reports how a handed-off update ended).
**Daily 03:30:** `auto_update` (installs a newer release when automatic updates are on).

**Daily ~02:xx:** `analytics_aggregate`, `purge_old_quarantine`, `cleanup_pending_invites`,
`cleanup_read_notifications`, `prune_history`, `reclaim_orphaned_files`.

`prune_history` prunes `audit_log`, `download_log`, `email_log`, `login_attempts`,
`webhook_deliveries`, `error_log`, `inbound_messages`, ended secrets and ended secret requests (each window `0` disables).

**Every 10 min:** `encrypt_existing_files` (encryption at rest: encrypts stored files, deletes the copies they replace, releases uploads waiting for encryption; with the switch off it only does the last two).

**Event-driven:** `av_scan_file(file_id)` (quarantines on infection),
`encrypt_new_files` (encryption at rest: encrypts a scanned upload, then releases it) and
`send_email_job(...)` (resolves SMTP per job; permanent 5xx → `email_undeliverable` +
admin alert).

</details>

## Adding things

- **New admin setting:** add a key in `services/settings.py::Keys`; read via `settings_svc.get/get_bool/set_value`; add `GET`/`PUT` under `routers/admin/` (audit on PUT); Pydantic schema; a `frontend/src/views/AdminSettings<Feature>.vue` + route + `adminNav.ts` entry; an i18n key per locale (the parity test catches gaps).
- **New audit event:** add the string to `AuditEventType`; call `record_audit_event(...)`. The audit view is generic.
- **New notification category:** add to `NotificationCategory` + `_DEFAULT_CHANNEL`; add `templates/email/{en,de}/<slug>.{txt,html}.j2` + `subjects.json`; call `services/notification.py::dispatch(...)`. **Everything** goes through `dispatch` - never write `notifications` or send email directly.

## Migration discipline

Every revision uses the `_has_table` / `_has_column` / `_has_index` helpers (re-runnable
after a partial failure). Timestamps are **naive UTC** (`utils/timeutil.py::utc_now`).
Roll-forward only; restore from backup to roll back. MariaDB reserves `key` - backtick
reserved identifiers in raw SQL (SQLite tests miss it; the CI alembic-roundtrip against
real MariaDB catches it).
