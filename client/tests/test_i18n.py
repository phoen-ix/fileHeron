"""Coverage gate for the v0.8.0 i18n locale files.

Walks every ``.py`` in ``src/fileheron_client/ui/`` (and a few outside
ui that also call ``t()``) via AST, collects every literal-key
argument to a ``t(...)`` callsite, and asserts the key exists in BOTH
``locales/en.json`` and ``locales/de.json``. en.json is the
authoritative reference; missing keys there are loud failures.

Also asserts the JSON structures parse correctly + share the same
top-level namespaces (no `share_detail` in en that's `shareDetail`
in de, etc.)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "fileheron_client"
LOCALES = PKG / "locales"


def _all_py_files() -> list[Path]:
    """Every .py inside the package, skipping __pycache__."""
    files: list[Path] = []
    for p in PKG.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        files.append(p)
    return files


def _collect_t_calls(source: str) -> list[str]:
    """Return literal-string first-args of every ``t('...')`` callsite."""
    tree = ast.parse(source)
    keys: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "t"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.append(node.args[0].value)
    return keys


def _load_locale(name: str) -> dict:
    with (LOCALES / f"{name}.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def _has_dotted(table: dict, dotted: str) -> bool:
    node = table
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def test_locale_files_parse() -> None:
    """JSON is well-formed."""
    en = _load_locale("en")
    de = _load_locale("de")
    assert isinstance(en, dict) and en
    assert isinstance(de, dict) and de


def test_locale_files_share_top_level_namespaces() -> None:
    en = _load_locale("en")
    de = _load_locale("de")
    assert set(en.keys()) == set(de.keys()), (
        f"top-level namespaces diverge: en\\de={set(en.keys()) - set(de.keys())}, "
        f"de\\en={set(de.keys()) - set(en.keys())}"
    )


def test_every_t_call_has_en_and_de_key() -> None:
    en = _load_locale("en")
    de = _load_locale("de")
    missing_en: list[tuple[str, str]] = []
    missing_de: list[tuple[str, str]] = []
    for path in _all_py_files():
        try:
            keys = _collect_t_calls(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for key in keys:
            if not _has_dotted(en, key):
                missing_en.append((str(path.relative_to(ROOT)), key))
            if not _has_dotted(de, key):
                missing_de.append((str(path.relative_to(ROOT)), key))
    assert not missing_en, (
        "Missing keys in locales/en.json (authoritative):\n  "
        + "\n  ".join(f"{p}: {k}" for p, k in missing_en)
    )
    assert not missing_de, (
        "Missing keys in locales/de.json:\n  "
        + "\n  ".join(f"{p}: {k}" for p, k in missing_de)
    )
