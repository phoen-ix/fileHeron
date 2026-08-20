"""Every shipped email template must exist, compile and render - in every locale.

`release_available.html.j2` was DEAD in both locales. It declared `{% block body %}`,
which the layout does not render, and no `{% block subject %}`, which the layout
invoked as `{{ self.subject() }}` - so it raised UndefinedError, `render_email`'s bare
`except Exception: html = None` swallowed it, and the mail shipped text-only, logging
nothing. Nobody noticed because NOTHING enumerated the template directory: of the
fifteen slugs that shipped an `.html.j2`, exactly two (share_created,
share_files_added) had any assertion on their HTML output at all.

So this is a matrix, driven by `subjects.json` - the same file services/email.py loads
at import time - and never by a hand-written list, because a hand-written list is how
the next slug gets missed. The repo has been bitten by that twice already
(frontend/tests/i18n/notif-categories.test.ts, and the NotificationCategory drift).

Templates are addressed by their FULL locale path, never through render_email alone.
`_render` falls back to `en/<slug>.<kind>.j2` when the locale's own file is missing,
so a German template that does not exist renders the English one and `render_email`
reports success - the failure is invisible from the outside.
"""
from __future__ import annotations

import json
import re

import pytest
from jinja2 import TemplateNotFound, nodes
from jinja2.meta import find_undeclared_variables

from app.services import email as email_svc
from app.services import email_placeholders as ep
from app.services import mail_log
from app.utils.timeutil import utc_now

_ROOT = email_svc._TEMPLATE_ROOT
_LOCALES = sorted(email_svc._LOCALE_CODES)   # Locale-derived, not a literal
_KINDS = ("txt", "html")
_APP_URL = "https://mail.test"
_APP_NAME = "fileHeron"
_TZ = "Europe/Vienna"

SLUGS: list[str] = sorted(json.loads((_ROOT / "en" / "subjects.json").read_text("utf-8")))

# Names _render and the layout inject, so a template may use them without the
# caller's ctx carrying them.
_INJECTED = {
    "app_name", "app_url", "locale", "site_timezone", "self", "ui",
    "manage_subscriptions_url", "unsubscribe_url", "brand_logo_url",
}

# The five slugs that are NOT admin-editable and so are absent from
# email_placeholders.REGISTRY. Kept here rather than pushed into REGISTRY:
# membership there IS `is_editable()`, which gates _load_override and every
# /admin/settings/email-templates route, so adding them would hand server_error and
# ops_alert to the template editor and break test_admin_email_templates's count - a
# product change made to satisfy a test.
_NON_EDITABLE_CTX: dict[str, dict] = {
    "password_changed": {},   # display_name / ip_hint / reset_url all in sample_ctx
    "smtp_test": {},          # `now` lives in sample_ctx
    "inbound_message": {"sender": "sender@example.test", "classification": "reply"},
    "ops_alert": {
        "reason": "cron_failed", "job_name": "expire_files", "type": "worker",
        "error": "TimeoutError: clamd did not answer", "detail": "attempt 3 of 3",
    },
    "server_error": {
        "source": "worker", "job_name": "expire_files",
        "status_code": 500, "code": "INTERNAL_ERROR",
        "exception_type": "OperationalError", "message": "database connection lost",
        "method": "POST", "path": "/api/shares", "ip": "203.0.113.42",
        "request_id": "01J8Z2Q5K7", "user_id": 7, "auth_via": "session",
        "occurrence_count": 4, "suppressed_count": 3, "suppressed_since": utc_now(),
    },
}


def _ctx(slug: str) -> dict:
    """Render context for one slug.

    The 21 editable slugs use the SAME sample context the admin preview and
    test-send use (routers/admin/email_templates.py), so "renders here" implies
    "renders in the preview". `app_name` is popped: _resolve_subject does
    `template.format(**ctx, app_name=...)` and a duplicate key raises TypeError,
    which its `except (KeyError, IndexError)` does not catch.
    """
    base = ep.sample_ctx(slug, app_url=_APP_URL)
    base.pop("app_name", None)
    base.update(_NON_EDITABLE_CTX.get(slug, {}))
    return base


def _path(locale: str, slug: str, kind: str):
    return _ROOT / locale / f"{slug}.{kind}.j2"


