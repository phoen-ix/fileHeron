# Admin guide

Part of the [file:Heron](../README.md) documentation.

Audience: users with the **admin** role. Everything lives under `/admin`. The landing page
is an **Overview** (what needs attention, a search over every setting, all pages as cards);
the sidebar groups the pages by task: **People & access** · **Sharing & files** ·
**Email & notifications** · **Security & audit** · **Site & appearance** · **System**.
A policy and the state it produces share one page as tabs (Quarantine: Files | Alerts &
scanner; API tokens: Tokens | Policy; Secrets: Secrets | Policy; Blocked sources: Blocks &
allowlist | Auto-block rules (Scan guard); Errors & alerts: Log | Alerts); every tab keeps its
historical URL.

<img src="screenshots/admin-overview.png" alt="The admin overview: the task-grouped sidebar, the setting search, attention tiles, service status and the recently changed settings" width="800">

## User management

<img src="screenshots/admin-users.png" alt="The admin user list: employees and clients with role, status, 2FA, storage and last sign-in" width="800">

- **`/admin/users`** - paginated, role/status filter, search. **Invite** (one-time link, 24 h; optional initial groups; pre-flights `USER_EXISTS` / `INVITE_PENDING`) or **create directly** (set a password; account active immediately, for out-of-band hand-off).
- **`/admin/users/:id`** - edit name/role/quota (NULL = unlimited)/disabled, plus three irreversible actions: **Force password reset** (one-time token), **Erase user (GDPR)** (hard-deletes files and the secrets they sent, anonymises the row, audits `user_erased`; two-step with a pre-flight count), and **Erasure receipt PDF**. The page also shows the user's **sessions** and **current files** (with per-file delete) and authoritative **storage** figure.

## Groups

- **`/admin/groups`** + **`/admin/groups/:id`** - name, the **company-inbox** flag (addressable by every connected client), and members. Removing a member instantly revokes access to past group-targeted shares. Deletion refuses with `GROUP_IN_USE` (409) while the group is a recipient of an active share.

## Audit log (`/admin/audit-log`)

