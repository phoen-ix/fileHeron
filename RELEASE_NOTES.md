# fileHeron v1.4.1

**Hotfix:** large file uploads (over 100 MB, which use the resumable TUS
protocol) failed with *“HTTP 308 Permanent Redirect.”*

## What was wrong

The frontend reverse proxy (nginx) overwrote the `X-Forwarded-Proto` header that
Traefik sets — telling tusd the request was plain `http` even on an HTTPS site.
tusd then handed back `http://` upload URLs, the upload got redirected
`http → https` mid-transfer, and it failed. Files up to 100 MB were unaffected
(they use the non-resumable direct path).

## Fix

nginx now passes the real forwarded scheme through, so tusd emits correct
`https://` upload URLs and the redirect never happens. Deploy this release to
restore large uploads in the web app. (The desktop client received a matching
client-side fix in `client-v0.9.5`.)

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.4.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.4.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.4.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.4.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.4.1`

Set `FH_TAG=v1.4.1` in your `.env` and `docker compose pull && docker compose up -d`
to roll forward, or click **Update** in `/admin/system`.
