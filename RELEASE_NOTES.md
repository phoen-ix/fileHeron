# file:Heron v1.39.0

**Security hardening (round 2).** Five more audit fixes across single sign-on, email
logging, uploads, data-erasure and bulk downloads. Backend-only; no database
migration. Rolls forward via **Update** in `/admin/system`.

## What's fixed

- **Admin accounts can't be silently linked to single sign-on (important).** An
  unauthenticated sign-in callback used to link any existing account to a provider
  when the email matched. For an admin account that's a takeover risk if the
  identity provider's email claim can be influenced. Admins must now link SSO
  deliberately from their own account settings; the automatic match no longer
  applies to them.
- **One-time email links no longer leak to the server log.** If email is left
  unconfigured on a production server, the app previously printed full message
  bodies - including live password-reset / verification / invite links - to the
  container log. In production it now logs only the recipient and subject; the
  admin mail log keeps its masked copy.
- **Large single-shot uploads can't exhaust server memory.** The direct-upload path
  used to hold the whole file in memory; a few simultaneous uploads could crash the
  backend. It now streams straight to disk, and correctly frees the reserved quota
  if a write fails.
- **Erasure now clears inbound-email sender details too.** A right-to-erasure
  request now also removes the person's email address and name from any messages
  they sent into the admin inbox, instead of leaving them searchable.
- **The bulk "download all as ZIP" limit can't be bypassed.** A crafted request
  could fetch the whole archive without counting against a share's or public link's
  download limit (and without logging it). ZIP downloads are now always counted.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No frontend
  change, no database migration, no configuration change.
- If you use OIDC single sign-on with admin accounts: an admin signing in for the
  first time via SSO must first link their provider from *Account -> Single sign-on*
  (their account is no longer auto-linked on the login callback).

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.39.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.39.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.39.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.39.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.39.0`

Click **Update** in `/admin/system` to roll forward.
