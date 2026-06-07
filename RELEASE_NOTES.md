# file:Heron v1.35.0

**Security hardening.** This release closes an authentication bypass and several
access-control gaps surfaced by a full-surface security and reliability audit. No
database migration; safe to roll straight forward from v1.34.x.

## What's fixed

- **Passkey sign-in no longer bypasses brute-force protection (important).** The
  passkey login step (`/api/auth/webauthn/begin`) checked your password but, unlike
  the normal sign-in page, applied none of the throttling: the per-IP rate limit,
  the 5-strike account lockout, and the sign-in attempt log were all skipped. An
  attacker who knew an email address could therefore guess passwords through this
  endpoint at full speed, invisibly. Both sign-in paths now run through one shared
  gate, so the rate limit, lockout, attempt logging and lockout email apply
  identically - and an unknown email is now indistinguishable from a wrong password.
- **A successful sign-in no longer resets the shared per-IP throttle.** Previously
  one valid login cleared the rate-limit window for its whole IP, which a determined
  attacker could use to keep guessing other accounts from the same address. The
  window now simply expires on its own.
- **API tokens now respect account lockout.** A pre-issued `fh_...` token kept
  working while its owner's account was locked out; locked accounts are now refused
  on the token path too.
- **Passkey completion re-checks account state.** A passkey ceremony can no longer
  finish a sign-in for an account that was disabled, locked, or left unverified
  between the two steps.
- **The last administrator can no longer be removed.** Demoting or disabling the
  final remaining admin (including yourself) is now refused with a clear error, so
  the organization can't be accidentally locked out of every admin screen.
- **Password changes now send a security alert.** Changing your password emails you
  a heads-up (with a reset link if it wasn't you), matching the email-change flow.
- **Inbound mailbox keeps flowing during a ClamAV outage.** If the virus scanner is
  briefly unavailable while fetching mail, attachments are stored as *pending*
  (gated from download) and ingestion continues, instead of the whole poll aborting
  and silently stalling the inbox until the scanner recovered.

## Upgrade notes

- Backend-only; no frontend change, no database migration. Roll straight forward
  from v1.34.0 / v1.34.1.
- No configuration changes required. Existing passkeys, API tokens and sessions
  continue to work unchanged.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.35.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.35.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.35.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.35.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.35.0`

Click **Update** in `/admin/system` to roll forward.