def _parse(locale: str, slug: str, kind: str):
    """Parse through the REAL env - it carries the `dt_locale` filter, and
    find_undeclared_variables runs the code generator, so a bare Environment fails
    with TemplateAssertionError('No filter named dt_locale')."""
    return email_svc._env.parse(
        _path(locale, slug, kind).read_text("utf-8"),
        name=f"{locale}/{slug}.{kind}.j2",
    )


def _blocks(ast) -> set[str]:
    return {n.name for n in ast.find_all(nodes.Block)}


_LAYOUT_AST = email_svc._env.parse((_ROOT / "layout.html.j2").read_text("utf-8"))
_LAYOUT_BLOCKS = _blocks(_LAYOUT_AST)

_ALL = [(loc, slug) for loc in _LOCALES for slug in SLUGS]
_IDS = [f"{loc}-{slug}" for loc, slug in _ALL]

_LEAK = re.compile(r"\{\{|\{%|\bUndefined\b|No caller defined")


# --- vacuity guards ---------------------------------------------------------


def test_the_matrix_is_not_empty():
    """If any of these hits zero, every assertion in this file is void."""
    assert len(SLUGS) >= 26, SLUGS
    assert set(_LOCALES) == {"en", "de"}, _LOCALES
    assert len(_ALL) == len(SLUGS) * len(_LOCALES)
    assert _LAYOUT_BLOCKS, "the layout parse found no blocks - the scan reads nothing"


def test_every_locale_declares_the_same_slugs():
    books = {
        loc: json.loads((_ROOT / loc / "subjects.json").read_text("utf-8"))
        for loc in _LOCALES
    }
    reference = set(books["en"])
    assert reference, "the EN subject book is empty"
    for loc, book in books.items():
        assert set(book) == reference, (
            f"{loc}/subjects.json diverges: missing={sorted(reference - set(book))} "
            f"extra={sorted(set(book) - reference)}"
        )


def test_no_template_file_is_orphaned():
    on_disk = {
        p.name.split(".")[0]
        for loc in _LOCALES
        for p in (_ROOT / loc).glob("*.j2")
    }
    assert on_disk, "the directory scan matched nothing"
    assert on_disk <= set(SLUGS), (
        f"template files with no subjects.json entry: {sorted(on_disk - set(SLUGS))}"
    )


# --- 1. both parts exist, in both locales -----------------------------------


@pytest.mark.parametrize("locale, slug", _ALL, ids=_IDS)
@pytest.mark.parametrize("kind", _KINDS)
def test_both_parts_exist_in_both_locales(locale, slug, kind):
    """The check that eleven slugs failed for the product's whole life.

    By FULL PATH, because _render falls back to `en/` when the locale's file is
    missing: going through render_email would let an absent de/ template pass while
    German recipients receive an English HTML part beside a German text part."""
    assert _path(locale, slug, kind).is_file(), f"missing {locale}/{slug}.{kind}.j2"


@pytest.mark.parametrize("locale, slug", _ALL, ids=_IDS)
@pytest.mark.parametrize("kind", _KINDS)
def test_every_template_compiles_in_its_own_locale(locale, slug, kind):
    """get_template COMPILES. _render's fallback used to catch bare Exception, so a
    TemplateSyntaxError in a de/ file was swallowed and the EN template rendered in
    its place - plausible wrong-language content that no reviewer would flag. Compile
    each file directly, where nothing can catch it."""
    try:
        email_svc._env.get_template(f"{locale}/{slug}.{kind}.j2")
    except TemplateNotFound:
        pytest.fail(f"{locale}/{slug}.{kind}.j2 not found")


# --- 2. structure -----------------------------------------------------------


@pytest.mark.parametrize("locale, slug", _ALL, ids=_IDS)
def test_every_html_template_extends_the_layout_and_fills_it(locale, slug):
    """THE release_available check, both halves: a block the layout never renders
    means the content is written and dropped on the floor, and no `content` block
    means the mail is an empty branded frame."""
    ast = _parse(locale, slug, "html")
    extends = [n.template.value for n in ast.find_all(nodes.Extends)]
    assert extends == ["layout.html.j2"], (
        f"{locale}/{slug}.html.j2 does not extend the shared layout: {extends}"
    )
    declared = _blocks(ast)
    stray = declared - _LAYOUT_BLOCKS
    assert not stray, (
        f"{locale}/{slug}.html.j2 declares {sorted(stray)}, which the layout never "
        f"renders - that content is silently discarded, which is exactly what "
        f"`{{% block body %}}` did to release_available"
    )
    assert "content" in declared, (
        f"{locale}/{slug}.html.j2 overrides no `content` block - the body would be "
        f"an empty branded frame"
    )


