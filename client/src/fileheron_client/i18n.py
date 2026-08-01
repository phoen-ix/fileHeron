"""Tiny dict-based translation layer (v0.8.0).

Why not gettext / Babel: those want .po files + a compilation step;
the build is PyInstaller + Windows .exe and the surface is small. A
JSON-per-locale dict + ``t(key, **kwargs)`` covers what we need with
no extra deps.

Conventions:
- Keys are dotted namespaces mirroring the SPA where possible -
  ``login.sign_in``, ``share_detail.edit_expiry``, ``common.ok``.
- Values are Python ``str.format``-style templates: ``"Hello {name}"``.
- Missing key in the active locale → fall back to English. Missing in
  English → return the key (loud failure mode; tests pin against it).
- Boot order: ``set_locale(code)`` once at startup with the user's
  ``users.locale`` from ``/me``; subsequent calls flip locale +
  reload. The Settings dialog re-packs after a swap so labels update.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("fileheron_client.i18n")

_DEFAULT_LOCALE = "en"
_SUPPORTED = ("en", "de")

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# Module-global state - the UI is single-threaded under Tk so a global
# is the simplest dispatch. Re-entrancy isn't a concern; the only
# writer is set_locale, called from the Tk main thread.
_active: str = _DEFAULT_LOCALE
_active_table: dict[str, Any] = {}
_fallback_table: dict[str, Any] = {}


def _load(code: str) -> dict[str, Any]:
    path = _LOCALES_DIR / f"{code}.json"
    if not path.is_file():
        _log.warning("locale file missing: %s - falling back to %s",
                     path, _DEFAULT_LOCALE)
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log.exception("locale parse failed: %s: %s", path, e)
        return {}


def set_locale(code: str) -> None:
    """Switch the active locale. Idempotent. Loads the fallback (en)
    table once; reloads the active table every call so a re-set
    re-reads from disk (useful for dev iteration)."""
    global _active, _active_table, _fallback_table
    code = (code or "").lower()
    if code not in _SUPPORTED:
        _log.info("unsupported locale %r - using %s", code, _DEFAULT_LOCALE)
        code = _DEFAULT_LOCALE
    _active = code
    _active_table = _load(code)
    if not _fallback_table:
        _fallback_table = _load(_DEFAULT_LOCALE)


def get_locale() -> str:
    return _active


def _lookup(table: dict[str, Any], dotted: str) -> Any:
    """Walk a dotted key through a nested dict. Returns None on miss."""
    node: Any = table
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def has(key: str) -> bool:
    """Whether `key` resolves in the ACTIVE locale (not the fallback).

    Used by ApiError.localized: an unknown backend code must fall through to
    the server's own text rather than render a raw key."""
    if _active_table is None:
        set_locale(_active)
    found = _lookup(_active_table or {}, key)
    if found is None:
        found = _lookup(_fallback_table or {}, key)
    return isinstance(found, str)


def t(key: str, **kwargs: Any) -> str:
    """Resolve ``key`` (dotted) through the active locale, falling
    back to English on miss. ``kwargs`` are passed to ``str.format``.

    A missing English key returns the key itself (with a log line) so
    bugs show up loudly rather than silently rendering empty
    strings. ``tests/test_i18n.py`` asserts every t() callsite has a
    matching key, so this should only fire during dev iteration.
    """
    val = _lookup(_active_table, key)
    if val is None:
        val = _lookup(_fallback_table, key)
    if val is None:
        _log.warning("i18n: missing key %r in both %s and %s",
                     key, _active, _DEFAULT_LOCALE)
        return key
    if not isinstance(val, str):
        _log.warning("i18n: key %r resolved to non-str %r", key, type(val))
        return key
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError) as e:
            _log.warning("i18n: format failed for %r (%s): %s", key, val, e)
            return val
    return val
