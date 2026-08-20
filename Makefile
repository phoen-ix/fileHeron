# Developer convenience targets. `make lint` / `make test` mirror the CI gates;
# the up/dev/down/build/seed helpers wrap the common docker compose + npm
# commands. See CONTRIBUTING.md. Heavy release work stays in the release
# workflow, not here.

.DEFAULT_GOAL := help

# Single-source the ruff pin from backend/pyproject.toml so this Makefile can
# never drift from CI. Bump it there; lint-backend then tells every dev to
# resync their local ruff.
RUFF_PIN := $(shell sed -n 's/.*"ruff==\([0-9.]*\)".*/\1/p' backend/pyproject.toml)

.PHONY: help lint lint-backend lint-frontend lint-docker typecheck test test-backend \
	test-frontend up dev down build seed fmt

help:
	@echo "Targets:"
	@echo "  lint / lint-backend / lint-frontend   CI lint gates (ruff + eslint)"
	@echo "  lint-docker    backend ruff in an ephemeral python:3.12-slim (no local ruff needed)"
	@echo "  typecheck      backend mypy, exactly as CI's infra-lint job runs it"
	@echo "  test / test-backend / test-frontend   pytest (needs backend .[dev]) + vitest"
	@echo "  build          frontend production build (vue-tsc type-check + vite)"
	@echo "  dev            docker compose dev stack (auto-reload + HMR)"
	@echo "  up / down      start / stop the compose stack"
	@echo "  seed           seed the dev test accounts into a running stack"
	@echo "  fmt            autoformat the frontend (prettier)"

lint: lint-backend lint-frontend typecheck

test: test-backend test-frontend

# Assumes `cd backend && pip install -e .[dev]` (see CONTRIBUTING.md). In-memory
# SQLite via conftest - no running services needed.
test-backend:
	cd backend && python -m pytest -q

test-frontend:
	cd frontend && npm run test

build:
	cd frontend && npm run build

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

up:
	docker compose up -d

down:
	docker compose down

seed:
	docker compose exec backend python scripts/seed_dev.py

# mypy runs in CI's infra-lint job, and used to be invisible here - it was
# absent from this file and from CONTRIBUTING's gate table, so the documented
# local gate was ruff-only and a type error first surfaced on a red main. Runs
# in an ephemeral container matching the backend image, so it needs nothing
# installed on the host and cannot drift from the pinned mypy in pyproject.
# `app scripts` is the same scope CI uses.
typecheck:
	cd backend && docker run --rm -v "$$PWD":/w -w /w -e PYTHONDONTWRITEBYTECODE=1 \
		python:3.14-slim sh -c \
		'set -e; pip install -q --require-hashes -r requirements.lock; \
		 pip install -q -e ".[dev]"; python -m mypy app scripts'

# Frontend uses prettier; backend style is enforced by ruff (make lint).
fmt:
	cd frontend && npm run format

# Runs the same ruff CI runs. Guards the drift that reddened v1.60.0/v1.61.0:
# if the local ruff is not the pinned version it fails loudly with the one
# command that fixes it, instead of silently linting with the wrong ruff (or,
# on a too-old ruff, failing to even parse the config). --no-cache avoids a
# root-owned .ruff_cache/ in the repo dir.
lint-backend:
	@have=$$(cd backend && ruff --version 2>/dev/null | awk '{print $$2}'); \
	if [ "$$have" != "$(RUFF_PIN)" ]; then \
		echo "local ruff '$$have' != pinned '$(RUFF_PIN)'"; \
		echo "  fix: pip install --user --upgrade ruff==$(RUFF_PIN)   (or: make lint-docker)"; \
		exit 1; \
	fi; \
	cd backend && ruff check --no-cache .

lint-frontend:
	cd frontend && npm run lint

# Escape hatch when the host ruff can't/shouldn't be synced: re-resolves the
# pin in a throwaway container, faithful to CI with nothing installed on the
# host. --no-cache keeps it from leaving a root-owned .ruff_cache behind.
lint-docker:
	cd backend && docker run --rm -v "$$PWD":/w -w /w python:3.12-slim \
		sh -c "pip install -q ruff==$(RUFF_PIN) && ruff check --no-cache ."
