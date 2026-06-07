# file:Heron v1.34.1

**Translation fixes.** Several admin strings - mostly on *Settings -> Advanced* -
were rendering raw keys or English-only because their translations were never
added. This release fills them in for both English and German.

## What's fixed

- **Advanced settings** now have complete labels, help text and group headers for
  every runtime tunable, including the `Storage & low-disk`, `Anomaly detection`
  and `Updates` groups (the last added in v1.34.0). Previously some rows showed
  their raw key (e.g. `anomaly.enabled`) and several had no help text.
- The inbound-message **Delete** button no longer shows a raw `common.delete`
  label.
- Full English + German parity restored across the locale files.

## Upgrade notes

- Frontend-only; no backend change, no database migration. Safe to roll straight
  forward from v1.34.0.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.34.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.34.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.34.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.34.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.34.1`

Click **Update** in `/admin/system` to roll forward.
