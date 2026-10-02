# Public links and recipients without an account

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/{public_link,external_recipients}.py`, `backend/app/routers/{public,public_links}.py`, `frontend/src/views/PublicShare.vue`, `frontend/src/components/PublicLinkPanel.vue`

## Public links

→ README §Public links.

- Per-share singleton (`UNIQUE(share_id)`). Token = 43-char urlsafe-b64, stored as `token_hash` (SHA-256, public consume path) + `token_encrypted` (Fernet, for the owner-facing re-viewable URL). Legacy `token_encrypted=NULL` → SPA shows "revoke and re-create", which must keep working: a missing guard once turned it into an unhandled IntegrityError → 500, making the documented remedy impossible.
- URL `/d/{token}` → SPA wraps `GET /api/public/{token}` (metadata) + `…/files/{id}/download`. **Password:** Argon2. `POST …/unlock` sets signed cookie `fh_dl_unlock` (HMAC under JWT_SECRET, path-scoped, lifetime min(24h, expires_at)).
- **Counter:** atomic `UPDATE … downloads_remaining-1 WHERE remaining>0` + rowcount. NULL = unlimited.
- **Brute-force lock needs BOTH conditions.** After `PUBLIC_LINK_PASSWORD_RATE_LIMIT` (10) in `PUBLIC_LINK_PASSWORD_WINDOW_SEC` (900) **AND** from `MIN_DISTINCT_IPS_FOR_LOCK` (3) distinct IPs, `locked_until` is set on the **link** (all IPs). **The distinct-IP condition is the whole point**: a link-wide lock reachable by ONE address is a ~10-guess denial of service against the legitimate recipients. A single IP gets the router's per-IP 429 and nothing more.
- **Policy** kv `public_link.policy_mode` ∈ everyone|employees_admins|admins_only + allowlists; single gate `services/public_link.py::is_allowed_to_create` (admin always passes).
- **A link's URL is re-viewable by design, never "shown once".** The token is stored encrypted exactly so the owner (and admins) can copy it again from the share page's `PublicLinkPanel`; only a legacy row without ciphertext, or one that no longer decrypts after a `JWT_SECRET` rotation, falls back to `url_legacy_hint`. The SPA said "copy it now - it won't be shown again" for this long after it stopped being true, which made users think the link was lost; `frontend/tests/i18n.test.ts` pins that no link string says so. API tokens and webhook secrets are the genuine show-once secrets.
- **The link paths `/d`, `/s` and `/r` are code constants, not settings** (`PUBLIC_LINK_BASE_PATH` / `SECRET_LINK_BASE_PATH` / `REQUEST_LINK_BASE_PATH` are `ClassVar`s on `config.Settings`). The SPA routes are fixed and Vue routes on the browser's own URL, so any other value - even behind a proxy rewrite - only made dead links; `PUBLIC_LINK_BASE_PATH` was an env var in `.env.example` until v2.24.0. `tests/test_link_base_paths.py` pins all three against `frontend/src/router/index.ts`.
- **`services/public_link.py::public_url` is the ONE link-URL builder** (`stored_url` rebuilds it from `token_encrypted`, None when it cannot). Two routers each kept a copy of the f-string; a third reader of `PUBLIC_LINK_BASE_PATH` now masks the mail log, so a copy that drifts leaks.

### Recipients without an account (`services/external_recipients.py`, v2.21.0)

`recipients.emails` on `POST /api/shares`; rows in `share_external_recipients`; the share's public link is MAILED to each address. Off by default (`share.external_recipients.enabled`, plus `.offer_invite` for the compose form's "also invite as client" question); both on the Public links admin page, written by its PUT (optional fields, `None` = unchanged).

- **The public-link policy is the gate, deliberately.** An address gets nothing a pasted link would not, so `may_send` = switch AND `is_allowed_to_create` AND not a client, and a request with `emails` must carry `public_link` (`EXTERNAL_RECIPIENT_NEEDS_LINK`) - the password and counter stay the sender's choice.
- **Never look the address up.** Refusing or converting an address "because it has an account" tells an employee that an unconnected client exists; mail goes to exactly what was typed, and this is not account mail (the stored-address rule in CLAUDE.md §Conventions is about mail ABOUT an account).
- **The mail is sent from `share._dispatch_share_created`, BEFORE its early return** - a share whose only recipients have no account notifies no user, and every announcement path (files-added, the sweep, approval) funnels through it, so the link is never mailed before it works. `notified_at` makes it once per address; a revoked or undecryptable link sends nothing and leaves the row unstamped.
- **Whether an address is mailed is the SENDER's per-share choice** (`email_external_link`, default true → per-row `send_link`, migration `202609280002`), shown as its own checkbox. It is **never gated on `notify_recipients`**, which is about ACCOUNT recipients - v2.21.0 coupled them, so "keep this share quiet" for colleagues silently withheld the link from the outside address. A `send_link=false` row is recorded and never mailed; `ShareResponse.external_recipients_emailed` tells the share page.
- **`share_approval._has_client_recipient` counts them** - `outbound_to_clients` means "does this leave the organisation", and the rows are flushed before `is_approval_required` for that reason.
- **The link is a bearer credential in a mail body**: `mail_log._AUTH_LINK_RE` builds its public-link alternative from `settings.PUBLIC_LINK_BASE_PATH` (never a literal `/d/`), `share_link_external` is in `_AUTH_LINK_CATEGORIES` (no resend), and its `[DOWNLOAD_LINK]` placeholder is `auth_link=True`. No unsubscribe footer - no user, no preferences. The link password is never in the mail.
- **Addresses are shown only to `RosterVisibility.may_see_full` viewers** (`ShareResponse.external_recipients`, `[]` otherwise), and audit rows carry a COUNT, never the addresses - erasure cannot reach a person with no account.
- **The share's life is the addresses' retention.** `prune_history` deletes them once the share is `expired`/`revoked`/`deleted`/`failed` (an erased user's shares are revoked, so theirs go too); the mail log keeps the send for its own window. **`rejected` is deliberately NOT ended** - it can be resubmitted and its addresses were never mailed.
