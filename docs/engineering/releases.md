# Release history: current state and per-tag migrations

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `RELEASE_NOTES.md`, `client/RELEASE_NOTES.md`, `backend/alembic/versions/`

## Current state

Backend **`v2.24.0`** is the newest tag (2026-10-02): secrets - a password or
short text readable a set number of times or until a date, then shredded - and
requesting one (`docs/engineering/secrets.md`); off by default, two migrations. Desktop client
**`client-v1.5.0`** ships beside it with a Secrets tab. **`v2.23.2`**
(2026-09-29): every step-up password
prompt is `StepUpDialog`, a popup (CLAUDE.md §Step-up re-auth). **`v2.23.1`** (same day):
every email's From was
RFC 5322 group syntax and none had a Date or Message-ID (`docs/engineering/mail.md`); nightly
backups are written owner-only (`backup.sh` under `umask 077`); pyjwt 2.14.0
(CVE-2026-102274) and one malformed key no longer fails a provider's whole JWKS;
the rest is code-scanning cleanup. **`v2.23.0`** (2026-09-28): a share's expiry clock and
its recipient mail start when its files can be downloaded; no share expires
during a live upload. **`v2.22.1`** (same day): public links are no
longer described as "shown once" - the share page always shows them again.
**`v2.22.0`** (same day): an address without an account no longer dead-ends the
new-share form, and the sender decides whether it is emailed the link.
**`v2.21.0`** (same day): the new-share form says
why it cannot be sent, recipients without an account (off by default, via the
emailed public link), and a public link counts as leaving the organisation for
share approval. **`v2.20.1`** (2026-09-26): the admin search's two
"Updates" results open Status & updates again. **`v2.20.0`** (same day):
automatic updates, off by default (`docs/engineering/self-update.md`). **`v2.19.1`/`v2.19.2`** (same day): an update keeps the
app down only while db/redis are recreated (clamav/tusd follow the verified
app), uvicorn's drain is bounded to 5s, and the updater-shim stops on SIGTERM.
**`v2.19.0`** (same day) is the release whose
updater backs up DB+Redis and syncs infra, and the one that moved MariaDB 11 ->
12.3, Redis 7 -> 8.10 and ClamAV 1.5.4 through it. Desktop client
**`client-v1.5.1`** (2026-10-02, a client-only patch: the share page shows its
public link, which it never did since v0.5.3) is current; **`client-v1.5.0`**
shipped beside v2.24.0 with the Secrets tab, which needs a v2.24.0 server -
everything else still works from v2.6.1.
**`v2.17.0` is a tag with NO images** (its release run failed the dependency
audit on three anyio CVEs; tags are immutable, so the same commits shipped as
v2.17.1 plus the anyio bump). **v2.17.1 shipped a sidebar showing nothing but
"Overview"** (a `v-if` on the Overview link captured the categories' `v-else`;
no test mounted AdminLayout); v2.17.2 is that one-line fix plus
`tests/components/AdminLayout.test.ts`. **The reference host runs v2.23.0**
(in-app update 2026-09-29 05:06 UTC from v2.22.0, 41 s to `healthy`, no
warnings, pre-update backup taken); v2.23.1's `backup.sh` is already live there
from the working tree. Its infra has been on `mariadb:12.3.3` /
`redis:8.10.2` / `clamav:1.5.4` since the v2.19.0 update (API down ~70 s). The
default moves of v2.16.0 and v2.19.0 are live there; v2.17.x, v2.18.0 and
v2.19.1 through v2.24.0 move no default.
`data/updater/rollback_target.json` holds the version BEFORE last, not the
running one. Images and working tree can diverge without any deploy - see `docs/engineering/deploy-and-backups.md` §Ops
on which half of a fix is live.

**Keep that line current on release.** It was once carried forward unread
through two releases; README's version badges read the git tags live, this line
does not.

### Migrations, host steps and default moves

A rollback past any migration below needs the
[[reference_rollback_migration_trap]] `alembic stamp` recovery. Neither a host
step nor a default move is a searchable file change, so this table is their only
record.

