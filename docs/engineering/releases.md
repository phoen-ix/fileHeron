# Release history: current state and per-tag migrations

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `RELEASE_NOTES.md`, `client/RELEASE_NOTES.md`, `backend/alembic/versions/`

## Current state

Backend **`v2.29.0`** is the newest tag (2026-10-30): the updater audit - every published
image is cosign-signed and the shim verifies the executor image (and the executor the app
images) before running anything, by digest; the shim refuses an update to an older version
than the daemon reports and binds a rollback to the version it recorded in the new root-owned
`data/updater-private/`; the executor's `/state` I/O is symlink-safe with no in-place
fallback and the `.env` rewrite is atomic; **every service is `cap_drop: [ALL]`** with a
measured per-service `cap_add` (reasons beside each in `docker-compose.yml`); the infra
images are pinned `tag@sha256` (`docs/engineering/self-update.md` §What the updater
trusts). No migration; one default move (signature verification ON, opt-out
`SHIM_REQUIRE_SIGNATURE=false`). The update recreates all four infra services (their
compose definition changed), db and redis with the app stopped and the forced backup.
**`v2.28.0`** (2026-10-23): Python 3.15.1 in the backend, worker and
updater-executor images (the backend image builds hiredis and PyYAML in a `deps` stage: no
cp315 wheels yet); no migration, no host step, no default move. Desktop client
**`client-v1.6.0`** is tagged beside it: Python 3.15 in the .exe, so it needs Windows 10 or
newer (`docs/engineering/desktop-client.md`).
**`v2.27.2`** (same day): a security patch - Pillow 13.0.0
(39 advisories) and certifi 2026.10.23 (emSign roots dropped) in the backend, images on
python 3.14.9, nginx 1.31.8, alpine 3.24.3; no migration, no host step, no default move.
**`v2.27.1`** (2026-10-17): a security patch - the frontend
image on `nginx:1.31.7-alpine3.24`, which fixes five nginx CVEs (plus vue-tsc 3.3.13 in
the dev tree); no migration, no host step, no default move.
**`v2.27.0`** (2026-10-11): passkey sign-in - a passkey alone
signs in, and counts as 2FA - behind one admin switch, off by default
(`auth.passkey_signin`, docs/engineering/auth.md §Passkey sign-in), one migration.
**`v2.26.0`** (same day): folder uploads - a file keeps the
folder it came from (`files.relative_dir`, one migration), the share and public pages
group by folder and the ZIP carries the tree (`LAYOUT_VERSION` 2) - plus a share label
that could name a file still pending review, and `/files-added` batches over 1,000.
**`v2.25.3`** (same day): a dependency refresh - the
backend lock and the frontend tree to current, the SPA built on Node 26, ruff 0.17.0,
and CI's linter images pinned where Dependabot looks (`.github/ci-tools/Dockerfile`).
Desktop client `client-v1.6.0` was prepared beside it on Python 3.14 and shipped with
v2.28.0 on Python 3.15. **`v2.25.2`**
(same day): the fixes of the 2026-10-10
audit - daily tasks run once on DST nights, IMAP Fetch now no longer 500s, a tab
from before an update reloads itself, mail survives an outage of an hour or
more, one IMAP blip no longer alerts, one mail per failed job, an answer to a
secret request can never become a link, the secret sweep reaches every active
secret, post-finish checks the tus file's size, clean DB connections on deploy -
and one default move, sessions ending 30 days after sign-in
(`auth.session_max_age_days`). **`v2.25.1`** (2026-10-02): `ops_check` retries a failed
ClamAV ping before alerting (a signature reload had mailed a false
`av_unhealthy` two days running), and the README's Architecture section is
current again. **`v2.25.0`** (same day): encryption at rest - every
stored file kept as ciphertext under a per-file key (`docs/engineering/storage-and-encryption.md`),
off by default, one migration - plus unlock-now, the pending email change on the
account page and the Overview's recent settings changes; tusd 2.10.1 rides the
infra sync. Desktop client **`client-v1.5.2`** ships beside it (outlined buttons
readable in light mode). **`v2.24.0`** (same day): secrets - a password or
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
public link, which it never did since v0.5.3) preceded 1.5.2; **`client-v1.5.0`**
shipped beside v2.24.0 with the Secrets tab, which needs a v2.24.0 server -
everything else still works from v2.6.1.
**`v2.17.0` is a tag with NO images** (its release run failed the dependency
audit on three anyio CVEs; tags are immutable, so the same commits shipped as
v2.17.1 plus the anyio bump). **v2.17.1 shipped a sidebar showing nothing but
"Overview"** (a `v-if` on the Overview link captured the categories' `v-else`;
no test mounted AdminLayout); v2.17.2 is that one-line fix plus
`tests/components/AdminLayout.test.ts`. **The reference host runs v2.25.2**
(the nightly automatic patch update, 2026-10-11 01:31 UTC from v2.25.1, no
migration; it skipped the infra sync because the checkout was ahead of the tag,
as designed; encryption at rest is off there). v2.26.0 and later are MINOR
releases, which the default patch scope never installs - they wait for a click on
Update. The working tree's `scripts/` are live there
as always. Its infra has been on `mariadb:12.3.3` /
`redis:8.10.2` / `clamav:1.5.4` since the v2.19.0 update (API down ~70 s); db and
clamav were recreated by hand on 2026-10-10 on Docker Hub's same-tag rebuilds, which
the updater never pulls (it compares tags, not digests). The
default moves of v2.16.0, v2.19.0 and v2.25.2 (sessions end 30 days after
sign-in) are live there; v2.17.x, v2.18.0 and v2.19.1 through v2.25.1 move no
default.
`data/updater/rollback_target.json` holds the version BEFORE last, not the
running one. Images and working tree can diverge without any deploy - see `docs/engineering/deploy-and-backups.md` §Ops
on which half of a fix is live.

