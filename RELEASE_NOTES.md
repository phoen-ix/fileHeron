# file:Heron v1.10.4

**Pager polish.** The shared list pager no longer shows a disabled "Prev" on the
first page (or "Next" on the last) — those controls are now simply hidden when
they don't apply. Applies to every admin list (Users, File history, Sessions,
Quarantine, API tokens, Audit log).

No `.env` change, no migration, no backend change. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.10.4`
- `ghcr.io/phoen-ix/fileheron-worker:v1.10.4`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.10.4`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.10.4`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.10.4`

Click **Update** in `/admin/system` to roll forward.