def test_the_block_scanner_actually_fires():
    """Negative control: a child shaped like the broken release_available must be
    reported by the same expression the check above uses."""
    probe = email_svc._env.parse(
        "{% extends 'layout.html.j2' %}{% block body %}x{% endblock %}",
        name="_probe.html.j2",
    )
    assert _blocks(probe) == {"body"}
    assert _blocks(probe) - _LAYOUT_BLOCKS == {"body"}, (
        "the probe satisfies the contract - the stray-block check proves nothing"
    )
    assert "content" not in _blocks(probe)


def test_the_layout_defaults_every_block_it_invokes():
    """The layout calls `{{ self.subject() }}`. It used to define no such block, so a
    child that forgot one raised UndefinedError from inside <title>. Whatever the
    layout invokes through `self`, it must also define."""
    invoked = {
        n.attr for n in _LAYOUT_AST.find_all(nodes.Getattr)
        if isinstance(n.node, nodes.Name) and n.node.name == "self"
    }
    assert invoked <= _LAYOUT_BLOCKS, (
        f"the layout invokes {sorted(invoked - _LAYOUT_BLOCKS)} through self without "
        f"defining it; any child that omits that block raises UndefinedError from "
        f"inside the layout, render_email swallows it, and the mail ships text-only"
    )
    assert "subject" in _LAYOUT_BLOCKS, (
        "the layout no longer defines a `subject` block - it is what gives <title> a "
        "default so a child that forgets one degrades instead of dying"
    )


# --- 3. context coverage ----------------------------------------------------


@pytest.mark.parametrize("locale, slug", _ALL, ids=_IDS)
@pytest.mark.parametrize("kind", _KINDS)
def test_every_referenced_variable_is_supplied(locale, slug, kind):
    """Jinja's default Undefined renders as the EMPTY STRING, so "the output has no
    literal Undefined" catches almost nothing - a template reaching for a key nobody
    passes renders a blank and looks fine. Stated as it means: every variable a
    template references must be one someone actually supplies."""
    referenced = find_undeclared_variables(_parse(locale, slug, kind)) - _INJECTED
    missing = sorted(referenced - set(_ctx(slug)))
    assert not missing, (
        f"{locale}/{slug}.{kind}.j2 references {missing}, which no sender and no test "
        f"context supplies - it renders as an empty string. Add it to "
        f"email_placeholders.sample_ctx (editable slugs) or _NON_EDITABLE_CTX here, "
        f"and to the sender that builds the ctx."
    )


def test_the_variable_scan_reads_something():
    total = sum(
        len(find_undeclared_variables(_parse(loc, slug, kind)))
        for loc, slug in _ALL for kind in _KINDS
    )
    assert total > 100, f"the AST scan found only {total} variable references"


# --- 4. output --------------------------------------------------------------


@pytest.mark.parametrize("locale, slug", _ALL, ids=_IDS)
def test_every_slug_renders_both_parts_in_every_locale(locale, slug):
    """The end-to-end assertion release_available needed and never had."""
    subject, text, html = email_svc.render_email(
        locale, slug, _ctx(slug),
        app_url=_APP_URL, app_name=_APP_NAME, site_timezone=_TZ,
    )

    assert subject.strip(), f"{locale}/{slug}: empty subject"
    assert "{" not in subject and "}" not in subject, (
        f"{locale}/{slug}: unsubstituted subject {subject!r}. _resolve_subject "
        f"catches KeyError and returns the RAW template, so a key the subject needs "
        f"but nobody passes ships to the recipient verbatim."
    )
    assert text.strip(), f"{locale}/{slug}: empty text part"
    assert html is not None, (
        f"{locale}/{slug}: render_email returned html=None - the template is missing "
        f"or it raised and the except in services/email.py swallowed it, which is "
        f"how release_available shipped text-only for its whole life."
    )
    assert html.strip(), f"{locale}/{slug}: empty html part"
    assert "<!doctype html" in html.lower(), f"{locale}/{slug}: not layout-wrapped"
    assert _APP_NAME in html, f"{locale}/{slug}: html part carries no app name"

    for name, part in (("text", text), ("html", html)):
        hit = _LEAK.search(part)
        assert hit is None, (
            f"{locale}/{slug} {name} part leaks template syntax at {hit.start()}: "
            f"{part[max(0, hit.start() - 60):hit.start() + 60]!r}"
        )


