# Local dev gates that mirror CI (.github/workflows/ci.yml). Run `make lint`
# before pushing so a lint error surfaces here instead of on a red main.
#
# Intentionally lint-only: this box is the production host, so heavier work
# (image builds, deploys) stays in Docker / the release workflow, not here.

.DEFAULT_GOAL := help

# Single-source the ruff pin from backend/pyproject.toml so this Makefile can
# never drift from CI. Bump it there; lint-backend then tells every dev to
# resync their local ruff.
RUFF_PIN := $(shell sed -n 's/.*"ruff==\([0-9.]*\)".*/\1/p' backend/pyproject.toml)

.PHONY: help lint lint-backend lint-frontend lint-docker

help:
	@echo "Targets:"
	@echo "  lint           run every CI lint gate (backend ruff + frontend eslint)"
	@echo "  lint-backend   ruff on backend/ (must match the pyproject pin ruff==$(RUFF_PIN))"
	@echo "  lint-frontend  eslint on frontend/src"
	@echo "  lint-docker    backend ruff in an ephemeral python:3.12-slim (CI-faithful, no local ruff needed)"

lint: lint-backend lint-frontend

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
