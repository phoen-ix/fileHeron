# file:Heron v1.37.0

**Server-side request hardening.** Two backend security fixes from the audit:
webhook URLs are now SSRF-guarded, and the self-update tag is format-validated.
Backend-only; no database migration. Rolls forward via **Update** in
`/admin/system`.

## What's fixed

- **Webhooks can no longer be pointed at internal targets (SSRF).** A webhook URL
  is admin-controlled and fetched by the server, so it could previously be aimed
  at the cloud metadata endpoint, a loopback service (e.g. Redis), or other
  non-routable internal addresses. Webhook create/update now reject such targets,
  delivery re-checks the target each time (so a URL that resolves to an internal
  address is blocked even if it slipped in via a config import), and redirects are
  no longer followed. Self-hosted receivers on your private LAN remain allowed -
  only loopback / link-local / metadata / reserved ranges are blocked, matching
  the existing policy for OIDC and update checks. (A transient DNS hiccup no longer
  permanently fails a legitimate webhook - it simply retries.)
- **The self-update target tag is validated.** The version tag passed to an update
  flows into a container pull and the host's `FH_TAG`, so it is now constrained to
  the exact release-tag shape (`vMAJOR.MINOR.PATCH`) - rejecting `latest`,
  arbitrary refs, and any shell metacharacters before they reach the host.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No frontend
  change, no database migration.
- **If you use webhooks pointed at a public SaaS endpoint or a receiver on your
  own LAN, they continue to work unchanged.** Only internal/non-routable targets
  (loopback, cloud-metadata, link-local) are now refused.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.37.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.37.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.37.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.37.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.37.0`

Click **Update** in `/admin/system` to roll forward.
