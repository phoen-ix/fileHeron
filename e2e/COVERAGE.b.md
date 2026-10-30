# E2E coverage, part b: the admin console (`/admin/**`)

Part a (`COVERAGE.md`) owns everything outside `/admin` and the index; this file
maps every admin route and every admin feature to a test in `e2e/tests/admin/`.

- **Extraction** (2026-10-30, unchanged since 2026-10-24): `grep -oE "name: 'admin-[a-z0-9-]+'" frontend/src/router/index.ts | sort -u`
  → 49 route names (48 pages - tab leaves included - plus the `admin-settings`
  redirect), and the unnamed redirect `settings/home-page`; the
  features per page are the controls of `frontend/src/views/Admin*.vue` and
  `frontend/src/components/admin/*.vue` (`grep -nE '@click|@submit|<input|<select|<textarea'`).
- **Items:** 177 rows - 51 routes (each checked as admin, client, employee and guest:
  204 route × role checks), 8 role-level checks, 118 features / actions / dialogs /
  error states. ✅ 169 · ❌ 8 (b: 3, c: 5; no a, no d, no e).
- **Run:** `npx playwright test tests/admin` against a stack from `scripts/e2e.sh`
  (part a); `tests/admin/_admin.ts` holds the shared helpers.

Reasons for ❌: (a) needs hardware or an external account and no simulator exists,
(b) irreversible external effect with no sink, (c) covered at another layer,
(d) cannot be driven, (e) time-box overrun.

## Routes × roles (breadth)

Every route below is visited by the parameterised sweep (`sweep.spec.ts`):

| Role | What is asserted | Test |
|---|---|---|
| admin | each page renders its own `<h1>`, no error notice, the sidebar marks the page, no `pageerror`, no 5xx | `sweep.spec.ts › as the admin › <route name> renders its heading` (one per route) |
| client | every admin URL bounces to the client's landing page, no sidebar | `sweep.spec.ts › as a client › every admin route bounces a client to their landing page` |
| employee | same as client | `sweep.spec.ts › as a employee › every admin route bounces a employee to their landing page` |
| guest | every admin URL goes to `/login?redirect=<the URL>` | `sweep.spec.ts › as a guest › every admin route sends a guest to /login with the target kept` |
| guest → admin | signing in lands on the admin page asked for | `sweep.spec.ts › as a guest › after signing in, the guest lands on the admin page they asked for` |
| client / employee (API) | every console collection answers 403 | `sweep.spec.ts › as a client › the admin API answers 403 to a client`, `sweep.spec.ts › as a employee › the admin API answers 403 to a employee` |
| guest (API) | every console collection answers 401 | `sweep.spec.ts › as a guest › the admin API answers 401 to a guest` |
| client / employee | no Admin link in the app | `sweep.spec.ts › as a client › the sidebar shows no admin link to a client`, `sweep.spec.ts › as a employee › the sidebar shows no admin link to a employee` |

