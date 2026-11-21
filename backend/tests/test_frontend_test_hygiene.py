"""A test step a user would not perform is a product finding, never a test fix.

The recipient picker could not take a second recipient from its first release
until 2026-11-20: a pick closed its list and only a fresh focus reopened it.
Tests met that three times and were bent around it instead of failing:

- the Playwright `pick` helper pressed ArrowDown in a retry loop until the
  option showed (2026-10-30), under a comment describing the bug as how the
  widget works;
- the docs tour blurred the field and waited 300 ms between two picks;
- the vitest typing helper fired a synthetic `focus` before every step - an
  event a browser does not fire on a field that already has the focus.

So this scans EVERY Playwright file and EVERY vitest file, not a list of
today's, for the shapes those took. An entry in an allowlist names its reason
and its count; a use that goes away must leave the list too.

Here rather than in the suites themselves for the reason
test_frontend_table_layout.py gives for its own scan: one place that reads
both trees, run by the gate every change already passes.

The use cases (`e2e/tests/usecases/`) carry a rule of their own. They exist
because the other specs set up through the API and often checked through it
too, so a chain in which one person hands something to another stopped at the
second person: an account the admin made in the UI was "signed in" with an API
call, a forced password reset ended at "a token is shown" - and the admin was
being handed a bare token with no link (2026-11-21). A use-case spec therefore
reaches the server only through somebody's browser; the background facts it is
not about come from `given` in its `lib.ts`, the one file that calls the API.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
E2E = ROOT / "e2e"
UNIT = ROOT / "frontend" / "tests"

E2E_FILES = sorted(
    p
    for p in E2E.rglob("*.ts")
    if "node_modules" not in p.parts and p.suffixes[-2:] != [".d", ".ts"]
)
UNIT_FILES = sorted(UNIT.rglob("*.ts"))
# The one place that may move the focus: it moves the REAL one.
USER_HELPER = UNIT / "helpers" / "user.ts"

_ACTION = re.compile(
    r"\.(?:click|dblclick|press|fill|type|check|uncheck|selectOption|blur|focus|hover|tap"
    r"|setInputFiles|pressSequentially)\(|\bkeyboard\.|\bmouse\."
)
_RETRY_BLOCK = re.compile(r"expect\(\s*async\s*\(\)\s*=>\s*\{(.*?)\}\s*\)\s*\.toPass\(", re.S)

E2E_RULES: dict[str, re.Pattern[str]] = {
    "blur": re.compile(r"\.blur\(\)"),
    "wait": re.compile(r"\bwaitForTimeout\("),
    "force": re.compile(r"\bforce:\s*true"),
    "dispatch": re.compile(r"\.dispatchEvent\("),
}
UNIT_RULES: dict[str, re.Pattern[str]] = {
    "focus-event": re.compile(r"trigger\(\s*['\"](?:focus|blur|focusin|focusout)['\"]"),
    "focus-ctor": re.compile(r"new FocusEvent\("),
}

USECASES = E2E / "tests" / "usecases"
USECASE_SPECS = sorted(USECASES.glob("*.spec.ts"))
USECASE_RULES: dict[str, re.Pattern[str]] = {
    # Every helper in e2e/ that talks to the API or the containers directly.
    "api-call": re.compile(
        r"(?<![.\w])(?:fetch|apiFetch|apiLogin|api|ok|tokenOf|admin|adminToken|py|tokenFromStdout"
        r"|createShare|uploadFile|newUser|setSettings|restoreSettings|connect|group)\("
    ),
    "request-context": re.compile(r"\.request\b"),
}
# A use case is written against its own helpers and nothing else.
USECASE_IMPORTS = {"./lib", "@playwright/test"}
_IMPORT_FROM = re.compile(r"^import\b[^;]*?\bfrom\s+['\"]([^'\"]+)['\"]", re.M)

# (file relative to the repo, rule) -> (count, why it is not a workaround).
E2E_ALLOWED: dict[tuple[str, str], tuple[int, str]] = {
    ("e2e/tests/admin/webhooks.spec.ts", "retry-action"): (
        1,
        "re-opens the deliveries panel to re-read SERVER state: the delivery is "
        "made by the worker, and the panel loads once when opened",
    ),
    ("e2e/tests/legal-page-xss.spec.ts", "wait"): (
        1,
        "waits for a script that must NOT run; there is no event to wait for",
    ),
}
UNIT_ALLOWED: dict[tuple[str, str], tuple[int, str]] = {}


def _e2e_findings(src: str) -> dict[str, int]:
    found = {name: len(rule.findall(src)) for name, rule in E2E_RULES.items()}
    found["retry-action"] = sum(bool(_ACTION.search(m.group(1))) for m in _RETRY_BLOCK.finditer(src))
    return {k: v for k, v in found.items() if v}


def _unit_findings(src: str) -> dict[str, int]:
    found = {name: len(rule.findall(src)) for name, rule in UNIT_RULES.items()}
    return {k: v for k, v in found.items() if v}


def _usecase_findings(src: str) -> dict[str, int]:
    found = {name: len(rule.findall(src)) for name, rule in USECASE_RULES.items()}
    return {k: v for k, v in found.items() if v}


def _scan(files, findings) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for p in files:
        for rule, n in findings(p.read_text()).items():
            out[(str(p.relative_to(ROOT)), rule)] = n
    return out


def test_no_playwright_step_a_user_would_not_take():
    found = _scan(E2E_FILES, _e2e_findings)
    assert found == {k: n for k, (n, _why) in E2E_ALLOWED.items()}
    assert len(E2E_FILES) >= 40, f"scanned only {len(E2E_FILES)} files - is the glob still right?"


def test_no_vitest_file_fires_a_focus_the_browser_would_not():
    found = _scan([p for p in UNIT_FILES if p != USER_HELPER], _unit_findings)
    assert found == {k: n for k, (n, _why) in UNIT_ALLOWED.items()}
    assert len(UNIT_FILES) >= 100, f"scanned only {len(UNIT_FILES)} files - is the glob still right?"


def test_a_use_case_reaches_the_server_only_through_a_browser():
    assert len(USECASE_SPECS) >= 4, f"found only {len(USECASE_SPECS)} use-case specs - is the glob still right?"
    assert _scan(USECASE_SPECS, _usecase_findings) == {}
    for p in USECASE_SPECS:
        imported = set(_IMPORT_FROM.findall(p.read_text()))
        assert imported and imported <= USECASE_IMPORTS, (p.name, imported)


def test_the_helper_moves_the_real_focus():
    src = USER_HELPER.read_text()
    assert ".focus()" in src and ".blur()" in src
    assert "new FocusEvent(" not in src


def test_every_allowlisted_use_has_a_reason():
    for allowed in (E2E_ALLOWED, UNIT_ALLOWED):
        for key, (n, why) in allowed.items():
            assert n > 0 and len(why) > 20, key


def test_the_scan_catches_what_it_is_for():
    # The three shapes the picker's bug was hidden behind, verbatim.
    arrow_down = """
  await expect(async () => {
    if (!(await opt.isVisible())) await box.press('ArrowDown')
    await expect(opt).toBeVisible({ timeout: 1_000 })
  }).toPass()
