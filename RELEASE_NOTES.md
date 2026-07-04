# file:Heron v1.58.0

**Session, sign-in, and erasure correctness.** The second follow-up release from
the code audit, focused on authentication and account lifecycle. No database
migration and no host step - deploy from this banner.

## What's fixed

- **No more surprise "signed out everywhere".** Using **Log out other devices**, a
  password change, or the session-cap eviction could later cause a background
  device's routine token refresh to be mistaken for token theft - which signed you
  out of *every* session and raised a false security alert. Deliberately-ended
  sessions are now told apart from genuine token reuse, and two browser tabs
  refreshing at the same moment no longer trip it either. Real token theft (a
  replayed, already-rotated token) is still caught and still revokes everything.
- **The session-lifetime setting now means "idle timeout".** Shortening the refresh
  token lifetime now measures from a session's **last activity**, so a session
  that's actively in use keeps working; only genuinely idle sessions past the new
  window are ended. (Previously it measured from original sign-in and could cut off
  sessions that were still active.)
- **SSO and passkey logins are now recorded like password logins.** Signing in with
  single sign-on (OIDC) or a passkey (WebAuthn) now appears in your login history
  and the audit log, and triggers the **new-device email alert** - previously these
  logins left no trace and never alerted.
- **Erasing a user now closes their shares.** A GDPR erasure hard-deletes the user's
  files but was leaving their shares (and any public links) still "active" over
  now-deleted files. Those shares and links are now revoked as part of the erasure.
- **More accurate new-device detection for IPv6.** The "same network" grouping used
  for new-device alerts mis-handled IPv6 addresses, which could produce false "new
  device" alerts. It now groups an IPv6 /64 correctly.

## Notes

- **No migration, no host step** - the in-app Update swaps the backend, worker, and
  frontend images.
- One-time effect: because the IPv6 grouping changed, IPv6 users may get a single
  "new device" alert on their next sign-in as their device is re-registered under
  the corrected grouping.