| # | Route (name) | Path | Admin render ✅ |
|---|---|---|---|
| 1 | admin-overview | `/admin` | `sweep.spec.ts › as the admin › admin-overview renders its heading` |
| 2 | admin-users | `/admin/users` | `sweep.spec.ts › as the admin › admin-users renders its heading` |
| 3 | admin-user-detail | `/admin/users/:id` | `sweep.spec.ts › as the admin › admin-user-detail renders its heading` |
| 4 | admin-groups | `/admin/groups` | `sweep.spec.ts › as the admin › admin-groups renders its heading` |
| 5 | admin-group-detail | `/admin/groups/:id` | `sweep.spec.ts › as the admin › admin-group-detail renders its heading` |
| 6 | admin-audit | `/admin/audit-log` | `sweep.spec.ts › as the admin › admin-audit renders its heading` |
| 7 | admin-analytics | `/admin/analytics` | `sweep.spec.ts › as the admin › admin-analytics renders its heading` |
| 8 | admin-settings-webhooks | `/admin/settings/webhooks` | `sweep.spec.ts › as the admin › admin-settings-webhooks renders its heading` |
| 9 | admin-mail-log | `/admin/mail-log` | `sweep.spec.ts › as the admin › admin-mail-log renders its heading` |
| 10 | admin-mail-detail | `/admin/mail-log/:id` | `sweep.spec.ts › as the admin › admin-mail-detail renders its heading` |
| 11 | admin-error-log | `/admin/error-log` | `sweep.spec.ts › as the admin › admin-error-log renders its heading` |
| 12 | admin-settings-error-alerts | `/admin/settings/error-alerts` | `sweep.spec.ts › as the admin › admin-settings-error-alerts renders its heading` |
| 13 | admin-inbox | `/admin/inbox` | `sweep.spec.ts › as the admin › admin-inbox renders its heading` |
| 14 | admin-inbox-detail | `/admin/inbox/:id` | `sweep.spec.ts › as the admin › admin-inbox-detail renders its heading` |
| 15 | admin-file-history | `/admin/file-history` | `sweep.spec.ts › as the admin › admin-file-history renders its heading` |
| 16 | admin-sessions | `/admin/sessions` | `sweep.spec.ts › as the admin › admin-sessions renders its heading` |
| 17 | admin-settings-sessions | `/admin/settings/sessions` | `sweep.spec.ts › as the admin › admin-settings-sessions renders its heading` |
| 18 | admin-quarantine | `/admin/quarantine` | `sweep.spec.ts › as the admin › admin-quarantine renders its heading` |
| 19 | admin-settings-quarantine | `/admin/settings/quarantine` | `sweep.spec.ts › as the admin › admin-settings-quarantine renders its heading` |
| 20 | admin-api-tokens | `/admin/api-tokens` | `sweep.spec.ts › as the admin › admin-api-tokens renders its heading` |
| 21 | admin-settings-api-tokens | `/admin/settings/api-tokens` | `sweep.spec.ts › as the admin › admin-settings-api-tokens renders its heading` |
| 22 | admin-settings-sso | `/admin/settings/sso` | `sweep.spec.ts › as the admin › admin-settings-sso renders its heading` |
| 23 | admin-settings-sso-new | `/admin/settings/sso/new` | `sweep.spec.ts › as the admin › admin-settings-sso-new renders its heading` |
| 24 | admin-settings-sso-edit | `/admin/settings/sso/:id` | `sweep.spec.ts › as the admin › admin-settings-sso-edit renders its heading` |
| 25 | admin-secrets | `/admin/secrets` | `sweep.spec.ts › as the admin › admin-secrets renders its heading` |
| 26 | admin-secret-requests | `/admin/secret-requests` | `sweep.spec.ts › as the admin › admin-secret-requests renders its heading` |
| 27 | admin-settings-secrets | `/admin/settings/secrets` | `sweep.spec.ts › as the admin › admin-settings-secrets renders its heading` |
| 28 | admin-settings-public-links | `/admin/settings/public-links` | `sweep.spec.ts › as the admin › admin-settings-public-links renders its heading` |
| 29 | admin-settings-transfers | `/admin/settings/transfers` | `sweep.spec.ts › as the admin › admin-settings-transfers renders its heading` |
| 30 | admin-settings-anomaly | `/admin/settings/anomaly` | `sweep.spec.ts › as the admin › admin-settings-anomaly renders its heading` |
| 31 | admin-settings-encryption | `/admin/settings/encryption` | `sweep.spec.ts › as the admin › admin-settings-encryption renders its heading` |
| 32 | admin-settings-share-approval | `/admin/settings/share-approval` | `sweep.spec.ts › as the admin › admin-settings-share-approval renders its heading` |
| 33 | admin-settings-email | `/admin/settings/email` | `sweep.spec.ts › as the admin › admin-settings-email renders its heading` |
| 34 | admin-settings-email-templates | `/admin/settings/email-templates` | `sweep.spec.ts › as the admin › admin-settings-email-templates renders its heading` |
| 35 | admin-settings-imap | `/admin/settings/imap` | `sweep.spec.ts › as the admin › admin-settings-imap renders its heading` |
| 36 | admin-settings-general | `/admin/settings/general` | `sweep.spec.ts › as the admin › admin-settings-general renders its heading` |
| 37 | admin-settings-branding | `/admin/settings/branding` | `sweep.spec.ts › as the admin › admin-settings-branding renders its heading` |
| 38 | admin-settings-legal | `/admin/settings/legal` | `sweep.spec.ts › as the admin › admin-settings-legal renders its heading` |
| 39 | admin-settings-twofa | `/admin/settings/twofa` | `sweep.spec.ts › as the admin › admin-settings-twofa renders its heading` |
| 40 | admin-settings-sign-in | `/admin/settings/sign-in` | `sweep.spec.ts › as the admin › admin-settings-sign-in renders its heading` |
| 41 | admin-settings-email-change | `/admin/settings/email-change` | `sweep.spec.ts › as the admin › admin-settings-email-change renders its heading` |
| 42 | admin-ip-blocks | `/admin/ip-blocks` | `sweep.spec.ts › as the admin › admin-ip-blocks renders its heading` |
| 43 | admin-settings-scan-guard | `/admin/settings/scan-guard` | `sweep.spec.ts › as the admin › admin-settings-scan-guard renders its heading` |
| 44 | admin-settings-advanced | `/admin/settings/advanced` | `sweep.spec.ts › as the admin › admin-settings-advanced renders its heading` |
| 45 | admin-settings-backup | `/admin/settings/backup` | `sweep.spec.ts › as the admin › admin-settings-backup renders its heading` |
| 46 | admin-settings-maintenance | `/admin/settings/maintenance` | `sweep.spec.ts › as the admin › admin-settings-maintenance renders its heading` |
| 47 | admin-system | `/admin/system` | `sweep.spec.ts › as the admin › admin-system renders its heading` |
| 48 | admin-scheduled-tasks | `/admin/scheduled-tasks` | `sweep.spec.ts › as the admin › admin-scheduled-tasks renders its heading` |
| 49 | admin-settings (redirect) | `/admin/settings` → General | `sweep.spec.ts › as the admin › /admin/settings redirects to General` |
| 50 | (redirect) | `/admin/settings/home-page` → General `#home-page` | `sweep.spec.ts › as the admin › /admin/settings/home-page lands on the home-page section of General` |
| 51 | 404 / unknown ids | `/admin/no-such-page`; `/admin/{users,groups,mail-log,inbox}/99999999` | `sweep.spec.ts › as the admin › an unknown admin path shows the not-found page`, `sweep.spec.ts › as the admin › detail pages for ids that do not exist show an error, not a crash` |

