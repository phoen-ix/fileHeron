"""`.fh-field` stacks its label, control and help text: global.css makes it a
flex column. Three admin pages (Maintenance mode, Email templates, Inbound
mail) re-declared it `display: block` in their scoped styles, so on those
pages the label, the input at its default width and the help text ran
together on one line, and the email subject was cut off after ~20 characters.

Checked over EVERY .vue file, here rather than in vitest, for the reason
test_frontend_table_layout.py gives: vitest serves a stylesheet as an empty
string.
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
