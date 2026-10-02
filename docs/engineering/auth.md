# Auth: sessions, two factors, SSO, email change

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/{auth,jwt_session,rate_limit,totp,twofa_policy,twofa_enforcement,webauthn,login_alert,hibp,oidc,oidc_admin,jwks,email_change,email_change_policy,user_lookup}.py`, `backend/app/routers/{auth,oidc,oidc_connect,webauthn,account}.py`, `frontend/src/api/client.ts`, `frontend/src/stores/auth.ts`, `frontend/src/views/{Login,LoginSecondFactor,TwoFactorSetup,ConfirmEmailChange,CancelEmailChange}.vue`, `client/src/fileheron_client/api/{client,auth}.py`

## Auth

**Login flows** all funnel through `services/jwt_session.py::create_refresh_token`
→ `enforce_session_cap` (session-cap eviction): `POST /api/auth/login` (`TOTP_REQUIRED`/`INVALID_TOTP`
when 2FA on), `/login/recovery`, `/webauthn/begin`+`/complete`, OIDC
`/oidc/start|callback/{id}` (state cookie packs `state::provider_id`),
`/register-from-invite`. **Session** = JWT access (15min, HS256) + refresh cookie
`fh_refresh` (httpOnly, Secure-in-prod, SameSite=Lax, 7d, scoped `/api/auth`;
64 random bytes, SHA-256 in DB).

### Refresh rotation

- **`replaced_by_id` is the theft discriminator, not `revoked_at`.** NULL = a deliberate revoke, soft-failed `INVALID_REFRESH`; set = a rotated link replayed, which revokes the whole user family and audits `refresh_token_reused`. Two racers on one cookie hit BOTH branches depending on whether the loser's read lands before or after the winner's COMMIT.
- **Never let one client refresh concurrently on one cookie.** An immediate replay is indistinguishable from theft server-side, so **a time-based grace window in `rotate_refresh` cannot be made safe** - any window wide enough to fix the race admits a stolen token (`tests/test_auth_flow.py::test_refresh_reuse_revokes_entire_family` replays milliseconds apart and correctly expects `TOKEN_REUSE`). It is PREVENTED per client: the SPA on `api/client.ts::withRefreshLock` (`navigator.locks` / `localStorage`), the desktop client on `ApiClient._refresh_access_token`'s `threading.Lock` plus a "the token already moved" short-circuit. Both **fail open** - a lock that can wedge sign-in is worse than the race. Fix both clients or neither.
- **A lock holder must never outlive its own lock.** `tryAcquireStorageLock` overwrites a record older than `LOCK_TTL_MS` as abandoned, so a refresh that outlives the TTL lets a waiter take over mid-flight - concurrent rotation exactly when the backend is slow. The constants are DERIVED, not commented: `LOCK_TTL_MS = 2 * REFRESH_TIMEOUT_MS + 2_000` (the `2 *` budgets both of `doRefresh`'s attempts, an argument that dies the moment someone retries `unavailable`). `client.refresh.test.ts` asserts on the real exported values, because a waiter stealing a LIVE lock has no behavioural signal - the relationship IS the invariant. Only the localStorage fallback is affected (Web Locks needs a secure context), i.e. plain HTTP, the fresh-self-host default via `COOKIE_SECURE:-false`.
- **A failed refresh is TWO different things, and only one signs anyone out.** `api/client.ts::RefreshOutcome`: `expired` = a credential verdict, and **only 401 and 403 are verdicts**; `unavailable` = no answer (a proxy 502/503/504 during a restart, a network drop, a timeout, a 429, the scan guard's 404) and must leave the session, the access token and `onAuthLost` alone - one boolean for both made the in-app updater's deliberate 9-25s restart sign every open tab out. Classify on `err.response?.status`, **never on the envelope code** (a Traefik 502 is plain `Bad Gateway`, an nginx one is HTML; `asEnvelope` returns null for both). Deliberately **no retry loop** - no delay short enough to keep the UI responsive covers a 25s restart, and the session self-heals on the next request. `refreshOnce` was renamed `refreshSession` so the compiler would find its truthy call sites (every outcome string is truthy).
- **`bootstrap()` may run more than once and must never END a session.** `router.beforeEach` awaits it on every navigation and `main.ts` gates `app.mount()` on it; the memo is dropped on `unavailable` (so a restart blip is not cached forever), and its `else` branch nulls `user`, so a later blip would end a live session and redirect to `/login`. It returns early when `user` is set - signing out is the interceptor's job, on a verdict - and a re-probe cooldown stops `beforeEach` refreshing on every click.
- **A refresh 200 is not proof of a token.** An SPA-fallback misconfiguration or captive portal answers `/api/auth/refresh` with `200 text/html`; taking that as success drops the Authorization header for the replay and signs the user out on every request. `attemptRefresh` checks the token is a non-empty string.
- **`isAuthCall` in `frontend/src/api/client.ts` must list every route that 401s for a WRONG SUBMITTED SECRET** (`INVALID_CREDENTIALS`/`INVALID_TOTP` from a signed-in caller). A replayed 401 fires `onAuthLost`, so a missing entry **signs the user out for a typo** and on the login paths costs 2 against `failed_login_count`. The list was found short three times by enumerating raise sites by eye; `backend/tests/test_wrong_secret_routes.py` AST-scans every `AppError(401, ...)` carrying a `_WRONG_SECRET` code, requires each DECLARED with its route, and asserts the reachable ones appear in the `isAuthCall` chain. Both halves assert their scan matched something.

### Limits, lockout, forensics

- **Session cap** `MAX_ACTIVE_SESSIONS_PER_USER` (default 10) - oldest evicted per login. Cleanup cron soft-revokes expired, hard-deletes past `REFRESH_TOKEN_RETENTION_DAYS` (30).
- **Lockout:** 5 consecutive `INVALID_CREDENTIALS` → `locked_until = now+15min` + lockout email (6h dedup); success resets.
- **An admin can lift a lockout now:** `POST /api/admin/users/{id}/unlock` (`user_management.unlock_account`, audited `account_unlocked`, no step-up - it grants nothing the user's next login would not). It clears `failed_login_count` + `locked_until` and deliberately leaves `lockout_email_sent_at` (the mail dedup, which no reset clears). `AdminUserItem.locked_until` is set only while the lock is in force - a lapsed one stays in the column until the next attempt.
- **Per-IP rate limit:** 10 / 15min Redis sliding window → 429 `RATE_LIMITED`, fail-open. The same `check_ip_allowed(...)` gates register/forgot/verify/reset/change-password.
- **Forensics:** every attempt → `login_attempts`; new device → `known_devices` (UA-hash + IP /24 geohash) → `services/login_alert.py::fire_new_device_alert` on first sighting, carrying the real client IP + browser version + raw user-agent.

### Two factors

- **2FA enforcement** (`services/twofa_policy.py::is_2fa_required`, computed live, no static column): kv `twofa.required_roles` + `twofa.required_group_ids` override env `REQUIRE_2FA`. **No admin escape**; API tokens short-circuit (`request.state.auth_via == "api_token"`).
- **`is_2fa_required` answers "must they still SET 2FA UP", not "must they present it"** - it returns False once a user *has* TOTP. To challenge anyone use `totp_svc.is_enabled`, as the password flow does; reaching for the enrolment predicate is how the challenge went missing on two paths at once.
- **Enrolled TOTP is challenged after OIDC and after a passkey too.** Both paths once called `finalize_successful_login` directly. **A UV-verified assertion IS the second factor**: `/webauthn/begin` requests `UserVerificationRequirement.REQUIRED` when TOTP is enabled, `webauthn.authenticate_complete` returns `PasskeyAuthResult(user, user_verified)`, and only `user_verified=False` earns the pending token - a passkey is not automatically a second factor. Because a passkey can stand in for TOTP, `register/begin` is step-up gated like `/2fa/disable`.
- **The half-authenticated state is `jwt_session.create_pending_2fa_token`** (`type: "pending_2fa"`, 5 min), exchanged at `POST /api/auth/2fa/complete`. Additive by design: `resolve_user_from_access_token` refuses any type that is not `"access"`, so a pending token fails closed everywhere. **Do not add `amr`/`acr` to the access token** instead - it touches mint, resolve, rotate and every consumer, and needs a default for already-issued tokens.
- **The exchange accepts recovery codes, not only TOTP** - otherwise a user who loses their authenticator and signs in via SSO has no route back short of an operator on the host.
- **`rate_limit.record_success` must not run until the second factor passes.** It clears `failed_login_count` and `locked_until`; the OIDC callback called it at the first factor. Pinned by a test.

### Revocation

- **`users.sessions_invalidated_at` is what makes "revoke" cover access tokens.** Stamped in `jwt_session.revoke_all_user_refresh_tokens` - one chokepoint for logout-others, password change/reset, email change, admin revoke-all, reuse detection and backup import - and checked in `resolve_user_from_access_token` on the User row already being SELECTed.
- **Compared at second granularity with `<`, not `<=`, on purpose.** `change_password` revokes and re-mints inside one request, so `<=` signs the user out for changing their password. The ≤1s window is pinned by a test.
- **Single-session `logout` deliberately does NOT stamp it** - the mark is per-user and would close every other tab. `POST /api/auth/sessions/revoke-others` goes through the chokepoint and then **re-mints the caller's own session**, like `change_password`.
- **Revoking ONE session leaves its access token valid for the rest of its TTL** (15 min default, admin-raisable to 1440). A per-token denylist was considered and not built: it needs a Redis lookup on every authenticated request, the hot-path I/O `docs/engineering/scan-guard.md` refuses. The sessions help text on the account page and the admin user page states the window; keep both true if a denylist ever lands.
- **Any signed token standing in for a session honours the same mark**, via `jwt_session.was_issued_before_revocation` - the SSE stream token and the unsubscribe token both carry an issue time for this (`docs/engineering/mail.md`). Deriving it from `exp - TTL` would reject tokens minted legitimately after a revoke for a whole TTL.

**Email verification is unreachable by construction, and deliberately left so.**
Every creator writes `email_verified=True` (admin bootstrap, setup, invites, user
management, config-backup import, `promote_user`, `seed_dev`); only the model
default is False. `POST /api/auth/resend-verification` needs a signed-in user
while the first factor refuses unverified users with `EMAIL_NOT_VERIFIED`, so
even a hand-made unverified row cannot request a mail. Removing the surface spans
the column, the `verify` template (four files), two SPA routes and seven pinned
tests; making it real needs an anonymous resend route plus a policy toggle that
creates invitees unverified. Both are out of scope. **Do not "fix" the model
default to True, and do not extend the flow without the anonymous resend route.**

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): preset ∈ entra|google|authentik|keycloak|custom, issuer_url, client_id, `client_secret_encrypted` (Fernet, HKDF over JWT_SECRET), redirect_uri, enabled. **Binding:** `users.oidc_provider_id` + composite unique `(provider_id, oidc_subject)` - each user binds to one provider. Presets in `services/oidc_admin.py::PROVIDER_PRESETS`. DELETE refuses `OIDC_PROVIDER_HAS_USERS`.
- **Roles are local. No group→role mapping exists.** `groups_claim`/`admin_groups`/`employee_groups` were dropped in migration `202607040001`. An IdP group claim changes nothing: linking binds an identity, it does not grant a role.
- **Callbacks:** `handle_callback` (anon login) - `(provider, sub)` match → return; else verified-email match against an **un-linked** local user → link + audit (via=`auto_link`); else `OIDC_NO_ACCOUNT` (403), **no auto-create**. `handle_connect_callback` (authed) refuses `OIDC_ALREADY_LINKED`/`OIDC_EMAIL_MISMATCH`/`OIDC_SUBJECT_TAKEN`.
- **Verification:** sig + issuer + audience + expiry + nonce (pyjwt); JWKS cached per-provider (`services/jwks.py`). Allowlist `RS256/384/512`, `ES256/384` - **`none` and `HS*` refused** (downgrade defense).
- **Every JSON fetch from a host this instance does not control goes through `utils/http_fetch.fetch_json_capped`** (streamed, refused past its cap): SSO discovery, JWKS, the admin issuer probe and the token exchange at 1 MiB, the release check at 8 MiB (reported as a described failure, never raised). A non-streamed `client.get().json()` reads the whole body before anything can check its size; only discovery and JWKS were capped, each with its own copy of the loop, and `int(Content-Length)` in discovery was a 500 on a non-numeric header. `tests/test_outbound_size_caps.py` pins every caller with a VALID oversized JSON body (an unparseable one fails them for the wrong reason) and allows exactly three modules to build an httpx client: the helper, HIBP (fixed public host) and webhook delivery, which streams and reads only the status.
- **The issuer check is ours, not pyjwt's, and normalises a trailing slash on BOTH sides.** pyjwt's `issuer=` compares byte-for-byte, so passing an `rstrip("/")`'d expectation while the IdP echoes its issuer verbatim meant **any provider whose canonical issuer ends in `/` could never log in** - including the shipped **Authentik preset** (`oidc_admin.py`, `https://{host}/application/o/{slug}/`); discovery had always rstripped both sides, so it only failed at the last step, and `test-connection` reported **ok** because it rstrips too. Since `issuer=` is no longer passed and `iss` is not in the `require` list, the **presence** check lives in `_verify_token_response` as well - drop it and a token with no issuer at all passes. Exactly one difference is tolerated (the trailing slash) and nothing else. `tests/_oidc_helpers.py::make_claims` must echo `issuer_url` **verbatim**: it once built `iss` with the same `.rstrip("/")` expression the implementation applied, so no fixture could ever disagree with the code.

