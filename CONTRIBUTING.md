# Contributing to file:Heron

Thanks for your interest. This is a self-hosted, single-organisation file-sharing
platform (Python/FastAPI backend, Vue 3 SPA, a CustomTkinter desktop client).

Security vulnerabilities: **do not** open a public issue - see [SECURITY.md](SECURITY.md).

## Dev setup

Fastest full stack (auto-reload + HMR):

```bash
cp .env.example .env      # required: the base compose fail-fasts on the secrets
make dev                  # docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

If your host user isn't UID 1000, first make the data dirs writable:
`docker run --rm -v "$PWD/data":/data alpine chown -R 1000:1000 /data/{uploads,quarantine,files,updater}`.

### Backend tests (no services needed - in-memory SQLite via conftest)

```bash
cd backend
pip install -e '.[dev]'
python -m pytest -q         # or: make test-backend
```

### Frontend

```bash
cd frontend
npm install
npm run test                # vitest      (make test-frontend)
npm run build               # vue-tsc type-check + vite build (the pre-ship gate)
```

### End-to-end (Playwright, against a real compose stack)

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
cd e2e && npm ci && npx playwright install --with-deps && npm test
```

## Before you push - the gates CI runs

| Gate | Command |
|---|---|
| Backend lint (ruff, pinned) | `make lint-backend` (or `make lint-docker` if you don't have the pinned ruff) |
| Frontend lint (eslint) | `make lint-frontend` |
| Backend tests | `make test-backend` |
| Frontend type-check + tests | `make build` + `make test-frontend` |
| Migrations up/down roundtrip | runs in CI against real MariaDB (`alembic-roundtrip` job) |
| e2e | runs in CI on PRs (`playwright`) |

`make lint && make test` covers the fast local gates. The full CI set
(`.github/workflows/ci.yml` + `e2e.yml`) runs on every PR.

## Conventions

- **Naming:** the product is **file:Heron** in UI/prose; everything in code, paths,
  containers, packages, and env vars is **fileHeron** (no colon).
- **Migrations** must be re-runnable: use the `_has_table` / `_has_column` /
  `_has_index` helpers from `alembic/env.py`, and test the `downgrade()` roundtrip.
- **Service-not-router:** routers parse + delegate + serialise; business logic,
  audit, and notification dispatch live in `services/`.
- New backend imports go in `backend/pyproject.toml` in the same change.
- The architecture, subsystems, and operator guide are documented in
  [README.md](README.md).

## Pull requests

- Branch from `main`; keep the change focused; include a test for any bug fix or
  new behaviour.
- Describe what changed and why. If a change needs a host step on upgrade (compose,
  infra images, Traefik), call it out - the in-app updater only swaps the
  backend/worker/frontend images.