"""
    assert _e2e_findings(arrow_down) == {"retry-action": 1}
    assert _e2e_findings("await picker.blur()\nawait page.waitForTimeout(300)\n") == {
        "blur": 1,
        "wait": 1,
    }
    assert _unit_findings("await input.trigger('focus')\nawait input.setValue(text)") == {
        "focus-event": 1
    }
    assert _unit_findings("el.dispatchEvent(new FocusEvent('blur'))") == {"focus-ctor": 1}
    assert _e2e_findings("await row.click({ force: true })") == {"force": 1}

    # What the use cases replaced: the outcome read from the API, the account
    # signed in through it, the newest token taken from the log.
    assert _usecase_findings("const me = await fetch(`${BASE}/api/account/me`)") == {"api-call": 1}
    assert _usecase_findings("const t = await apiLogin(email, password)") == {"api-call": 1}
    assert _usecase_findings("const token = tokenFromStdout('reset')") == {"api-call": 1}
    assert _usecase_findings("await page.request.get('/api/shares')") == {"request-context": 1}
    assert _usecase_findings("const u = await newUser('client', 'x')") == {"api-call": 1}
    # ... and what a use case is made of.
    browser_only = """
  const person = await given.user('client', 'x')
  const adminPage = await browserOf(browser, THE_ADMIN)
  await confirm(adminPage)
  await given.connected(a, b)
  expect(await asTokenHolder(token)).toEqual({ status: 200 })
"""
    assert _usecase_findings(browser_only) == {}
    two_imports = "import {\n  a,\n  b,\n} from './lib'\nimport type { Page } from '@playwright/test'\n"
    assert _IMPORT_FROM.findall(two_imports) == ["./lib", "@playwright/test"]

    # A retry loop that only LOOKS is polling, not a workaround.
    polling = """
  await expect(async () => {
    expect(await page.locator('.row').count()).toBe(3)
  }).toPass({ timeout: 30_000 })
"""
    assert _e2e_findings(polling) == {}
    assert _unit_findings("await button.trigger('click')") == {}
