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
        ring = re.search(r"outline:\s*2px solid\s+([^;]+);", block)
        assert ring, f"{view}: no visible ring"
        # `outline: 2px solid transparent` satisfied the old substring check and
        # is a ring nobody can see (audit #2 cross-check, MUT-6). Resolve the
        # colour and hold it to the same non-text floor as the ring token.
        colour = ring.group(1).strip()
        token = re.fullmatch(r"var\((--fh-[a-z0-9-]+)\)", colour)
        resolved = _token(token.group(1)[2:]) if token else colour
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", resolved), (
            f"{view}: focus ring is {colour!r}, which is not a solid colour"
        )
        assert _contrast(resolved, _token("fh-paper-raised")) >= 3.0


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


# --- audit #2: named controls, headings, live regions -----------------------


def _controls(src: str) -> list[str]:
    """Opening tags of every form control in a template."""
    return re.findall(r"<(?:input|select|textarea)\b[^>]*>", src, re.S)


def test_every_form_control_has_an_accessible_name():
    """38 had none - 11 of them `<select>`, which has no placeholder to fall
    back on, so a screen-reader admin heard "combo box, collapsed, All" with no
    idea what it filtered; one was the config-backup FILE input, which they
    could not tell from the passphrase field beside it (audit #2).

    Not an eslint gate: most of these names are BOUND (`:aria-label="t(...)"`)
    and `vuejs-accessibility/form-control-has-label` only recognises a static
    attribute. This check understands both, and a wrapping or `for`-associated
    <label>.
    """
    offenders: list[str] = []
    for path in list(FRONTEND.rglob("*.vue")):
        src = path.read_text()
        # Between the FIRST <template> and the LAST </template>. Partitioning on
        # the opening tag alone swept the <script> block in too, and matched an
        # `<input>` written inside a comment there.
        start = src.find("<template>")
        end = src.rfind("</template>")
        if start == -1 or end == -1:
            continue
        template = src[start:end]
        for tag in _controls(template):
            if re.search(r'\baria-label\b|\baria-labelledby\b|:aria-label\b', tag):
                continue
            if 'type="hidden"' in tag or "v-model" not in tag and "type=" not in tag:
                continue
            # `<label>` wrapping this control, or pointing at its id.
            m = re.search(r':?id="([^"]+)"', tag)
            if m and f'for="{m.group(1)}"' in template:
                continue
            if m and f':for="{m.group(1)}"' in template:
                continue
            idx = template.find(tag)
            before = template[max(0, idx - 400) : idx]
            if "<label" in before and "</label>" not in before.split("<label")[-1]:
                continue
            offenders.append(f"{path.name}: {tag[:70].strip()}")
    assert offenders == [], (
        f"{len(offenders)} form control(s) with no accessible name: {offenders[:8]}"
    )


def test_every_page_view_renders_a_page_heading():
    """30 of 55 views had no <h1> and 12 had no heading element at all, so a
    screen-reader admin landing on /admin/error-log heard "no headings" and had
    nothing to distinguish it from /admin/mail-log."""
    views = FRONTEND / "views"
    missing = [
        p.name
        for p in sorted(views.rglob("*.vue"))
        if "<h1" not in p.read_text().partition("<template>")[2]
        and p.name not in {"AdminLayout.vue", "HomePlaceholder.vue", "NotFound.vue"}
    ]
    assert missing == [], f"views with no page heading: {missing}"


def test_error_notices_are_live_regions():
    """58 of 67 were not, so a failed public-link unlock, 2FA setup or password
    change was announced to nobody - and on the public link page a few silent
    retries trip the brute-force counter, locking every OTHER recipient out of
    that share."""
    offenders: list[str] = []
    for path in list(FRONTEND.rglob("*.vue")):
        src = path.read_text()
        for tag in re.findall(r"<(?:div|p)\b[^>]*data-tone=\"(?:error|danger)\"[^>]*>", src, re.S):
            if "role=" not in tag and "aria-live" not in tag:
                offenders.append(f"{path.name}: {tag[:60].strip()}")
    assert offenders == [], f"{len(offenders)} error notice(s) announced to nobody"


def test_toggle_groups_expose_their_selected_state():
    """The header LanguageSwitcher does it right; its two duplicates and the
    expiry presets did not, so a blind user could not tell which option was
    active before or after activating one."""
    for rel in (
        "views/Account.vue",
        "views/RegisterFromInvite.vue",
        "components/ExpiryPicker.vue",
    ):
        assert "aria-pressed" in (FRONTEND / rel).read_text(), rel


def test_the_recipient_picker_honours_the_combobox_contract():
    """Arrow-key navigation on the primary share flow was silent: no
    aria-expanded, so the list opening was unannounced, and no
    aria-activedescendant, so no keystroke said anything."""
    src = (FRONTEND / "components" / "RecipientPicker.vue").read_text()
    for attr in ('role="combobox"', "aria-expanded", "aria-activedescendant", "aria-controls"):
        assert attr in src, attr

    # The vocabulary being present says nothing about it pointing anywhere: an
    # aria-activedescendant naming an id no element has is announced as
    # silence, exactly like having no attribute at all (audit #2 cross-check,
    # MUT-5). Compare the id TEMPLATE the computed builds against the one the
    # option rows bind.
    active = re.search(r"activeOptionId = computed\((.*?)\)\n", src, re.S)
    assert active, "activeOptionId moved; this test no longer sees it"
    active_tpl = re.search(r"`\$\{inputId\}(-opt-)\$\{", active.group(1))
    assert active_tpl, "activeOptionId no longer builds an option id"
    option_ids = re.findall(r':id="`\$\{inputId\}(-opt-)\$\{', src)
    assert option_ids, "the option rows no longer mint ids"
    assert all(o == active_tpl.group(1) for o in option_ids), (
        "aria-activedescendant points at an id pattern no option row uses"
    )

    # And the listbox the combobox controls has to be the one it names.
    controls = re.search(r':aria-controls="`\$\{inputId\}([a-z-]+)`"', src)
    assert controls, "aria-controls is not bound to a minted id"
    assert f':id="`${{inputId}}{controls.group(1)}`"' in src, (
        "aria-controls names a listbox id no element has"
    )


def test_placeholders_are_legible():
    """Placeholder text measured 2.39:1, so the filter bar on /admin/mail-log
    was three empty gaps to a low-vision admin - and on /account the user's own
    address is shown ONLY as a placeholder.

    Reads the RULE and resolves whichever token it names. Asserting on
    `--fh-subtle` instead checked a token the fix never touched: the whole
    pre-fix frontend satisfied it, and swapping the rule back to
    `--fh-subtle-soft` left every backend and vitest test green (audit #2
    cross-check, MUT-1).
    """
    css = (FRONTEND / "styles" / "global.css").read_text()
    block = re.search(r"\.fh-field-input::placeholder\s*\{(.*?)\}", css, re.S)
    assert block, "the placeholder rule moved; this test no longer sees it"
    named = re.search(r"color:\s*var\((--fh-[a-z0-9-]+)\)", block.group(1))
    assert named, "the placeholder colour is no longer a design token"
    assert _contrast(_token(named.group(1)[2:]), _token("fh-paper")) >= 4.5


def test_field_edges_are_visible():
    """The input boundary measured 1.44:1 - a field a low-vision user cannot
    see the extent of."""
    assert _contrast(_token("fh-field-edge"), _token("fh-paper")) >= 3.0