@pytest.mark.parametrize("slug", SLUGS)
def test_the_locales_render_from_their_own_files(slug):
    """Guards the EN fallback from the other side: two locales producing identical
    HTML means the de/ file was never reached, or is a verbatim copy of the English
    one - the same bug with extra steps."""
    outs = {
        loc: email_svc.render_email(
            loc, slug, _ctx(slug),
            app_url=_APP_URL, app_name=_APP_NAME, site_timezone=_TZ,
        )[2]
        for loc in _LOCALES
    }
    assert len(set(outs.values())) == len(_LOCALES), (
        f"{slug}: the locales render identical HTML - the de/ template is a copy of "
        f"the English one, or _render fell back to en/"
    )


# --- 5. the auth-link slugs get an HTML body for the FIRST time -------------

# slug -> the ctx keys whose value carries a one-time token. lockout_warning and
# email_change_alert's `reset_url` are absent on purpose: those point at the
# token-free /forgot-password page.
_TOKEN_KEYS = {
    "verify": ("verify_url",),
    "reset_password": ("reset_url",),
    "invite": ("register_url",),
    "email_change_confirm": ("confirm_url",),
    "email_change_verify_old": ("confirm_url", "cancel_url"),
    "email_change_alert": ("cancel_url",),
}
# base64url alphabet, matching utils/crypto.py::random_token.
_LIVE = "L1ve-T0ken_do_not_log.xyz"


@pytest.mark.parametrize("locale", _LOCALES)
@pytest.mark.parametrize("slug", sorted(_TOKEN_KEYS))
def test_a_live_token_never_survives_into_the_logged_html_body(locale, slug):
    """These six slugs shipped NO .html.j2, so `html` was None and
    email_log.body_html was NULL for them. Giving them one puts a live one-time
    token into the browsable admin mail log for the first time.

    mask_bodies does run mask_sensitive over BOTH parts, but _AUTH_LINK_RE only
    matches the canonical `/<path>/<token>` shape - so a template that wraps the
    link, URL-encodes the path segment, or breaks it up to make a 43-character
    token wrap would defeat masking silently, while `masked=True` (forced by the
    category) still claims it was handled. mask_sensitive fails closed only on an
    EXCEPTION; a non-match returns the body verbatim.
    """
    ctx = _ctx(slug)
    for key in _TOKEN_KEYS[slug]:
        ctx[key] = ctx[key].rsplit("/", 1)[0] + "/" + _LIVE

    _subject, text, html = email_svc.render_email(
        locale, slug, ctx, app_url=_APP_URL, app_name=_APP_NAME, site_timezone=_TZ,
    )
    assert html is not None
    assert _LIVE in html, (
        f"{locale}/{slug}: the auth link is not in the HTML body at all - the "
        f"recipient cannot act on the mail"
    )

    masked_text, masked_html, masked = mail_log.mask_bodies(text, html, slug)
    assert _LIVE not in masked_html, (
        f"{locale}/{slug}: a LIVE one-time token survives into email_log.body_html. "
        f"mail_log._AUTH_LINK_RE only matches /<path>/<token>; this template emits "
        f"the link in a shape it cannot see. That is account takeover for anyone "
        f"with read access to the admin mail log."
    )
    assert _LIVE not in masked_text
    assert masked is True, f"{locale}/{slug}: resend is not disabled for a token mail"


def test_the_masking_control_can_fail():
    """Negative control: the assertion above must be capable of failing, or it only
    proves that mask_bodies was called. A link broken up for wrapping is the exact
    shape a redesign would introduce."""
    body = f'<p>Copy this: {_APP_URL}/verify-<wbr>email/{_LIVE}</p>'
    _t, h, _m = mail_log.mask_bodies("", body, "verify")
    assert _LIVE in h, (
        "mask_bodies redacted a link whose path was broken up - then the "
        "shape-sensitivity this test guards against does not exist"
    )