## Features (depth)

### Console: overview, sidebar, tabs, breadcrumbs, analytics — `console.spec.ts`

| Feature | Test |
|---|---|
| Overview setting search: no match, results, keyboard, jump | ✅ `console.spec.ts › overview › the setting search finds a setting by keyword and jumps to it` |
| Overview attention tiles link to their pages | ✅ `console.spec.ts › overview › tiles, status and every page card lead to their pages` |
| Overview status line links to Status & updates | ✅ same test |
| Overview "All pages" cards (every link is an admin page) | ✅ same test |
| Overview "Recently changed settings" + "Full audit log" | ✅ `console.spec.ts › overview › a settings change shows up under "Recently changed settings"` |
| Sidebar category open/close, remembered per account | ✅ `console.spec.ts › sidebar, tabs and breadcrumbs › a category closes and opens, and the choice survives a reload` |
| Sidebar link navigates and is marked current | ✅ `console.spec.ts › sidebar, tabs and breadcrumbs › a sidebar link navigates and marks itself current` |
| Tab bar of a tab page (AdminTabs), keyboard | ✅ `console.spec.ts › sidebar, tabs and breadcrumbs › the tabs of a tab page switch pages; breadcrumbs lead back` |
| Breadcrumbs lead back | ✅ same test |
| Analytics ranges, every chart card, CSV export | ✅ `console.spec.ts › analytics › each range loads; the CSV export carries the range` |
| Analytics quota warnings card | ✅ `console.spec.ts › analytics › a user over 90% of their quota is listed under Quota warnings` |

### People › Users — `users.spec.ts`

