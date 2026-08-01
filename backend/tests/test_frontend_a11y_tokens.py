"""Keyboard and screen-reader affordances that were measured to be missing.

From audit #2:
 - the app-wide focus ring was a 35% wash of the accent, which composites to
   1.63:1 on the warm paper background - below the 3:1 WCAG 1.4.11 floor for a
   non-text indicator, on every control on every page;
 - the share and SSO tables set `outline: none` on focus and relied on a
   background from `--fh-hover`, a custom property defined nowhere, so focus
   moved through 25 clickable rows with NO indicator at all and Enter opened
   whichever row happened to hold it;
 - `--fh-rule` was likewise undefined and used for a border in twenty
   components, so `border-color` fell back to its initial `currentColor`;
 - every runtime tunable on /admin/settings/advanced was a control with no
   accessible name, and the boolean was wrapped in an EMPTY <label> - the one
   shape that satisfies `form-control-has-label` while conveying nothing.

These live in the backend suite because that is where this repo already keeps
its cross-language structural checks (see test_error_log_path_redaction.py):
vitest strips CSS to an empty string, so a frontend test cannot read a token
file at all, and contrast is not observable from a mounted component.
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
TOKENS = (FRONTEND / "styles" / "tokens.css").read_text()


def _srgb_to_linear(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", hex_colour.strip())
    assert m, f"not a 6-digit hex colour: {hex_colour}"
    n = int(m.group(1), 16)
    r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
    return (
        0.2126 * _srgb_to_linear(r)
        + 0.7152 * _srgb_to_linear(g)
        + 0.0722 * _srgb_to_linear(b)
    )


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _token(name: str) -> str:
    m = re.search(rf"--{name}:\s*([^;]+);", TOKENS)
    assert m, f"token --{name} is not defined"
    return m.group(1).strip()


@pytest.mark.parametrize("surface", ["fh-paper", "fh-paper-raised", "fh-paper-sunk"])
def test_the_focus_ring_meets_the_wcag_non_text_floor(surface):
    ring = _token("fh-focus-ring")
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", ring), (
        "a translucent ring composites to less than it looks; state it solid"
    )
    assert _contrast(ring, _token(surface)) >= 3.0


def test_the_control_that_gives_the_measurement_meaning():
    """The value that shipped, composited by hand: 0.35 * #b45309 over #faf8f3."""
    assert _contrast("#e2bea1", "#faf8f3") < 3.0


@pytest.mark.parametrize(
    ("view", "table"),
    [("ShareList.vue", "share-table"), ("AdminSettingsSSOList.vue", "provider-table")],
)
def test_a_focusable_row_keeps_its_outline(view, table):
    src = (FRONTEND / "views" / view).read_text()
    blocks = re.findall(rf"\.{table} tbody tr:focus-visible\s*\{{[^}}]*\}}", src)
    assert blocks, f"{view} has no :focus-visible rule for its rows"
    for block in blocks:
        assert "outline: none" not in block, f"{view}: focus ring removed"
        assert "outline: 2px solid" in block, f"{view}: no visible ring"


def test_every_referenced_design_token_exists():
    """Widened past the two row views on purpose: `--fh-hover` was not the only
    one."""
    missing: set[str] = set()
    for path in list(FRONTEND.rglob("*.vue")) + list(FRONTEND.rglob("*.css")):
        for name in re.findall(r"var\((--fh-[a-z0-9-]+)[,)]", path.read_text()):
            if f"{name}:" not in TOKENS:
                missing.add(f"{name} ({path.name})")
    assert missing == set()


def test_every_advanced_tunable_has_an_accessible_name():
    src = (FRONTEND / "views" / "AdminSettingsAdvanced.vue").read_text()
    assert re.search(r"<label class=\"field-label\" :for=\"`tunable-\$\{it\.key\}`\"", src)
    controls = re.findall(r"<input\b.*?/>", src, re.S)
    assert len(controls) >= 3
    for control in controls:
        assert "`tunable-${it.key}`" in control, "a tunable control with no id to label"


def test_no_control_is_wrapped_in_an_empty_label():
    src = (FRONTEND / "views" / "AdminSettingsAdvanced.vue").read_text()
    assert not re.search(r"<label[^>]*class=\"switch\"", src)