Filter by event type / target / time window, paginated newest-first; each row links the
actor and shows IP + `request_id` for log correlation. **Export CSV** streams the
current filter. See [audit events](#audit-events) for the full catalogue.

## File history (`/admin/file-history`)

Cross-user inventory of every file ever uploaded, joined with parent share + uploader +
download stats. Hides deleted/abandoned rows by default. Each live row has **Delete**
(hard-deletes bytes, frees quota, auto-revokes the parent share if it was the last live
file); orphans keep **Reclaim**.

## Sessions (`/admin/sessions`)

Every signed-in session (a live refresh-token row) across all users - searchable,
sortable by **Last active** (stale devices first). Revoke one session or all of a
user's; both audited.

## Quarantine (`/admin/quarantine`)

Files ClamAV flagged. Bytes stay under `./data/quarantine/{share_id}/{filename}`. Per
row: **Download** (forensics), **Release** (back to active, re-reserves quota,
conditionally restores the share), **Purge** (unlink bytes, keep the marker row). **Release**
requires a reason; Purge takes no body - you have already seen the file in the
list and confirmed the dialog, and the `file_quarantine_purged` audit row
records the actor and the file. Companion toggle at **`/admin/settings/quarantine`** fans an
infection alert out to all admins.

## Analytics (`/admin/analytics`)

A usage dashboard - active users, shares/files created, downloads, top senders, and a
**storage trend** fed by a tiny daily snapshot (`analytics_aggregate` cron). Charts are
hand-rolled SVG (no charting dependency).

## Error log & alerts (`/admin/error-log` + `/admin/settings/error-alerts`)

A browsable record of server-side errors, with optional email alerting - **logging and
alerting are independent**.

- **Error log** (`/admin/error-log`) - one row per captured error: HTTP **5xx**,
  opted-in **4xx**, and failed scheduled tasks. Columns include **client IP**, status,
  code, path, and an "emailed" flag. Filter by code / status / source / **IP** / time;
  open a row for full detail; **Export CSV**.
- **Errors & alerts** (`/admin/settings/error-alerts`) - the **log** switch (on by
  default for 5xx + cron failures), an opt-in **4xx capture** with a status allowlist
  (e.g. `429, 409`), retention, and the **email** side: master toggle, 5xx and (opt-in)
  4xx sources, recipients (all admins or a custom list), and anti-flood **cooldown +
  hourly cap**. Logging captures every qualifying error even when alert emails are
  deduped, capped, or off.
- **Spotting scans.** Because the SPA serves a `200` shell for unknown page paths,
  vuln-scanner probes (`/wp-login.php`, `/.env`, `/.git/config`, …) are routed at the
  edge (nginx) to the backend, which returns `404 NOT_FOUND`; with `404` in the 4xx
  allowlist they appear in the Error log with the source IP. A scan reads as a burst of
  bogus 404s from one IP. Real-browser hits on nonexistent *page* paths (which nginx
  answers `200` with the SPA shell) are additionally reported by the SPA itself via an
  anonymous beacon (`POST /api/telemetry/page-404`, opt-in with 4xx capture, per-IP
  rate-limited, query string stripped); those rows show `source: spa`. The per-minute
  4xx capture ceiling is tunable on Advanced (`error_log.scan_capture_per_min`) if a
  scan burst exceeds the default.

## Webhooks (`/admin/settings/webhooks`)

Register outbound webhook subscriptions (events such as share/file lifecycle) and
inspect the **delivery log** (status, retries). Deliveries are pruned by retention.

## Scheduled tasks (`/admin/scheduled-tasks`)

Every background cron with its live status; set each to *every N minutes* or *daily at
HH:MM* (site timezone) or disable it, and **Run now** on demand. Defaults reproduce the
historical cadence. See [ARQ workers + cron](developer-guide.md#arq-workers--cron).

## Scan guard (blocking probes and brute force)

Self-hosted instances get scanned constantly - requests for `/.env`,
`/.git/config`, `/wp-login.php` and similar, looking for leaked secrets. These
already 404 and land in the [error log](#error-log--alerts-adminerror-log--adminsettingserror-alerts); the scan guard is
the switch that makes them stop arriving.

**It ships off.** Nothing is ever blocked until an admin enables it at
*Settings -> Scan guard*, and every part of it is configurable there: which
signals count, how many offences and over what period, how long a block lasts,
whether repeat offenders are blocked for longer, and whether you are notified on
every block.

- **Probes for files that don't belong here** (on by default once enabled). One
  hit is enough - these paths have no legitimate use, and a scanner typically
  tries a hundred of them in seconds. Needs no list to maintain, though you can
  add your own patterns.
- **Repeated unknown API paths** (off). An expired share link also returns 404,
  so this one can reach a real recipient. Public share links are never counted
  regardless, and it only fires after many *different* paths.
- **Repeated sign-in failures** (off) - counted on sign-in endpoints only, and
  only when the failure means a *submitted secret was wrong*. The ordinary
  two-factor prompt, a disabled account and an expired browser session all
  produce the same status code and none of them count. It has its own, much
  higher threshold than the probe signals (15 by default), because legitimate
  people mistype passwords and a whole office can share one address - and a
  source is exempted outright when its own successful sign-ins, across more than
  one account, explain its failures. Account lockout and the per-address sign-in
  limit already cover ordinary password guessing; this is the switch for someone
  who keeps trying anyway.

**Safeguards, because blocking is the one thing here that denies service:**
signed-in users are never blocked; private, loopback and allowlisted addresses
are never blocked; there is no permanent block at any level, so every mistake
expires on its own; and blocked requests get an ordinary 404, so a scanner
learns nothing. Put your own office address in the allowlist.

**Blocking a whole network is off by default and worth thinking about before you
turn it on** - an IPv4 /24 is 256 addresses, which may be a customer's office, a
mobile carrier, or a mail-security gateway that fetches share links from many
addresses at once.

For IPv6, how much counts as "one network" is adjustable (*Settings → Scan
guard*), because IPv6 addresses are handed out very
differently depending on where they come from. **A shorter prefix is not simply
"stricter"** - it is a bet about who else is behind it. The default /64 is one
customer at most hosting providers. Widening to /56 or /48 is right for a range
you know belongs to a single operator, and wrong for a hosting provider, where
one /48 can be tens of thousands of unrelated customers renting servers. Some
providers put several customers inside a single /64, so even the default can
catch a neighbour. Widen it only when you know the answer for that range.
Changing this releases any network blocks currently in force, since they were
filed under the old grouping.

Everything the guard is currently *doing* lives on its own page, *Blocked
sources*: every block with its reason and expiry, filters for released and
expired history, a Release button, "Release + allow" for a source that should
never have been caught, blocking an address or range by hand, and the allowlist
itself. It also shows a **watchlist** of sources accruing offences that have not
been blocked yet, so you can see a scan building up - and allow or block it -
before it crosses a threshold. (The watchlist keeps those addresses in memory
for at most one counting window; turn it off under *Settings → Scan guard* if
you would rather it never held them at all.)

If a block ever locks *you* out - the check runs before sign-in, so you can't
reach the admin page to undo it - release it from the host:

```bash
docker compose exec backend python scripts/unblock_ip.py --list
docker compose exec backend python scripts/unblock_ip.py --all
docker compose exec backend python scripts/unblock_ip.py 203.0.113.4
```

Naming your own address is enough even when what caught you was a whole *range*:
the tool releases any block containing that address, not just one filed under
exactly that text.

## Policies & settings

Each policy editor follows the same shape - a **mode** (`everyone` /
`employees_admins` / `admins_only`) plus an additive user/group **allowlist**; admins
always pass.

| Page | Route | Controls |
|---|---|---|
| API token policy | `/admin/settings/api-tokens` | Who may mint API tokens (+ allowlist). Cross-user inventory at `/admin/api-tokens` (disable/revoke, generate-for-user, per-token scopes). |
| Public-link policy | `/admin/settings/public-links` | Who may mint public links. |
| Secrets | `/admin/settings/secrets` (the *Policy* tab of **Secrets**; the *Secrets* tab at `/admin/secrets` lists every secret's metadata, with **Burn now**, and the *Requests* tab at `/admin/secret-requests` every secret request, with **Cancel**) | The on/off switch (off by default; turning it off stops new secrets, those already sent stay readable until they end), who may send secrets (default everyone; a client reaches only connected employees), who may send them outside the organisation - to an address or as a link (default employees and admins; never a client), what wrong passphrases do (slow down and lock, or destroy after N), and the limits: most views (100), latest expiry (90 days), longest life (90 days, also ending secrets that have only a view limit; `0` = none) and the passphrase throttle - the same rules decide who may ask for a secret, and whom. Admins never see a secret's text or its links. |
| 2FA enforcement | `/admin/settings/twofa` | Which roles/groups must enrol TOTP (computed live; **no admin escape**). **Passkey sign-in** (off by default): a passkey alone signs in, and a passkey counts as 2FA - the desktop app cannot use passkeys yet, so people whose only second factor is a passkey sign in there with an API token. |
| SSO providers | `/admin/settings/sso` | Multi-provider OIDC CRUD (entra/google/authentik/keycloak/custom presets, smart-prefill, test-discovery). DELETE refused while users are bound. |
| SMTP / email | `/admin/settings/email` | Live SMTP override (DB beats env), HELO host, test-send with the error class/code surfaced. Password Fernet-encrypted, never echoed. |
| Inbound mail (IMAP) | `/admin/settings/imap` | Poll a mailbox into the admin **Inbox** (`/admin/inbox`); labels REPLY / BOUNCE / AUTO; attachments are ClamAV-scanned; reuses SMTP creds by default. Off by default. |
| Email templates | `/admin/settings/email-templates` | Per-(template, language) subject/body overrides in a **ProseMirror HTML** editor; placeholders, live preview, test-send, reset-to-default. Auth-link templates can't drop their required link. |
| Share approval | `/admin/settings/share-approval` | The four-eyes workflow (who approves, which shares, content review, self-approval). |
| Email-change policy | `/admin/settings/email-change` (the *Email change* tab of **Sign-in policies**) | Whether users may change their own sign-in email and the verification mode (`immediate` / `verify_new` / `verify_both`) + what happens to an OIDC binding on change. |
| Branding & legal | `/admin/settings/branding` · `/admin/settings/legal` | Two tabs. Branding: logo (magic-byte-validated; per-surface toggles), optional logo link, application name. Legal pages: the imprint/privacy pages (per-language ProseMirror, nh3-sanitised). |
| Site | `/admin/settings/general` | Site URL (overrides `APP_URL` for links) + IANA timezone (drives 24-h timestamps). A section of **General**, not its own route. |
| Quarantine alerts / Home / MOTD | the Quarantine page's *Alerts & scanner* tab (`/admin/settings/quarantine`), and sections of `/admin/settings/general` | Single-knob toggles. Home page and MOTD are sections of **General**. |
| Sign-in policies | `/admin/settings/sign-in` | Account lockout, per-address sign-in/registration limits, the HIBP breach check; *Email change* is its second tab. |
| Files & transfers | `/admin/settings/transfers` | Share defaults, the direct-upload size cap, signed-URL lifetime + resume credit, in-browser preview. |
| Anomaly detection | `/admin/settings/anomaly` | The four heuristic thresholds; advisory only. |
| Encryption at rest | `/admin/settings/encryption` | Store every file encrypted with a key only this instance holds (off by default; turning it on needs a key-custody confirmation and your password, turning it off your password). Status, the background task's last run, and files set aside after repeated failures. See [Encryption at rest](operator-guide.md#encryption-at-rest). |
| Self-update | `/admin/system` | Releases API URL (forks repoint it), the postponed-update drain wait and **automatic updates** (off by default; turning them on asks for your password), on **Status & updates**; the poll cadence lives on [Scheduled tasks](#scheduled-tasks-adminscheduled-tasks), not here. |
| Maintenance mode | `/admin/settings/maintenance` | Pause **new** transfers (in-progress + resumable ones finish); standalone or via drain-before-update. |
| Configuration backup | `/admin/settings/backup` | Export/import settings/branding/OIDC/webhooks/groups/users (+ optional logs) to one `*.fhbackup.json`; three secret modes (passphrase / ciphertext / exclude). Files and secrets excluded; import invalidates active shares, burns active secrets and revokes sessions. |
| Data retention & storage | `/admin/settings/advanced` | What remains of the **registry overlay** on this page: every retention window and the low-disk thresholds, editable **live, clamped to safe bounds**. The other registry groups render on the page of their task through the same endpoint (`frontend/src/config/adminTunablePlacement.ts`): session lifetimes on **Sessions › Policy** (`/admin/settings/sessions`), lockout + sign-in limits + HIBP on **Sign-in policies**, the public-link brute-force limits on **Public links**, the secret limits and passphrase throttle on **Secrets › Policy**, the upload cap and download tunables on **Files & transfers**, anomaly thresholds on **Anomaly detection**, the alert throttle on **Errors & alerts**, the drain wait on **Status & updates**, the app name on **Branding & legal**. |

**Anomaly detection** is heuristic and **alert-only** (it never blocks): an hourly cron
flags mass-download, multi-network access, and login-failure spikes against the
thresholds on `/admin/settings/anomaly`, dispatching an `ops_alert`.

## Audit events

150+ event types; the authoritative list is
`backend/app/models/audit_log.py::AuditEventType`.

<details>
<summary>Grouped catalogue</summary>

- **Auth / accounts:** `user_registered`, `user_created_by_admin`, `email_verified`, `login_success`, `login_failure`, `logout`, `account_locked`, `account_unlocked`, `rate_limited`, `password_changed`, `password_reset_requested`, `password_reset_consumed`, `totp_enabled`, `totp_disabled`, `recovery_code_used`, `role_changed`, `user_disabled`, `user_erased`, `admin_bootstrapped`.
- **Email-change:** `email_change_requested`, `email_changed`, `email_change_cancelled`, `email_change_policy_changed`.
- **Sessions / invites:** `refresh_token_rotated`, `refresh_token_reused`, `refresh_token_evicted`, `refresh_token_admin_revoked`, `invite_created`, `invite_consumed`, `invite_revoked`, `invite_purged`.
- **Shares / files:** `share_created`, `share_revoked`, `share_expired`, `share_expiry_updated`, `share_limit_updated`, `share_files_added`, `share_failed`, `share_submitted_for_approval`, `share_approved`, `share_rejected`, `share_resubmitted`, `file_finalized`, `file_downloaded`, `file_deleted`, `file_expired`, `file_upload_abandoned`, `file_quarantined`, `file_quarantine_released`, `file_quarantine_purged`, `av_reload_triggered`.
- **Encryption at rest:** `encryption_at_rest_changed`, `file_encryption_deferred` (stored unencrypted for now; the backfill retries), `file_encryption_failed` (set aside after repeated failures), `file_integrity_failed` (an encrypted file failed to authenticate while being read).
- **Public links / groups:** `public_link_created`, `public_link_revoked`, `public_link_consumed`, `group_created`, `group_updated`, `group_deleted`, `group_member_added`, `group_member_removed`.
- **Secrets:** `secret_created`, `secret_viewed`, `secret_burned` (every view used), `secret_expired`, `secret_revoked` (burned early by the sender, an admin or a config import), `secret_recipient_burned` (too many wrong passphrases), `secret_link_shown`, `secret_link_replaced`, `secret_link_removed`, `secret_policy_changed`, and for requests `secret_request_created`, `secret_request_answered`, `secret_request_cancelled`, `secret_request_expired`, `secret_request_link_shown`. Metadata only - never the text, a token or an address. `secret_created`, `secret_viewed`, `secret_burned`, `secret_request_created` and `secret_request_answered` are also webhook events.
- **API tokens / OIDC:** `api_token_created` / `_revoked` / `_disabled` / `_reactivated` / `_admin_revoked` / `_admin_created`, `oidc_linked`, `oidc_unlinked`, `oidc_provider_created` / `_updated` / `_deleted`.
- **Email / messaging:** `email_resent`, `email_undeliverable`, `email_template_changed`, `email_template_reset`, `smtp_config_changed`, `imap_config_changed`.
- **Settings / policy:** `api_policy_changed`, `public_link_policy_changed`, `twofa_policy_changed`, `quarantine_policy_changed`, `share_defaults_policy_changed`, `share_approval_policy_changed`, `home_page_toggled`, `file_preview_toggled`, `motd_changed`, `branding_changed`, `legal_changed`, `site_url_changed`, `site_timezone_changed`, `updates_settings_changed`, `error_alert_settings_changed`, `webhook_created` / `_updated` / `_deleted`, `settings_changed`.
- **Ops / self-update:** `cron_failed`, `cron_run_triggered`, `cron_schedule_changed`, `ops_alert_dispatched`, `anomaly_detected`, `config_backup_exported`, `config_backup_imported`, `maintenance_enabled`, `maintenance_disabled`, `update_triggered` / `_completed` / `_failed`, `update_postponed`, `update_postpone_cancelled`, `update_auto_scheduled`, `rollback_triggered` / `_completed` / `_failed`.

</details>
