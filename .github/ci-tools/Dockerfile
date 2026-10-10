# NEVER BUILT. The version pins of the linter images ci.yml runs with
# `docker run`, kept in a Dockerfile because that is what Dependabot's docker
# ecosystem reads: a pin inside a workflow's `run:` line is watched by nothing.
# All three sat 9 to 24 months behind until 2026-10-10, the same class
# .github/dependabot.yml records for the clamav pin.
#
# ci.yml reads each reference from here by its stage name (sed, the way it
# reads the ruff and mypy pins from backend/pyproject.toml).
# backend/tests/infra/test_ci_tool_pins.py runs those sed expressions against
# this file and fails if a workflow names one of these images itself again.
FROM koalaman/shellcheck-alpine:v0.11.0 AS shellcheck
FROM rhysd/actionlint:1.7.12 AS actionlint
FROM hadolint/hadolint:v2.15.1 AS hadolint