| Feature | Test |
|---|---|
| Invite by email (form, role, disabled submit) → pending list + mail | ✅ `users.spec.ts › invites › an admin invites a user by email and the invite is listed as pending` |
| Invite: existing address refused (error state) | ✅ `users.spec.ts › invites › inviting an address that already has an account is refused` |
| Invite form cancel | ✅ `users.spec.ts › invites › cancel closes the invite form without inviting` |
| Pending invite: Details dialog | ✅ `users.spec.ts › invites › details, new link, resend, activate and delete on a pending invite` |
| Pending invite: New link (confirm, link shown, Copy, Done) | ✅ same test |
| Pending invite: Resend by email | ✅ same test |
| Pending invite: Activate dialog | ✅ same test |
| Pending invite: Delete dialog | ✅ same test |
| Create account directly with a password (min length) | ✅ `users.spec.ts › invites › an admin creates an account directly with a password` |
| Invite with initial groups (listed in Details) | ✅ `users.spec.ts › invites › an admin invites a user by email and the invite is listed as pending` |
| Search, role filter, empty state, row opens the user | ✅ `users.spec.ts › search and the role filter narrow the user list; a row opens the user` |
| User page: name, role, quota, disable (and a disabled account cannot sign in) | ✅ `users.spec.ts › a user page › profile: name, role, quota and disabling are saved` |
| User page: lockout pill + Unlock (the locking sign-in answers promptly and mails the owner) | ✅ `users.spec.ts › a user page › a locked-out account shows the lock and the admin lifts it` |
| User page: Force password reset → token | ✅ `users.spec.ts › a user page › force password reset shows a one-time reset token` |
| User page: Change email (immediate / confirmation link) | ✅ `users.spec.ts › a user page › change email: applied at once, or as a confirmation link` |
| User page: sessions — revoke one, revoke all | ✅ `users.spec.ts › a user page › sessions: one device revoked, then all` |
| User page: files — delete, "View in File History" | ✅ `users.spec.ts › a user page › files: listed, deleted, and linked to File history` |
| User page: emails — open one, "View in Mail log" | ✅ `users.spec.ts › a user page › emails to the user are listed and open the mail` |
| User page: erase (two steps, preflight, wrong password, receipt PDF) | ✅ `users.spec.ts › a user page › erase: two confirmations, a password, and the account is anonymised` |

### People › Groups — `groups.spec.ts`

| Feature | Test |
|---|---|
| Create (name, description, company inbox), list, open | ✅ `groups.spec.ts › create a group from the list, then open it` |
| Create form cancel | ✅ `groups.spec.ts › cancel closes the create form` |
| Edit name/description; add a member (search); remove a member (confirm) | ✅ `groups.spec.ts › edit a group, add and remove members` |
| Delete group; refused while an active share targets it (error state) | ✅ `groups.spec.ts › delete a group; a group an active share targets cannot be deleted` |

### People › Sessions, API tokens — `sessions-tokens.spec.ts`, `tunables.spec.ts`

| Feature | Test |
|---|---|
| Sessions: search, sort ×3, revoke, revoke all, include inactive | ✅ `sessions-tokens.spec.ts › sessions › search, sort, revoke one, revoke all, include the inactive` |
| Sessions › Policy tab (tunables) | ✅ `tunables.spec.ts › admin-settings-sessions: auth.session_max_age_days is saved, kept and reset` |
| API tokens: mint for a user (search, scopes, step-up incl. wrong password, plaintext once, copy) and it authenticates | ✅ `sessions-tokens.spec.ts › API tokens › mint a token for a user behind the password; it works` |
| API tokens: disable, reactivate, revoke; status filter; search | ✅ `sessions-tokens.spec.ts › API tokens › disable, reactivate and revoke; the status filter follows` |
| API tokens › Policy tab (PolicyGate: mode, allowlist add/remove) and its effect | ✅ `sessions-tokens.spec.ts › API tokens › the policy decides who may mint tokens; the allowlist overrides it` |

### People › SSO, 2FA, Sign-in — `sso.spec.ts`, `policies.spec.ts`, `tunables.spec.ts`

