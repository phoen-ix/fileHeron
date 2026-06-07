# file:Heron v1.41.0

**Build & supply-chain hardening.** No application or behavior change - this release
makes the build reproducible and integrity-checked. Roll forward via **Update** in
`/admin/system` like any other.

## What changed (operators / self-hosters)

- **Pinned, integrity-checked dependencies.** Both the backend and the frontend now
  build from committed lockfiles (`backend/requirements.lock` with hashes, and
  `frontend/package-lock.json`), installed with `pip install --require-hashes` and
  `npm ci`. Two builds of the same commit now produce the same images, and a
  compromised or typo-squatted upstream package can no longer slip into a build.
- **GitHub Actions pinned to commit SHAs.** Every CI/release action is pinned to an
  exact commit (with a `# vN` comment) instead of a movable tag, closing a
  supply-chain risk in the release pipeline.

No database migration, no configuration change, no functional difference for users.

> Maintainer note: regenerate the locks when bumping dependencies -
> `uv pip compile pyproject.toml --generate-hashes -o backend/requirements.lock`
> and `npm install --package-lock-only` in `frontend/`.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.41.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.41.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.41.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.41.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.41.0`

Click **Update** in `/admin/system` to roll forward.
