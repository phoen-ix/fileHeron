"""Every settings change the Overview's "Recently changed" panel can show links
to the page that owns it.

The panel lists audit rows filed as `target_type="settings"` (plus a task's
schedule) and maps each row's `target_id` - or, for a few events, the event
itself - to an admin page via frontend/src/config/adminSettingsChanges.ts. A new
settings route that audits under a target id the map does not know would show a
change linking nowhere, and a route that audits under another target type would
not show at all. Both directions are pinned GENERICALLY, by scanning every
`record_audit_event` call in app/, never by a list of today's routes.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType

_BACKEND = Path(__file__).resolve().parents[1]
APP = _BACKEND / "app"
TS = _BACKEND.parent / "frontend" / "src" / "config" / "adminSettingsChanges.ts"

# Settings audits whose target id is not a constant, and why that is fine.
DYNAMIC = {
    ("services/settings.py", "settings_changed"):
        "the tunables form: its row names the keys, linked through the search index",
    ("routers/admin/email_templates.py", "email_template_changed"):
        "target is `slug:locale`; EVENT_ROUTES sends the event to the templates page",
    ("routers/admin/email_templates.py", "email_template_reset"):
        "target is `slug:locale`; EVENT_ROUTES sends the event to the templates page",
    ("services/mail_test_gate.py", "event_type"):
        "a connection test, not a change - excluded from the panel by the route",
}
_SETTINGS_EVENT_SUFFIXES = ("_policy_changed", "_settings_changed", "_config_changed", "_toggled")


def _audit_calls():
    for path in sorted(APP.rglob("*.py")):
        rel = str(path.relative_to(APP))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != "record_audit_event":
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            event = kw.get("event_type")
            event_name = ast.unparse(event).replace("AuditEventType.", "") if event is not None else ""
            yield rel, node.lineno, event_name, kw.get("target_type"), kw.get("target_id")


def _ts_map(name: str) -> dict[str, str]:
    src = TS.read_text(encoding="utf-8")
    start = src.index(f"export const {name}")
    block = src[start : src.index("\n}\n", start)]
    return dict(re.findall(r"^\s+([a-z_]+): '([a-z0-9-]+)',", block, re.M))


@pytest.fixture(scope="module")
def calls():
    found = list(_audit_calls())
    assert len(found) > 100, f"the audit-call scan went vacuous ({len(found)})"
    return found


def _settings_calls(calls):
    return [c for c in calls if isinstance(c[3], ast.Constant) and c[3].value == "settings"]


def test_every_settings_target_links_to_a_page(calls):
    targets = _ts_map("SETTINGS_TARGET_ROUTES")
    assert len(targets) > 15, "the TS map scan went vacuous"
    settings_calls = _settings_calls(calls)
    assert len(settings_calls) >= 25, len(settings_calls)
    missing, undeclared = [], []
    for rel, line, event, _tt, target in settings_calls:
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            if target.value not in targets:
                missing.append(f"{rel}:{line} {event} target_id={target.value!r}")
        elif (rel, event) not in DYNAMIC:
            undeclared.append(f"{rel}:{line} {event}")
    assert not missing, (
        "settings audits whose target_id adminSettingsChanges.ts cannot link: "
        f"{missing} - add it to SETTINGS_TARGET_ROUTES"
    )
    assert not undeclared, (
        f"settings audits with a non-constant target_id: {undeclared} - "
        "declare them in DYNAMIC with the reason the panel can still place them"
    )


def test_every_map_entry_is_still_written(calls):
    written = {
        t.value for _r, _l, _e, _tt, t in _settings_calls(calls)
        if isinstance(t, ast.Constant)
    }
    stale = sorted(set(_ts_map("SETTINGS_TARGET_ROUTES")) - written)
    assert not stale, f"SETTINGS_TARGET_ROUTES keys no settings audit writes: {stale}"
    seen = {(rel, event) for rel, _l, event, _tt, _t in calls}
    gone = sorted(k for k in DYNAMIC if k not in seen)
    assert not gone, f"DYNAMIC declares calls that no longer exist: {gone}"


def test_event_routes_name_real_events():
    values = {e.value for e in AuditEventType}
    events = _ts_map("EVENT_ROUTES")
    assert events, "the EVENT_ROUTES scan went vacuous"
    assert set(events) <= values, sorted(set(events) - values)


def test_a_settings_change_is_filed_as_one(calls):
    """The other direction: a settings route, or an event named like a settings
    change, that audits under another target type never reaches the panel."""
    wrong = []
    for rel, line, event, target_type, _t in calls:
        is_settings_route = rel.startswith("routers/admin/settings/")
        named_like_one = event.endswith(_SETTINGS_EVENT_SUFFIXES)
        if not (is_settings_route or named_like_one):
            continue
        if not (isinstance(target_type, ast.Constant) and target_type.value == "settings"):
            wrong.append(f"{rel}:{line} {event} target_type={ast.unparse(target_type) if target_type else None}")
    assert not wrong, f"settings changes filed under another target type: {wrong}"