| Feature | Test |
|---|---|
| SSO: add provider, list, edit, enable (button on the sign-in page), delete | ✅ `sso.spec.ts › add, list, edit, test and delete an SSO provider` |
| SSO: "Test connection" failure reported | ✅ same test |
| SSO: "Test connection" success | ❌ (c) `backend/tests/test_oidc_probe_issuer_mismatch.py::test_probe_tolerates_exactly_one_trailing_slash` (the probe answers `ok: true`); the e2e stack's stand-in IdP is part a's (`e2e/oidc/`) |
| SSO: sign-in through the provider | ✅ part a: `user/sso.spec.ts › the first SSO sign-in links the account with that verified email` (COVERAGE.a.md B10, against the stand-in IdP) |
| SSO: client secret required on create; Cancel | ✅ `sso.spec.ts › a new provider needs a client secret; cancel goes back to the list` |
| SSO: preset issuer template fields | ✅ `sso.spec.ts › a preset with an issuer template fills the issuer from its fields` |
| 2FA enforcement: required role → user must enrol; passkey sign-in switch → offered on sign-in | ✅ `policies.spec.ts › 2FA enforcement: a required role is enforced; passkey sign-in switches` |
| 2FA enforcement: required groups → a member must enrol | ✅ `policies.spec.ts › 2FA enforcement: a required role is enforced; passkey sign-in switches` |
| Sign-in policies (lockout, rate limits, HIBP tunables) | ✅ `tunables.spec.ts › admin-settings-sign-in: rate_limit.lockout_duration_min is saved, kept and reset` |
| Sign-in › Email change tab | ✅ `policies.spec.ts › email change: verification, SSO handling and self-service are saved` |

### Sharing & files — `logs.spec.ts`, `quarantine.spec.ts`, `policies.spec.ts`, `secrets-admin.spec.ts`, `tunables.spec.ts`

| Feature | Test |
|---|---|
| File history: search, file-state + share-state filters, sort, delete, show deleted | ✅ `logs.spec.ts › file history › search, state filters, sort and delete` |
| File history: orphaned flag + filter, Reclaim, uploader-filter chip | ✅ `logs.spec.ts › file history › an orphaned file is flagged and reclaimed; the uploader filter clears` |
| Quarantine: list, search, download for review, release with reason (validation, cancel) | ✅ `quarantine.spec.ts › an infected file is listed, downloaded for review and released` |
| Quarantine: purge; history keeps the row | ✅ `quarantine.spec.ts › an infected file is purged; file history keeps the record` |
| Quarantine link from the uploader's page | ✅ `quarantine.spec.ts › the uploader page links a quarantined file to the quarantine` |
| Quarantine › Alerts & scanner tab: admin fan-out, AV status panel | ✅ `policies.spec.ts › quarantine alerts: the admin fan-out is saved; the AV panel reports dev mode` |
| AV panel: "Reload signatures" against a running clamd | ❌ (c) the e2e stack runs AV_SKIP (no clamd); `backend/tests/test_quarantine_admin.py` |
| Share approval: enable, approvers, scope, content review → a new share waits | ✅ `policies.spec.ts › share approval: on, every share waits for an approver` |
| Share approval: inert-combination warning | ✅ `policies.spec.ts › share approval: the inert combination is called out` |
| Public links: mode + allowlist and its effect; outside recipients + offer invite | ✅ `policies.spec.ts › public links: admins only, with an allowlisted employee; outside recipients on` |
| Public links: brute-force tunables | ✅ `tunables.spec.ts › admin-settings-public-links: public_link.lockout_sec is saved, kept and reset` |
| Secrets: list, search, state filter, burn (never shows the text) | ✅ `secrets-admin.spec.ts › every secret is listed; the admin filters and burns one, without reading it` |
| Secret requests: list, search, cancel | ✅ `secrets-admin.spec.ts › every secret request is listed; the admin cancels one` |
| Secrets › Policy: on/off, failure mode | ✅ `policies.spec.ts › secrets policy: switched on and off, failure mode saved` |
| Secrets › Policy: limits (tunables) | ✅ `tunables.spec.ts › admin-settings-secrets: secrets.max_views is saved, kept and reset` |
| Files & transfers: share defaults, file preview | ✅ `policies.spec.ts › files & transfers: share defaults and file preview are saved` |
| Files & transfers: upload/download tunables | ✅ `tunables.spec.ts › admin-settings-transfers: downloads.resume_credit_hours is saved, kept and reset` |
| Analytics | ✅ see Console |

### Email & notifications — `mail.spec.ts`, `logs.spec.ts`, `webhooks.spec.ts`

