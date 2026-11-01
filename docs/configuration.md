# Settings reference

Part of the [file:Heron](../README.md) documentation.

Three configuration layers:

1. **Environment variables** (`backend/app/config.py`, via `.env`) - read at **boot**; the only place for secrets + infra. `↻` = also live-tunable on Advanced.
2. **Admin runtime settings** (`app_settings` kv, `/admin/settings/*`) - applied **live, no restart**.
3. **Per-user preferences** (`/account`).

### 1. Environment variables (boot)

The annotated source of truth is [`.env.example`](../.env.example). `↻` = also live-tunable
via `/admin/settings/advanced`.

<details>
<summary>App, database, Redis, auth</summary>

| Variable | Default | What it does |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` forces `COOKIE_SECURE=true` and fail-fasts on placeholder secrets / `AV_SKIP=true`. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `APP_URL` | `http://localhost:8080` | Public URL baked into links when no `site.url` is set; WebAuthn/OIDC origin fallback. |
| `APP_NAME` | `fileHeron` | Brand name. ↻ (`branding.app_name`). Use the `file:Heron` spelling. |
| `DB_HOST`/`DB_PORT` | `db`/`3306` | MariaDB location. |
| `DB_NAME`/`DB_USER` | `fileheron`/`fileheron_app` | Database + app user. |
| `DB_PASSWORD` / `DB_ROOT_PASSWORD` | - (**required**) | App / root passwords. |
| `DB_POOL_SIZE` / `DB_POOL_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SEC` | `10`/`20`/`30` | SQLAlchemy pool. |
| `REDIS_HOST`/`REDIS_PORT` | `redis`/`6379` | ARQ queue, rate limits, quota Lua, SSE pubsub. |
| `JWT_SECRET` | - (**required**, ≥32) | HS256 + HMAC key. |
| `JWT_ALGORITHM` | `HS256` | Access-token algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access-JWT lifetime. ↻ (5-1440). |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-cookie lifetime. ↻ (1-365). |
| `SESSION_MAX_AGE_DAYS` | `30` | Days after sign-in a session can still be refreshed; `0` = no limit. ↻ (0-365). |
| `COOKIE_SECURE` | `false` | Forced `true` in production. |
| `MAX_ACTIVE_SESSIONS_PER_USER` | `10` | Per-user session cap. ↻ (1-100). |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` / `ARGON2_PARALLELISM` | `3`/`65536`/`2` | Argon2id cost. |
| `ADMIN_BOOTSTRAP_EMAIL` / `_PASSWORD` | empty | Bootstrap/promote an admin on boot. |
| `TEST_ACCOUNT_EMAIL` / `_PASSWORD` / `_DISPLAY_NAME` | empty | Dev seed; refused in production. |

</details>

<details>
<summary>SMTP, IMAP, rate limits & lockout</summary>

| Variable | Default | What it does |
|---|---|---|
| `SMTP_HOST` | empty | **Empty (and none set at `/admin/settings/email`) → email is not sent.** Outside production it is printed to the container log; in production only its recipient and subject are logged. |
| `SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD` | `587`/empty/empty | Connection + auth (DB overrides via `/admin/settings/email`). |
| `SMTP_FROM_EMAIL`/`SMTP_FROM_NAME` | `noreply@fileheron.local`/`fileHeron` | Envelope sender. |
| `SMTP_HELO_HOST` | empty | EHLO/HELO name (empty = container FQDN). |
| `IMAP_HOST`/`IMAP_PORT`/`IMAP_USER`/`IMAP_PASSWORD` | empty/`993`/empty/empty | Inbound mailbox (DB overrides via `/admin/settings/imap`; off unless enabled). |
| `IMAP_TLS_MODE`/`IMAP_MAILBOX` | `implicit`/`INBOX` | Inbound fetch tuning; both are overridden by the admin settings page. Poll cadence lives on [Scheduled tasks](admin-guide.md#scheduled-tasks-adminscheduled-tasks), not an env var. |
| `imap.require_known_sender` (admin setting) | `true` | Refuse mail whose sender address matches no enabled account, before anything is stored. **Turn it off if your instance accepts mail from people without accounts.** |
| `imap.tls_insecure` (admin setting) | `false` | Do not verify the mail server's certificate. Only for an internal server with a private CA - the connection stays encrypted, but an on-path attacker can impersonate the server and read the login. |
| `RATE_LIMIT_LOGIN` | `10` | Login attempts / IP / window. ↻ |
| `RATE_LIMIT_REGISTER` | `3` | Register/forgot/verify / IP / window. ↻ |
| `LOGIN_RATE_WINDOW_SEC` | `900` | Per-IP login window. ↻ |
| `LOCKOUT_THRESHOLD` / `LOCKOUT_DURATION_MIN` | `5`/`15` | Consecutive-failure lock + duration. ↻ |

</details>

<details>
<summary>Uploads, antivirus, storage, public links, downloads</summary>

| Variable | Default | What it does |
|---|---|---|
| `TUS_HOOK_SECRET` | - (**required**, ≥32) | HMAC for the tusd↔backend hook envelope. |
| `TUS_HOOK_ALLOWED_IPS` | empty | Optional source-IP allowlist for `/api/internal/tus-hooks`. |
| `MAX_DIRECT_UPLOAD_BYTES` | `104857600` (100 MB) | Direct-multipart cap; larger → TUS. The edge is no longer the binding constraint: `client_max_body_size` in `docker/frontend/nginx.conf` is `1024m`. If you also front the stack with Traefik, check its `maxRequestBodyBytes` - that one still caps the same request. ↻ |
| `UPLOAD_STALE_AFTER_HOURS` | `3` | When an `uploading` row is treated as abandoned. ↻ |
| `STORAGE_ROOT` / `TUS_UPLOAD_DIR` / `QUARANTINE_DIR` | `/data/files` / `/data/uploads` / `/data/quarantine` | **Must share one filesystem.** |
| `STORAGE_BACKEND` | `local` | `local` or `s3` (+ `S3_*`). |
| `STORAGE_LOW_THRESHOLD_PERCENT` / `_BYTES` | `5` / `10 GiB` | Low-disk degradation thresholds. ↻ |
| `CLAMAV_HOST`/`CLAMAV_PORT` | `clamav`/`3310` | clamd endpoint. |
| `AV_SKIP` | `false` | Skip scanning (CI/dev). **Refuses `production + true`.** |
| `AV_MAX_SCAN_BYTES` | `2147483645` | clamd's real scan ceiling. Do **not** raise it - see below. |

> **Antivirus coverage stops at ~2 GiB, and no configuration changes that.**
> clamd clamps `MaxFileSize` to `INT_MAX`, so `MaxFileSize 30G` in
> `docker/clamav/clamd.conf` really means 2147483645 bytes. Above that, clamd
> answers "clean" *without reading the file*. fileHeron still serves those
> uploads - the product supports files far larger than any scanner handles - but
> records them as `files.av_unscanned`, shows an **unscanned** badge next to them
> in the UI, and writes a `file_served_unscanned` audit event. Raising
> `AV_MAX_SCAN_BYTES` does not extend coverage; it only makes fileHeron report a
> verdict clamd never produced.
| `PUBLIC_LINK_PASSWORD_RATE_LIMIT` / `_WINDOW_SEC` / `PUBLIC_LINK_LOCKOUT_SEC` | `10`/`900`/`900` | Per-link password brute-force guard. ↻ |
| `SECRETS_MAX_VIEWS` / `SECRETS_MAX_EXPIRY_DAYS` / `SECRETS_MAX_LIFETIME_DAYS` | `100`/`90`/`90` | Ceilings for a secret: most views, latest expiry, longest life (`0` = no lifetime ceiling). ↻ |
| `SECRETS_PASSPHRASE_MAX_FAILURES` | `10` | Wrong passphrases before a secret is destroyed for that recipient (when that mode is chosen). ↻ |
| `SECRETS_PASSPHRASE_RATE_LIMIT` / `_WINDOW_SEC` / `_LOCKOUT_SEC` | `10`/`900`/`900` | Wrong-passphrase throttle per recipient and address, and how long a link is locked. ↻ |
| `DOWNLOAD_SIGNED_URL_TTL_SEC` | `900` | Signed-download-URL lifetime (30s-1h). ↻ |
| `DOWNLOAD_RESUME_CREDIT_HOURS` | `24` | How long a logged download lets the same user resume it for free (1-168h). Shorter tightens the download budget; longer suits clients that pause overnight. ↻ |

</details>

<details>
<summary>Security/2FA, anomaly, error alerting, retention, self-update, metrics</summary>

| Variable | Default | What it does |
|---|---|---|
| `REQUIRE_2FA` | `none` | Env fallback (`none`/`admins`/`all`); kv policy overrides. |
| `HIBP_ENABLED` | `true` | HaveIBeenPwned k-anonymity check. ↻ |
| `WEBAUTHN_RP_ID` / `WEBAUTHN_RP_NAME` / `WEBAUTHN_ORIGINS` | `localhost` / `fileHeron` / empty | Passkey RP id (= public hostname), name, allowed origins (empty → `APP_URL`). |
| `ANOMALY_ENABLED` | `true` | Hourly heuristic anomaly scan (alert-only). ↻ |
| `ANOMALY_MASS_DOWNLOAD_THRESHOLD` / `_MULTI_NETWORK_THRESHOLD` / `_LOGIN_FAILURE_THRESHOLD` | `100`/`4`/`50` | Anomaly thresholds. ↻ |
| `ERROR_ALERT_COOLDOWN_MINUTES` / `ERROR_ALERT_MAX_PER_HOUR` | `15`/`20` | Error-alert anti-flood. ↻ |
| `ERROR_LOG_RETENTION_DAYS` | `90` | Error-log prune window (`0` disables). ↻ |
| `AUDIT_LOG_RETENTION_DAYS` | `365` | `prune_history` audit window (`0` = forever). ↻ |
| `DOWNLOAD_LOG_RETENTION_DAYS` / `EMAIL_LOG_RETENTION_DAYS` / `LOGIN_ATTEMPT_RETENTION_DAYS` | `90`/`90`/`30` | Log prune windows (`0` disables). ↻ |
| `WEBHOOK_DELIVERY_RETENTION_DAYS` / `IMAP_MESSAGE_RETENTION_DAYS` | `30`/`90` | Webhook + inbound prune windows. ↻ |
| `NOTIFICATION_READ_RETENTION_DAYS` | `3` | Read in-app notification cleanup. ↻ |
| `QUARANTINE_PURGE_AFTER_DAYS` / `ORPHAN_RECLAIM_AFTER_DAYS` / `TUS_UPLOAD_ABANDONED_AFTER_HOURS` | `90`/`7`/`24` | Quarantine purge / orphan reclaim / abandoned-upload windows. ↻ |
| `REFRESH_TOKEN_RETENTION_DAYS` / `INVITE_RETENTION_DAYS` | `30`/`14` | Token + invite retention. ↻ |
| `SECRET_RETENTION_DAYS` | `90` | How long the record of an ended secret is kept (its text is gone at once; `0` keeps it). ↻ |
| `FH_TAG` | `latest` | GHCR image tag (the in-app updater rewrites it). |
| `UPDATER_HOST_WORKSPACE` / `UPDATER_HOST_STATE` | `${PWD}` / `${PWD}/data/updater` | Host paths the updater shim resolves. |
| `UPDATES_DRAIN_MAX_WAIT_MIN` | `30` | Max wait for transfers to drain before a postponed update applies. ↻ |
| `UPDATES_BACKUP_DEFAULT` | `true` | Whether the Update dialog's "Back up database first" box starts checked. ↻ |
| `UPDATES_BACKUP_ON_DB_CHANGE` | `true` | Back up before updating whenever the release changes the database service, checkbox or not. ↻ |
| `UPDATES_BACKUP_KEEP` / `UPDATES_BACKUP_MAX_AGE_DAYS` | `3` / `30` | Retention of `backups/pre-update/`, applied on each update (0 = no limit; the newest is always kept). ↻ |
| `UPDATES_INFRA_SYNC` | `true` | Let an update fast-forward the checkout and recreate changed db/redis/clamav/tusd. ↻ |
| `UPDATES_AUTO_ENABLED` / `UPDATES_AUTO_SCOPE` / `UPDATES_AUTO_MIN_AGE_HOURS` | `false` / `patch` / `24` | Automatic updates: on/off, which releases (`patch`, `minor`, `any`) and how long a release must be public first. Changed on Status & updates, which asks for the admin password. ↻ |
| `BACKUP_RESTIC_REPO` / `BACKUP_RESTIC_PASSWORD` | empty | Optional offsite restic push - read by the host `scripts/backup.sh`, not by the app. |
| `METRICS_BEARER_TOKEN` / `METRICS_ALLOWED_IPS` / `METRICS_CACHE_TTL_SEC` | empty/empty/`60` | `/api/metrics` auth + cache. |
| `OIDC_ALLOW_INSECURE_HTTP` | `false` | Disables HTTPS enforcement for OIDC discovery, JWKS **and the client-secret-bearing token exchange**. Only for a self-hosted IdP on a trusted private network with no TLS. |

SSO/OIDC providers are configured in the DB via `/admin/settings/sso`;
`OIDC_ALLOW_INSECURE_HTTP` above is the only OIDC env var, and it is env-only on
purpose so it cannot be flipped from a compromised admin session.

</details>

### 2. Admin runtime settings (hot - no restart)

Stored in `app_settings`; key list in `backend/app/services/settings.py::Keys`
(`smtp.password` + `imap.password` Fernet-encrypted). Each `/admin/settings/*` page is
described under [Policies & settings](admin-guide.md#policies--settings). The **Advanced** page is the
registry overlay for every `↻` env var above.

### 3. Per-user preferences (`/account`)

| Preference | Notes |
|---|---|
| UI language | `users.locale` (EN/DE); overrides browser language. |
| Default landing page | `home` / `outbox` / `inbox` / `new` / `account`. |
| Storage quota | `users.quota_bytes` (admin-set; NULL = unlimited). |
| Notification channels | Per category → `off` / `email` / `in_app` / `both`. **22 categories**: `share_created`, `share_files_added`, `share_expiring`, `share_pending_approval`, `share_approved`, `share_rejected`, `public_link_downloaded`, `secret_received`, `secret_viewed`, `secret_ended`, `secret_requested`, `secret_request_update`, `account_created`, `reset_password`*, `login_alert`*, `oidc_linked`, `file_quarantined`, `session_evicted`, plus admin-only `ops_alert`, `release_available`, `inbound_message`, `server_error`. (*locked on - can't be disabled.) |
| 2FA / recovery codes / passkeys | TOTP, 10 one-time recovery codes, WebAuthn credentials. |
| SSO connections / API tokens | Link/unlink OIDC; mint/scope/revoke your own tokens (if policy allows). |
