"""Every `Settings` field is documented in `docs/configuration.md`.

CLAUDE.md names that file as the settings reference, and this audit found 30+
environment knobs it did not mention - the whole scan-guard family, the
retention windows for IP blocks and public-link attempts, the secrets
passphrase throttle, the object-store variables, `SETUP_TOKEN` (audit
2026-11-07). A knob nobody can read about is a knob nobody tunes.

Grouped rows are allowed: `` `ADMIN_BOOTSTRAP_EMAIL` / `_PASSWORD` `` documents
both. The allowlist names what is deliberately kept out of the manual.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CONFIG = _REPO / "backend" / "app" / "config.py"
_DOC = _REPO / "docs" / "configuration.md"

# Deliberately undocumented, with the reason.
_NOT_FOR_THE_MANUAL: dict[str, str] = {
    "TEST_ACCOUNT_EMAIL": "dev seed only; refused in production (documented as the group)",
}


def _settings_fields() -> list[str]:
    tree = ast.parse(_CONFIG.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return [
                st.target.id
                for st in node.body
                if isinstance(st, ast.AnnAssign)
                and isinstance(st.target, ast.Name)
                and not st.target.id.startswith("model_")
            ]
    raise AssertionError("no Settings class")


def _documented_names(doc: str) -> set[str]:
    """Every backticked name, with `` `X_Y` / `_Z` `` groups expanded: the suffix
    token replaces the trailing segments of the full name before it."""
    tokens = re.findall(r"`([A-Z][A-Z0-9_]*|_[A-Z0-9_]+)`", doc)
    names: set[str] = set()
    last_full: str | None = None
    for tok in tokens:
        if tok.startswith("_") and last_full:
            segments = last_full.split("_")
            for k in range(1, len(segments)):
                names.add("_".join(segments[:k]) + tok)
        else:
            names.add(tok)
            last_full = tok
    return names


def test_every_settings_field_is_in_the_configuration_reference():
    documented = _documented_names(_DOC.read_text())
    missing = [
        f for f in _settings_fields() if f not in documented and f not in _NOT_FOR_THE_MANUAL
    ]
    assert missing == [], f"add a row to docs/configuration.md for: {missing}"


def test_the_allowlist_names_only_real_fields():
    fields = set(_settings_fields())
    assert set(_NOT_FOR_THE_MANUAL) <= fields, set(_NOT_FOR_THE_MANUAL) - fields


def test_the_group_expansion_reads_the_existing_rows():
    names = _documented_names("| `ADMIN_BOOTSTRAP_EMAIL` / `_PASSWORD` | empty | x |")
    assert {"ADMIN_BOOTSTRAP_EMAIL", "ADMIN_BOOTSTRAP_PASSWORD"} <= names
