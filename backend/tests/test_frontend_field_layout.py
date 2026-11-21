"""`.fh-field` stacks its label, control and help text: global.css makes it a
flex column. Three admin pages (Maintenance mode, Email templates, Inbound
mail) re-declared it `display: block` in their scoped styles, so on those
pages the label, the input at its default width and the help text ran
together on one line, and the email subject was cut off after ~20 characters.

Checked over EVERY .vue file, here rather than in vitest, for the reason
test_frontend_table_layout.py gives: vitest serves a stylesheet as an empty
string.

The second half is the field's CLICK TARGET (2026-11-21). A `<label
class="fh-field">` wraps label text, control and help text, and a click
anywhere in a label focuses its control: a click on the help text, or in the
empty space beside the label, jumped into the field - and on the recipient
field opened its list. Two parts keep the target to the label's words and the
control: `.fh-field-label` is as wide as its text (global.css), and
`utils/fieldLabels.ts`, installed in main.ts, cancels the jump for a click on
the label's own box or on its help text - for a wrapping label of any class. The behaviour itself is tested in
frontend/tests/utils/fieldLabels.test.ts and, with a real mouse on every
page, by the e2e route sweeps (`expectPreciseFieldLabels`).
"""
from __future__ import annotations

import pathlib
import re

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
VUE = sorted(FRONTEND.rglob("*.vue"))
GLOBAL_CSS = (FRONTEND / "styles" / "global.css").read_text()

# A rule whose selector list has a bare `.fh-field` (not `.fh-field-input`,
# not a descendant like `.x .fh-field`), and its body.
_RULE = re.compile(r"(?:^|[},])\s*\.fh-field\s*(?:,[^{]*)?\{([^}]*)\}", re.M)


def _display_overrides(src: str) -> int:
    at = src.find("<style")
    style = "" if at == -1 else src[at:]
    return sum(bool(re.search(r"(?<![-\w])display\s*:", m.group(1))) for m in _RULE.finditer(style))


def test_global_css_stacks_a_field():
    body = next(m.group(1) for m in _RULE.finditer(GLOBAL_CSS))
    assert re.search(r"display:\s*flex", body) and re.search(r"flex-direction:\s*column", body)


def test_no_view_overrides_how_a_field_is_laid_out():
    offenders = [str(p.relative_to(FRONTEND)) for p in VUE if _display_overrides(p.read_text())]
    assert offenders == []
    fields = sum(p.read_text().count('class="fh-field"') for p in VUE)
    assert fields >= 50, f"found only {fields} fields - is the glob still finding the views?"


def test_the_scan_catches_what_it_is_for():
    def style(rule: str) -> str:
        return f"<template></template>\n<style scoped>\n{rule}\n</style>"

    assert _display_overrides(style("  .fh-field {\n    display: block;\n  }")) == 1
    assert _display_overrides(style(".a { color: red; }\n.fh-field, .b { display: grid; }")) == 1
    assert _display_overrides(style(".fh-field {\n  margin-bottom: 1rem;\n}")) == 0
    assert _display_overrides(style(".fh-field-input { display: block; }")) == 0
    assert _display_overrides(style(".row .fh-field { display: block; }")) == 0


# --- the click target --------------------------------------------------------

_ANY_RULE = re.compile(r"([^{}]+)\{([^}]*)\}")
_SIZING = re.compile(r"(?<![-\w])(?:width|min-width|display|flex|flex-grow|align-self)\s*:")


def _label_rules(css: str) -> list[str]:
    """Bodies of every rule that styles the label itself: one of its selectors
    ENDS in `.fh-field-label`, however it is qualified (`.row .fh-field-label`
    stretches it just as well as the bare class)."""
    return [
        m.group(2)
        for m in _ANY_RULE.finditer(css)
        if any(re.search(r"\.fh-field-label\s*$", sel) for sel in m.group(1).split(","))
    ]


def _label_sizing_overrides(src: str) -> int:
    at = src.find("<style")
    style = "" if at == -1 else src[at:]
    return sum(bool(_SIZING.search(body)) for body in _label_rules(style))


def test_a_field_label_is_as_wide_as_its_words():
    bodies = _label_rules(GLOBAL_CSS)
    assert len(bodies) == 1, "one rule sizes the label; a second one can undo it"
    assert re.search(r"(?<![-\w])width:\s*fit-content", bodies[0])


def test_no_view_stretches_a_field_label_again():
    offenders = [
        str(p.relative_to(FRONTEND)) for p in VUE if _label_sizing_overrides(p.read_text())
    ]
    assert offenders == []
    labels = sum(p.read_text().count("fh-field-label") for p in VUE)
    assert labels >= 100, f"found only {labels} labels - is the glob still finding the views?"


def test_the_click_guard_is_installed_and_names_the_field():
    main = (FRONTEND / "main.ts").read_text()
    assert re.search(r"^installFieldLabelGuard\(\)", main, re.M)
    guard = (FRONTEND / "utils" / "fieldLabels.ts").read_text()
    # Any wrapping label, whatever its class; the help text by the class the
    # markup gives it.
    assert "closest('label')" in guard and ".fh-field-help" in guard
    assert "preventDefault()" in guard


def test_the_label_scan_catches_what_it_is_for():
    def style(rule: str) -> str:
        return f"<template></template>\n<style scoped>\n{rule}\n</style>"

    assert _label_sizing_overrides(style(".fh-field-label {\n  width: 100%;\n}")) == 1
    assert _label_sizing_overrides(style(".a { color: red; }\n.fh-field-label, .b { display: block; }")) == 1
    assert _label_sizing_overrides(style(".fh-field-label {\n  color: red;\n  border-width: 1px;\n}")) == 0
    assert _label_sizing_overrides(style(".row .fh-field-label { width: 100%; }")) == 1
    assert _label_sizing_overrides(style(".fh-field-label .hint { width: 100%; }")) == 0
