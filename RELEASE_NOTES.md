# file:Heron v1.13.0

**Change a user's email address.** Until now an account's email was fixed for
life. This release lets **admins change any user's email** (their own included),
optionally lets **users change their own**, verifies the change so it can't be
hijacked or fat-fingered into a lockout, and cleanly handles accounts that sign
in via single sign-on (SSO).

You're in control of how it behaves — a new **Settings → Email change** page
(`/admin/settings/email-change`) has three knobs:

- **How a change is verified**
  - *Confirm via the new address* (default) — the email only changes after the
    user clicks a link sent to the **new** address. Their old address keeps
    working the whole time and receives a security notice with a cancel link.
    Typo-proof, with no window where the account points at an unproven address.
  - *Confirm via both addresses* — a link goes to the **old and new** address;
    the change only applies once both are clicked. The strongest option. (If the
    old mailbox is no longer reachable, use an admin change with *apply
    immediately* instead.)
  - *Apply immediately* — the address changes at once (admins are trusted). A
    security notice still goes to the old address.

- **Who can change an email** — admins always can. Flip **"Let users change their
  own email"** on to add a *Change email* option to every user's Account page
  (they re-enter their password to use it). Off by default, so out of the box
  only admins can do it.

- **What happens to SSO** — when the user signs in through an identity provider
  (Microsoft Entra, Google, Authentik, Keycloak…), changing their email can
  *Reset SSO and send a set-password link* (default — so they're never locked
  out and can re-link SSO later), *Reset SSO only*, or *Keep the SSO link*.

### Where to find it

- **Admins:** open any user at **Users → (a user) → Security → Change email**.
  There's an optional *apply immediately (skip verification)* checkbox for the
  case where someone has lost access to their old mailbox. A small *verification
  pending* tag appears next to a user whose new address hasn't been confirmed
  yet.
- **Users (if you enabled self-service):** **Account → Email**.

Every change is written to the audit log, and the confirmation / security emails
appear in the Mail log (with their one-time links redacted, like password
resets).

### Upgrade notes

- **No `.env` change.** One small database migration runs automatically on
  start-up (a new table for pending email changes).
- Sensible defaults mean nothing changes in behaviour until you adjust the new
  settings — self-service stays **off**, and admin email change is available
  immediately.
- Also fixed: the admin user page now correctly detects already-erased accounts.
- No desktop-client change.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.13.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.13.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.13.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.13.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.13.0`

Click **Update** in `/admin/system` to roll forward.