## Email change

`services/email_change.py::_apply_email_change` is the **only** place
`users.email` is mutated. `services/email_change_policy.py` is the live read
layer; the mode + OIDC policy are **frozen onto the pending row** at request
time. All behaviour admin-tunable via `email_change.*` kv.

- `MeResponse.can_change_own_email` drives the SPA.
- **Modes** (`verification_mode`): `immediate` (apply at once, admin-trusted) · `verify_new` (default; confirm via NEW address) · `verify_both`. Email only changes after proof-of-control and lands `email_verified=True`, so the login gate is **never** tripped (no lockout).
- **SSO reset** (`oidc_mode`): `reset_setpw` (default - unlink + mint a set-password token so an SSO-only user isn't locked out) · `reset_only` · `keep`. OIDC matches by **subject** not email, so reset is a deliberate security choice.
- On apply: refresh tokens revoked; audit `email_changed`; old-address security alert (+ cancel link in pending modes); completion notice to the new address.
- **A pending change is visible and cancellable on the account page**: `GET /api/account/email` (`email_change.pending_for`; its own route, not a `/me` field, because `/me` loads on every navigation) and the existing `DELETE` - shown even where self-service is off, since an admin can start a change. An admin's cancel is audited with the ADMIN as actor (`cancel_email_change(actor=)`, `via="admin_revoke"`). `/account/email` serves three methods, so its `isAuthCall` entry is guarded to POST, pinned by `test_a_shared_path_is_excluded_only_for_its_secret_method`.
- **Mail-log masking:** confirm/cancel URL paths are in `mail_log._AUTH_LINK_RE` + `_AUTH_LINK_CATEGORIES` - **don't drop them** or a live confirm token leaks into the browsable mail log. The set-password link reuses the already-masked `/reset-password/{token}`.
