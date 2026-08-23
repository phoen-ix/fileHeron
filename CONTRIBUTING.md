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

**`COMPOSE_PROJECT_NAME` is not optional here.** Compose defaults the project
name to the directory, which for a checkout at `fileHeron/` is `fileheron` -
i.e. the *live* project. Without it this command recreates your running stack
with the e2e overrides: `AV_SKIP=true`, `ENVIRONMENT=development`,
`COOKIE_SECURE=false`, a login rate limit of 1000, and two seeded accounts with
published credentials. It is also what makes the teardown safe to run.

```bash
export COMPOSE_PROJECT_NAME=fileheron_e2e
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
cd e2e && npm ci && npx playwright install --with-deps && npm test

# tear the throwaway project down again (-v drops its volumes too)
cd .. && docker compose -f docker-compose.yml -f docker-compose.e2e.yml down -v
```

## Before you push - the gates CI runs

| Gate | Command |
|---|---|
| Backend lint (ruff, pinned) | `make lint-backend` (or `make lint-docker` if you don't have the pinned ruff) |
| Backend types (mypy, pinned) | `make typecheck` |
| Frontend lint (eslint) | `make lint-frontend` |
| Backend tests | `make test-backend` |
| Frontend type-check + tests | `make build` + `make test-frontend` |
| Migrations up/down roundtrip + MariaDB semantics | `make test-mariadb` (throwaway MariaDB in Docker); also runs in CI (`alembic-roundtrip` job) |
| e2e | runs in CI on PRs (`playwright`) |

`make lint && make test` covers the fast local gates (`make lint` includes
`make typecheck`; mypy has **no exemptions** - see
`backend/tests/test_mypy_has_no_exemptions.py`). The full CI set
(`.github/workflows/ci.yml` + `e2e.yml`) runs on every PR.

## Conventions

- **Naming:** the product is **file:Heron** in UI/prose; everything in code, paths,
  containers, packages, and env vars is **fileHeron** (no colon).
- **Migrations** must be re-runnable: use the `_has_table` / `_has_column` /
  `_has_index` helpers from **`app/db_guards.py`** — *not* from `alembic/env.py`,
  where inside a revision the name `alembic` resolves to the installed library.
  Guard **each op separately**: nesting an index or a NOT NULL tightening inside
  the `create_table` / `add_column` guard means a crash between them skips it
  forever on the retry. Test the `downgrade()` roundtrip.
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