**Keep that line current on release.** It was once carried forward unread
through two releases; README's version badges read the git tags live, this line
does not.

### Migrations, host steps and default moves

A rollback past any migration below needs an
`alembic stamp` recovery (`git log -S 'alembic stamp'` finds the write-ups). Neither a host
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
| v2.25.0 | `202610030001` encryption at rest: `files.enc_version` + `key_encrypted` + `release_verdict` (+ `ix_files_enc_state`), `inbound_attachments.enc_version` + `key_encrypted`, table `storage_purge_queue`; `downgrade()` REFUSES while any row is encrypted | - when the updater can sync infra (tusd v2.9.2 -> v2.10.1); where it SKIPS, `docker compose up -d --no-deps tusd` after the fast-forward the log names | - (encryption ships OFF: `storage.encrypt_at_rest`. New `GET/PUT /api/admin/settings/encryption` (PUT step-up gated BOTH ways), `POST .../retry-failed`, `POST /api/admin/users/{id}/unlock`, `GET /api/account/email`, `GET /api/admin/audit-log/settings-changes`; Rollback answers `409 ROLLBACK_BLOCKED_BY_ENCRYPTION` while encrypted files exist and the target predates the migration. Behaviour: an empty `FORWARDED_ALLOW_IPS=` now reaches uvicorn as `*`, not "" - it had trusted no proxy) |
| v2.25.1 | - | - | - (behaviour: `ops_check` alerts `av_unhealthy` only after 3 failed ClamAV pings over 20 s, not on one) |
| v2.25.2 | - | - | **`auth.session_max_age_days` = 30**: a session signed in more than 30 days ago ends at its first refresh after the update (browser and desktop client); `0` restores the old unlimited behaviour. Behaviour: the 02:xx daily tasks run once on DST nights; `send_email_job` retries for ~1 h 51 min (its own `max_tries`); `imap_poll` alerts only on an outage (15 min without a success) or a non-transient error; a cron failure mails an admin once; `run_poll`'s message count is `skipped_messages` |
| v2.25.3 | - | - | - (dependency refresh only: backend lock, frontend tree, the SPA built on Node 26; the backend image ships no curl - its compose healthcheck runs Python - and runs as `USER 1000:1000`; every Dockerfile hadolint-clean and gated at `style`. Desktop `client-v1.6.0`, prepared beside it and untagged until a Windows try-out, bundles Python 3.14 and needs **Windows 10 or newer**) |
| v2.26.0 | `202610100001` `files.relative_dir` (nullable) | - | - (behaviour: the bulk ZIP's `LAYOUT_VERSION` is 2, so a ZIP resume in flight across the update restarts once; a subject-less share's label never names a file pending review; the SPA sends `/files-added` in chunks of 1,000) |
| v2.27.0 | `202610110001` `user_webauthn_credentials.discoverable` (nullable) | - | - (passkey sign-in ships OFF: `auth.passkey_signin`, written through `PUT /api/admin/settings/twofa` - `passkey_signin`, omitted = unchanged. New `POST /api/auth/passkey/{begin,complete}` and `/api/auth/2fa/passkey/{begin,complete}`; `/api/config-public` gains `passkey_signin`, `WebAuthnCredentialItem` gains `discoverable`, the SSO second-factor redirect gains `&methods=`. Only with the switch ON: `POST /api/auth/login` answers `401 WEBAUTHN_REQUIRED` to an account with a passkey and no TOTP, and a passkey satisfies `is_2fa_required`) |
| v2.27.1 | - | - | - (security patch: the frontend image on nginx 1.31.7, which fixes five nginx CVEs) |
| v2.27.2 | - | - | - (security patch: Pillow 13.0.0 and certifi 2026.10.23 in the backend; images on python 3.14.9, nginx 1.31.8, alpine 3.24.3) |
| v2.28.0 | - | - | - (Python 3.15.1 in the backend, worker and updater-executor images; the backend image builds hiredis and PyYAML in a `deps` stage and its runtime stage stays compiler-free. Desktop `client-v1.6.0` tagged beside it: Python 3.15, Windows 10 or newer) |
| v2.29.0 | - | - where the updater fast-forwards the checkout (the executor recreates the shim with its new definition - the `/private` mount, `cap_drop`, `read_only`, `tmpfs` - at the end of the update). Where it SKIPS (dirty or diverged checkout, override file, `COMPOSE_FILE`, infra sync off): `git pull && docker compose up -d --no-deps updater-shim` afterwards; until then the shim verifies signatures but does not bind rollbacks, and says so once. The update recreates **all four infra services** - their compose definition changed (`cap_drop`/`cap_add`), and db, redis and clamav also run older builds than the pinned digests on most hosts - db and redis with the app stopped and the forced DB backup; the update TO v2.29.0 is supervised by the old shim (no verification) and runs the new executor (verifies the app images) | **image signature verification ON** (`SHIM_REQUIRE_SIGNATURE=false` is the fork opt-out; `SHIM_SIGNER_IDENTITY_RE` for a renamed repository). The shim refuses an update below the running version (`downgrade refused`) and a rollback to anything but the version it recorded in `data/updater-private/` (`rollback refused`; the first rollback after v2.29.0 has no record and is warned, not refused). Infra images pinned `tag@sha256` (a Dependabot digest bump is an infra recreate on the next update, like a tag bump). Shim `mem_limit` default 128m → 256m (cosign). **Every service drops all Linux capabilities** (`cap_drop: [ALL]`) and adds back the measured minimum of its image's root phase: db `CHOWN, DAC_READ_SEARCH, SETGID, SETUID`; redis those plus `FOWNER`; clamav `CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID`; frontend `CHOWN, NET_BIND_SERVICE, SETGID, SETUID`; backend/worker/tusd nothing. `GET /api/admin/system/update-jobs/{id}` caps `log_tail` at 200 × 2,000 chars and the free-text fields at 500 |

Per-release admin-facing notes for v2.13.0 and newer are in `RELEASE_NOTES.md`,
one `# file:Heron vX.Y.Z` section each, newest first; older releases live in
`git log` and the published GitHub Releases.