| Feature | Test |
|---|---|
| Inbox: unread badge, search, open (keyboard), plain/HTML, attachment download, archive, delete | ✅ `mail.spec.ts › admin inbox › list, search, open, read the attachment, archive, delete` |
| Inbox: type + status filters | ✅ `mail.spec.ts › admin inbox › the type and status filters narrow the list` |
| Inbox: Fetch now | ✅ `mail.spec.ts › admin inbox › fetch now reports that inbound mail is off` |
| Inbox: fetching real mail over IMAP | ❌ (c) no IMAP server in the e2e stack; the poll is `backend/tests/test_imap_poll_resilience.py`, `test_imap_poll_outage.py`, the ingest `test_inbound_mail.py` |
| IMAP settings: save, test connection (failure), fetch now (failure) | ✅ `mail.spec.ts › inbound mail (IMAP) › settings are saved; test and fetch report the unreachable server` |
| Mail log: search, recipient, category, status filters; recipient + subject links; redacted mail cannot be resent | ✅ `logs.spec.ts › mail log › search, recipient, category and status filters; a mail opens` |
| Mail log: user-scope chip, CSV export | ✅ `logs.spec.ts › mail log › the user-scope chip clears the recipient filter; search finds by subject` |
| Mail detail: HTML version, sandboxed | ✅ `logs.spec.ts › mail log › a mail with an HTML body shows it sandboxed` |
| Mail detail: Resend of a deliverable mail | ❌ (b) only mail sent over SMTP can be resent and the e2e stack has no mail sink; `backend/tests/test_mail_log*.py` |
| SMTP settings: save/clear, anonymous vs credentials gate, test mail failure | ✅ `mail.spec.ts › outgoing mail (SMTP) › settings are saved and cleared again; credentials or "anonymous" are required` |
| SMTP: a test mail that is delivered | ❌ (b) no SMTP sink in the e2e stack; `backend/tests/test_admin_email_test_send.py` |
| Email templates: pick, language tabs, subject, placeholder insert, save, preview (HTML/text), effect on a real mail, test send, reset | ✅ `mail.spec.ts › email templates › edit a template per language, preview it, reset it to the default` |
| Email templates: unsaved-changes guard | ✅ `mail.spec.ts › email templates › switching away from unsaved changes asks first` |
| Webhooks: create (validation, select all), secret once + copy, test, deliveries (204), a real event | ✅ `webhooks.spec.ts › create a webhook, test it, and see the delivery succeed` |
| Webhooks: failed delivery, retry, disable/enable, delete | ✅ `webhooks.spec.ts › a failing delivery is shown and can be retried; disable, enable, delete` |
| Webhooks: loopback target refused | ✅ `webhooks.spec.ts › a loopback address is refused as a webhook target` |

### Security & audit — `ip-blocks.spec.ts`, `policies.spec.ts`, `logs.spec.ts`, `system.spec.ts`

| Feature | Test |
|---|---|
| Blocked sources: block an address (confirm), search, origin filter, release, status filter | ✅ `ip-blocks.spec.ts › block an address by hand, find it, release it` |
| Blocked sources: block a range, "Ranges only", release + allow, allowlist remove | ✅ `ip-blocks.spec.ts › block a range, release + allow it, then remove it from the allowlist` |
| Allowlist add; non-global address refused | ✅ `ip-blocks.spec.ts › the allowlist takes an entry directly; a non-global address cannot be blocked` |
| Release all; "Show all" | ✅ `ip-blocks.spec.ts › release all lifts every block in force` |
| Watchlist empty/disabled state | ✅ `ip-blocks.spec.ts › the watchlist explains why it is empty` |
| Watchlist rows, "Block now" from the watchlist, automatic blocks | ❌ (c) needs offences from a globally routable address, which never reaches the e2e stack (every client is a private Docker address and is never counted); `backend/tests/test_scan_guard.py`, `test_scan_guard_middleware.py` |
| Scan guard: enable, thresholds, contradiction refused, notify mode, link to blocks | ✅ `policies.spec.ts › scan guard: switched on with its thresholds, a contradiction is refused` |
| Anomaly detection (switch + thresholds) | ✅ `tunables.spec.ts › admin-settings-anomaly: anomaly.multi_network_threshold is saved, kept and reset`, `tunables.spec.ts › admin-settings-anomaly (switch): anomaly.enabled is saved, kept and reset` |
| Encryption at rest: turn on (ack + password), files encrypted, turn off | ✅ part a `encryption-at-rest.spec.ts › encryption at rest › the admin turns it on: acknowledgement first, then the password` |
| Encryption at rest: status, counts, custody gate, cancel | ✅ `system.spec.ts › encryption at rest (page) › state, counts and the custody acknowledgement gate are shown` |
| Encryption at rest: "Retry" deferred files | ❌ (c) needs a failed encryption run; `backend/tests/test_encrypt_release_lane.py` |
| Audit log: event/target filters, actor link, details, CSV export | ✅ `logs.spec.ts › audit log › filters by event and target, shows a row in full, exports CSV` |
| Audit log: date range | ✅ `logs.spec.ts › audit log › the date range narrows the list` |

