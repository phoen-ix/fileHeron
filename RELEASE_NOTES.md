# file:Heron v1.42.0

**Hardening round 3.** A set of smaller security fixes from the audit: data-erasure
completeness, spreadsheet-export safety, branding-link safety, logo upload limits,
and a couple of robustness fixes. Backend + frontend; no database migration.

## What's fixed

- **Right-to-erasure is now more complete.** Erasing a user also removes their saved
  passkeys (WebAuthn credentials) and any pending email-change records, which carry
  the person's email address - these used to survive an erasure.
- **The analytics CSV export is safe to open in a spreadsheet.** Email addresses in
  the export are now protected against formula injection (a value starting with `=`,
  `+`, `-` or `@`), matching the audit and mail-log exports.
- **A branding link can't run code.** The logo's optional click-through link is now
  restricted to normal web addresses, so a `javascript:` link (e.g. left by a
  malicious config import) can't execute on the login page.
- **Logo uploads can't exhaust memory.** The logo image transcoder now caps the
  image size it will decode, rejecting oversized "decompression bomb" images.
- **Single sign-on fails clearly on a key-rotation mistake.** If an OIDC provider's
  stored secret can't be decrypted (e.g. `JWT_SECRET` was rotated without
  re-encrypting), sign-in now returns a clear error instead of silently sending an
  empty secret.
- **Search treats `%` and `_` literally.** Searching shares for a subject containing
  `%` or `_` now matches those characters, not "any text".

## Upgrade notes

- Backend + worker + frontend roll forward via **Update** in `/admin/system`. No
  database migration, no configuration change.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.42.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.42.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.42.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.42.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.42.0`

Click **Update** in `/admin/system` to roll forward.
