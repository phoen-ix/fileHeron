# file:Heron v1.44.0

**Hardening round 5.** Disaster-recovery safety, single sign-on correctness, and a
couple of robustness limits. Backend-only; no database migration. Rolls forward via
**Update** in `/admin/system`.

## What's fixed

- **A broken backup can no longer wipe your shares.** Restoring a configuration
  backup now fully validates the file BEFORE doing anything irreversible. A
  corrupt-but-readable backup is rejected with a clear error and changes nothing -
  previously it could delete every active share and then fail half-way through the
  restore.
- **Single sign-on verifies the provider's identity document.** The provider's
  discovery document must now declare the same issuer it was fetched from, closing a
  gap where a tampered discovery endpoint could misrepresent the provider.
- **Download links are domain-separated.** The short-lived signed download URL's
  signature is now scoped so it can't be confused with any other signed value.
- **Recipient lists are bounded.** A single share request can include at most 1000
  user IDs and 1000 group IDs, preventing an oversized request from straining the
  server.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No database
  migration, no configuration change. (Outstanding download links re-issue
  automatically; their ~60-second lifetime makes this invisible.)

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.44.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.44.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.44.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.44.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.44.0`

Click **Update** in `/admin/system` to roll forward.