### Site & appearance — `site.spec.ts`, `tunables.spec.ts`

| Feature | Test |
|---|---|
| General: site URL override + revert | ✅ `site.spec.ts › site URL: an override is saved and reverted to the env value` |
| General: timezone (invalid refused, "Use this") | ✅ `site.spec.ts › site timezone: a zone is saved, a bad one cannot be, the browser zone is offered` |
| General: home page switch and its redirect | ✅ `site.spec.ts › home page: switched off, / sends a user onward; switched on again` |
| General: login banner (MOTD) on the sign-in page | ✅ `site.spec.ts › login banner: shown on the sign-in page while on, gone when off` |
| Branding: logo upload (bad file refused), surfaces, link, remove | ✅ `site.spec.ts › branding: a logo is uploaded, shown where enabled, linked, and removed` |
| Branding: application name (tunable) | ✅ `tunables.spec.ts › admin-settings-branding: branding.app_name is saved, kept and reset` |
| Legal pages: EN + DE editors, footer link, public page, switch off | ✅ `site.spec.ts › legal pages: written in both languages, linked in the footer, then switched off` |

### System — `system.spec.ts`, `policies.spec.ts`, `logs.spec.ts`, `tunables.spec.ts`

| Feature | Test |
|---|---|
| Status: version, refresh, live checks re-run, counters | ✅ `system.spec.ts › status & updates › version, refresh, live checks and the counters render` |
| Release check "Check now" + update source | ✅ `system.spec.ts › status & updates › the update source is saved; a check against it reports the failure` |
| Automatic updates (step-up incl. wrong password; off without one) | ✅ `system.spec.ts › status & updates › automatic updates are switched on only with the password` |
| Update tunables on the Status page | ✅ `tunables.spec.ts › admin-system: updates.backup_keep is saved, kept and reset` |
| Update / postpone / force / cancel pending / rollback dialogs | ❌ (b) replaces the running stack through the updater shim and GHCR images; `backend/tests/test_release_apply.py`, `test_pending_update_recovery.py`, `backend/tests/infra/test_updater*.py` |
| Scheduled tasks: interval, daily time, failure alert, disable, save | ✅ `system.spec.ts › scheduled tasks › a task is rescheduled, switched to a daily time, and put back` |
| Scheduled tasks: Run now, run recorded | ✅ `system.spec.ts › scheduled tasks › run now queues a task and its run is recorded` |
| Errors & alerts › Log: CSP report listed, source/code filters, row details (mouse + keyboard), CSV | ✅ `logs.spec.ts › error log › a captured CSP report is listed, filtered, opened and exported` |
| Errors & alerts › Log: IP + status filters | ✅ `logs.spec.ts › error log › the IP and status filters narrow the list` |
| Errors & alerts › Alerts: log switch, 4xx capture + codes, mail alerts, custom recipients | ✅ `policies.spec.ts › errors & alerts: logging, 4xx capture and alert recipients are saved` |
| Errors & alerts: scan-capture tunable | ✅ `tunables.spec.ts › admin-settings-error-alerts: error_log.scan_capture_per_min is saved, kept and reset` |
| Maintenance mode: banner, transfers refused (503), off again | ✅ `policies.spec.ts › maintenance mode: transfers pause with the banner, then resume` |
| Backup: export (categories, passphrase mismatch, step-up), preview, import | ✅ `system.spec.ts › backup & restore › export with a passphrase, preview the file, import it` |
| Backup: not-a-backup file refused | ✅ `system.spec.ts › backup & restore › a file that is not a backup is refused in the preview` |
| Data retention & storage tunables (save, reset, out-of-range stopped in the form) | ✅ `tunables.spec.ts › admin-settings-advanced: retention.invite_days is saved, kept and reset`, `tunables.spec.ts › a value outside its bounds is stopped in the form and nothing is saved` |