| tag | migration | host step | default move / breaking API |
|---|---|---|---|
| v2.2.0 | `202607300001` `files.av_unscanned` | ClamAV 1.5.3 + worker `/state` mount | - |
| v2.3.0 | - | - | new `public_links:read` scope: a `shares:read`-only token now gets **403** on `GET /api/shares/{id}/public-link`, deliberately - that route returns the decrypted plaintext link URL |
| v2.5.0 | - | `docker compose up -d redis backend worker` (redis maxmemory headroom; operator-only secrets dropped from the app containers) | - |
| v2.7.2 | `202607310001` `av_unscanned` backfill | - | - |
| v2.8.0 | - | - | `imap.require_known_sender` **ON** - an instance accepting mail from addresses with no user account must turn it off at `/admin/settings/imap` |
| v2.9.0 | `202608070001` per-file approval state + `shares.approval_was_required`; `202608070002` `users.sessions_invalidated_at` | - | **six endpoints now require the caller's own `password`** (CLAUDE.md §Roles, permissions & security patterns); every new column defaults permissive/NULL |
| v2.10.0 | `202608080001` `ip_blocks` | - | scan guard ships OFF, so the upgrade is behaviour-neutral |
| v2.12.0 | `202608150001` `files.last_progress_at` | **`docker compose up -d tusd`** - its command changed (`post-receive` enabled, `-progress-hooks-interval=30s`) | - |
| v2.12.1 | - | **re-copy `scripts/ops/*`** - `OnFailure=` moved from `[Service]` (where systemd ignores it) to `[Unit]`; the host units are COPIES, so the fix reaches nothing until re-copied | - |
| v2.16.0 | - | - | **TWO default moves.** `error_alert.source_worker` **ON**: failed scheduled tasks now email admins, where alerting was per-task and opt-in - an instance that wants silence must turn it off (the per-task `cron.<name>.alert_on_failure` still overrides either way). And `unsubscribe_token.DEFAULT_TTL_SEC` 180d → **30d**; tokens already minted keep their baked `exp` |
| v2.16.1 | - | - | - (the `--no-deps` fix rides `updater-executor:<target_tag>`, which the shim pulls per run, so it applies to the update that INSTALLS it, not the one after) |
| v2.17.1 / v2.17.2 | - | - | - (admin nav restructure, no URL changed; v2.17.2 = the sidebar hotfix; `PATCH /api/account/admin-nav-open` now accepts only the six new category keys, and the SPA is its only caller; anyio 4.14.1 → 4.14.2. v2.17.0 is a tag without images - its run failed `dependency-audit` before building anything) |
| v2.18.0 | `202609240001` `users.email` + `invite_tokens.email` → `utf8mb4_bin` (MariaDB; lowercases first) | - (`SETUP_TOKEN` matters only to a not-yet-set-up instance; install.sh writes it. The updater-shim healthcheck is in `docker-compose.yml`: the executor recreates the shim on its new image but from the HOST's compose file, so it appears only where the checkout has been pulled - on the reference host, whose checkout is `main`, the next in-app update brings it) | `POST /api/admin/system/update` refuses a target older than the running version (`409 DOWNGRADE_REFUSED`). A rollback across the migration is safe: `stamp` leaves the binary collation, which older code only compares more strictly |
| v2.19.0 | - | - when the updater can sync infra (it fast-forwards the checkout, backs up, and recreates db/redis/clamav: MariaDB 11 -> 12.3 with `MARIADB_AUTO_UPGRADE`, Redis 7 -> 8.10, ClamAV 1.5.4); where it SKIPS (dirty/diverged checkout, override file, `COMPOSE_FILE`, no git) the log names `git fetch --tags && git merge --ff-only v2.19.0 && docker compose up -d --no-deps db redis clamav tusd`. The update TO v2.19.0 runs the new executor from the OLD SPA/backend (no checkbox, no options): the executor defaults do the backup. An old `data/redis/.gitkeep` may stay (uid 999, unlink warning; harmless) | **TWO default moves**, both updater: `updates.infra_sync` ON (an update recreates changed infra) and `updates.backup_on_db_change` ON (+ `updates.backup_default` ON: the Update dialog's box starts checked from the next update on). A MariaDB major cannot be rolled back in place - Rollback returns the app only |
| v2.19.1 | - | - (a user `command:` override for the backend needs `--timeout-graceful-shutdown 5` added by hand; the update TO v2.19.1 still stops the unbounded v2.19.0 backend, up to Docker's 10s grace) | - |
| v2.19.2 | - | - (the update TO v2.19.2 still stops the old, untrapped shim: 10s once, after `healthy`) | - |
| v2.20.0 | - | - | - (automatic updates ship OFF. New `PUT /api/admin/settings/auto-update` is step-up gated when it turns them on or changes them while on; `pending_update.requested_by_id` may be `null`, with a new `origin`) |
| v2.20.1 | - | - | - |
| v2.21.0 | `202609280001` `share_external_recipients` | - | - (recipients without an account ship OFF: `share.external_recipients.enabled` / `.offer_invite`). **Behaviour change for approval scope `outbound_to_clients` only:** a share created with a public link is now HELD, and attaching a link later to a live share the policy would hold answers `409 APPROVAL_REQUIRED` for non-admins (an exempt approver's own share passes). New optional `recipients.emails` on `POST /api/shares`; `external_recipients` on the share payload; `can_share_external` / `offer_invite_on_external` on `/me` |
| v2.22.0 | `202609280002` `share_external_recipients.send_link` | - | - (the link mail to an address without an account is the sender's per-share `email_external_link`, default true, and no longer follows `notify_recipients`; `external_recipients_emailed` on the share payload) |
| v2.22.1 | - | - | - |
| v2.23.0 | `202609280003` `shares.expires_in_sec` + `pending_added_notice` + `upload_batch_done` | - | - (behaviour: the recipient mail - and the link mail to addresses without an account - now waits for the virus scan, not just the upload; a preset expiry counts from ready; `expire_files` never expires a share with a live upload. New optional `expires_in_sec` on `POST /api/shares`, on the share, list and public payloads; `expires_at` is null while it is set) |
| v2.23.1 | - | optional: `chmod 700 backups backups/20*/ && chmod 600 backups/20*/*` tightens backups taken before (new ones are owner-only; `backup.sh` reaches a host with its checkout, which the updater fast-forwards where it can) | - (behaviour: every mail's From is quoted, and it carries Date, Message-ID, Auto-Submitted and X-Auto-Response-Suppress; the `ops_alert` subject carries its reason) |
| v2.23.2 | - | - | - (SPA only: the eight step-up password fields are one popup; no API change) |
| v2.24.0 | `202610010001` five tables: `secrets`, `secret_recipients`, `secret_group_members`, `secret_user_states`, `secret_access_events`; `202610020001` secret requests: `secret_requests`, `secret_request_targets`, `secret_request_group_members`, and on `secrets` a NULLable `created_by_id` plus `request_id`, `is_answer`, `answered_by_email`, `req_*`, `has_request_passphrase` | - | - (secrets ship OFF: `secrets.enabled`. New `/api/secrets*`, `/api/secret-requests*`, `/api/public/secrets/{peek,reveal}`, `/api/public/secret-requests/{peek,answer}`, six `secrets:*` token scopes (`secrets:request` asks), `/me` gains `secrets_enabled`, `can_send_secrets`, `can_send_secrets_external`, `secret_limits`; config import now also burns every active secret. `PUBLIC_LINK_BASE_PATH` is no longer read from the environment - the SPA only ever served `/d`, so a different value had only produced dead links) |
| *next (untagged)* | `202610030001` encryption at rest: `files.enc_version` + `key_encrypted` + `release_verdict` (+ `ix_files_enc_state`), `inbound_attachments.enc_version` + `key_encrypted`, table `storage_purge_queue`; `downgrade()` REFUSES while any row is encrypted | - | - (encryption ships OFF: `storage.encrypt_at_rest`. New `GET/PUT /api/admin/settings/encryption` (PUT step-up gated BOTH ways), `POST .../retry-failed`; Rollback answers `409 ROLLBACK_BLOCKED_BY_ENCRYPTION` while encrypted files exist and the target predates the migration. Give this row its tag on release) |

Per-release admin-facing notes for v2.13.0 and newer are in `RELEASE_NOTES.md`,
one `# file:Heron vX.Y.Z` section each, newest first; older releases live in
`git log` and the published GitHub Releases.
