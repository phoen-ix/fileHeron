# fileHeron v1.5.6

**Fix: OIDC (SSO) login crash + a repo-wide code-quality cleanup.**

## What changed

- **OIDC SSO login fixed.** The anonymous SSO routes
  (`/api/auth/oidc/start/{provider_id}` and `/callback/{provider_id}`) referenced
  a service module that was never imported — so clicking a provider's **Sign in**
  button would have crashed with a 500 (`NameError`) instead of redirecting to
  the identity provider. If you use (or were about to set up) Entra / Google /
  Authentik / Keycloak SSO, it works now. (Local password + API-token logins were
  never affected.)
- **Zero-warning lint baseline.** The backend is now clean under its full linter
  ruleset (was carrying ~525 findings). This was almost entirely mechanical —
  import ordering, modern type-annotation syntax, exception chaining
  (`raise … from …`), collapsing nested conditionals — with no behaviour change,
  plus genuine ignores for intentional framework idioms (FastAPI dependency
  defaults) and removal of a few dead variables. Backed by the full test suite
  (552 passing).

This keeps future changes honest: a new bug that trips the linter now stands out
instead of hiding among hundreds of pre-existing warnings.

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.6`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.6`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.6`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.6`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.6`

Click **Update** in `/admin/system` to roll forward.
