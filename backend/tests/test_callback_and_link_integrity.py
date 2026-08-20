"""Four places where the user was handed something that could not work.

oidc-6    every OIDC callback failure answered a BROWSER redirect with a raw
          JSON error body on an /api/ URL: no navigation, no retry, and for
          OIDC_NO_ACCOUNT - the expected outcome for anyone the admin has not
          invited - no explanation either. The translations for all five codes
          already existed; nothing ever reached them.
flow-selfupdate-7 the release check surfaced suffixed tags (`v1.2.3-rc1`) as an
          available update, and the update endpoint validated `target_tag` with
          fullmatch - so the banner offered a version whose button answered an
          opaque 422 outside the standard error envelope.
config-12 `updates.check_mode` outlived the code that read it by four releases,
          and release_check's module docstring still described the cadence gate
          and 24h `_too_soon` guard that moved to the cron scheduler in v1.28.0.
comms-7   the manage-subscriptions footer is redacted at rest but resend stays
          allowed for it. Resend shipped the STORED body, so the recipient got a
          real email whose "Manage subscriptions" link pointed at
          /manage-notifications/<redacted>.
crypto-11 utils/crypto.py pointed rotation at scripts/rotate_totp_key.py, which
          has never existed, and described it as TOTP-only when the real script
          covers five Fernet columns. The rotation script's own SAFETY section
          claimed a transaction per table over code that commits once.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest
from pydantic import ValidationError

# --- oidc-6 -----------------------------------------------------------------


def test_a_failed_login_callback_redirects_into_the_spa():
    from app.routers import oidc as oidc_router

    src = inspect.getsource(oidc_router.callback)
    assert "except AppError" in src
    assert "_error_redirect" in src


def test_the_redirect_carries_the_code_and_lands_on_login(db):
    from app.routers.oidc import _error_redirect

    resp = _error_redirect(db, "OIDC_NO_ACCOUNT")
    assert resp.status_code == 302
    assert "/login?oidc_error=OIDC_NO_ACCOUNT" in resp.headers["location"]


def test_a_failed_connect_callback_lands_on_the_account_page():
    from app.routers import oidc_connect

    src = inspect.getsource(oidc_connect.connect_callback)
    assert "except AppError" in src
    assert "/account?oidc_error=" in src


@pytest.mark.parametrize(
    "code",
    [
        "OIDC_NO_ACCOUNT",
        "OIDC_STATE_MISMATCH",
        "OIDC_ALREADY_LINKED",
        "OIDC_EMAIL_MISMATCH",
        "OIDC_SUBJECT_TAKEN",
    ],
)
def test_every_callback_code_has_a_translation(code):
    """The redirect is only useful if the SPA has something to render."""
    import json

    root = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/i18n/locales"
    for locale in ("en", "de"):
        data = json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))
        assert code in data["errors"], f"{code} missing from {locale}.json"


@pytest.mark.parametrize("view", ["views/Login.vue", "components/OIDCConnectPanel.vue"])
def test_the_spa_reads_the_error_parameter(view):
    root = pathlib.Path(__file__).resolve().parents[2] / "frontend/src"
    src = (root / view).read_text(encoding="utf-8")
    assert "oidc_error" in src, f"{view} ignores the redirect's error code"
    assert "errors." in src


@pytest.mark.asyncio
async def test_an_unknown_identity_does_not_dead_end_the_browser(
    db, client, monkeypatch, make_provider
):
    """End to end: the callback for an identity with no local account answers a
    302 into the SPA, not a JSON body a browser cannot navigate away from."""
    from app.services import oidc as oidc_svc
    from tests._oidc_helpers import install_jwks_mock, make_claims, patch_exchange

    provider = make_provider()

    state = "s" * 24
    nonce = "n" * 24
    install_jwks_mock(monkeypatch)
    patch_exchange(
        monkeypatch,
        make_claims(
            provider, sub="nobody-here", email="stranger@example.com", nonce=nonce
        ),
    )

    r = await client.get(
        f"/api/auth/oidc/callback/{provider.id}",
        params={"code": "abc", "state": state},
        cookies={oidc_svc.STATE_COOKIE: f"{state}::{provider.id}::{nonce}"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "oidc_error=OIDC_NO_ACCOUNT" in r.headers["location"]


# --- flow-selfupdate-7 -------------------------------------------------------


def test_the_two_tag_patterns_are_one_object():
    from app.routers.admin.system import UpdateApplyRequest
    from app.services.release_check import RELEASE_TAG_RE

    src = inspect.getsource(UpdateApplyRequest._validate_target_tag.__func__)
    assert "RELEASE_TAG_RE" in src, "the update endpoint still has its own copy"
    assert RELEASE_TAG_RE.fullmatch("v1.2.3")


@pytest.mark.parametrize("tag", ["v1.2.3-rc1", "v1.2.3+build42", "v1.2", "latest"])
def test_a_tag_the_endpoint_would_refuse_is_never_offered(tag):
    from app.services.release_check import _select_backend_release

    assert _select_backend_release([{"tag_name": tag}]) is None


def test_a_real_release_is_still_selected():
    """Control: filtering harder must not stop updates being found."""
    from app.services.release_check import _select_backend_release

    got = _select_backend_release(
        [{"tag_name": "client-v1.1.0"}, {"tag_name": "v2.5.0", "html_url": "x"}]
    )
    assert got is not None and got["tag_name"] == "v2.5.0"


@pytest.mark.parametrize("flag", ["prerelease", "draft"])
def test_an_unfinished_release_is_skipped(flag):
    """The button pulls images and restarts the stack; a draft is not something
    to offer as THE update."""
    from app.services.release_check import _select_backend_release

    got = _select_backend_release(
        [{"tag_name": "v9.9.9", flag: True}, {"tag_name": "v2.5.0", "html_url": "x"}]
    )
    assert got is not None and got["tag_name"] == "v2.5.0"


def test_the_endpoint_accepts_what_the_check_offers():
    from app.routers.admin.system import UpdateApplyRequest

    payload = UpdateApplyRequest(password="x", target_tag="v2.5.0")
    assert payload.target_tag == "v2.5.0"
    with pytest.raises(ValidationError):
        UpdateApplyRequest(password="x", target_tag="v2.5.0-rc1")


def test_all_three_tag_call_sites_anchor_the_same_way():
    """RELEASE_TAG_RE carries no ``^``, so anchoring lives at the call site and
    a site reaching for ``match`` quietly re-accepts suffixes. The third one
    (`html_release_url_for_tag`) did, and minted changelog links for tags the
    update endpoint refuses."""
    from app.services import release_check

    src = inspect.getsource(release_check)
    assert "_BACKEND_TAG_RE.match(" not in src
    assert "RELEASE_TAG_RE.match(" not in src


def test_the_two_default_urls_are_one_object():
    """The settings router used to keep its own copy of the default updates URL,
    left pointing at `/releases/latest` when v1.1.8 moved the check to the list
    endpoint. The SPA prefills its input from that GET, so opening the page and
    pressing Save pinned the check to an endpoint that can never yield a backend
    release - the same error message, permanently."""
    # Points at the SUB-MODULE, not the `settings` package: the package's
    # __init__ only includes routers, so `hasattr(package, "_DEFAULT_...")`
    # would be trivially False and the negative assertion would pin nothing.
    from app.routers.admin.settings import home_motd_updates as admin_updates
    from app.services.release_check import DEFAULT_UPDATES_API_URL

    src = inspect.getsource(admin_updates.get_updates_settings)
    assert "DEFAULT_UPDATES_API_URL" in src
    assert not hasattr(admin_updates, "_DEFAULT_UPDATES_API_URL"), (
        "a second default is a second meaning; there is one constant"
    )
    # And it must be the LIST endpoint: /releases/latest returns GitHub's newest
    # release whatever its tag, which for this repo is a client-v* desktop tag.
    assert "/releases?" in DEFAULT_UPDATES_API_URL
    assert not DEFAULT_UPDATES_API_URL.endswith("/releases/latest")


@pytest.mark.parametrize("locale", ["en", "de"])
def test_the_url_placeholder_teaches_the_working_endpoint(locale):
    """The locale files carried a THIRD copy of the wrong URL, so the field the
    admin sees suggested the broken shape even once the fallback was fixed."""
    import json
    from pathlib import Path

    from app.services.release_check import DEFAULT_UPDATES_API_URL

    path = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "i18n" / "locales" / f"{locale}.json"
    )
    # Asserted, not skipped: a test that quietly skips when it cannot find its
    # subject pins nothing, and this one exists precisely because the copy it
    # checks drifted unnoticed.
    assert path.exists(), f"{path} not found - did the locale files move?"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["admin_updates"]["url_placeholder"] == DEFAULT_UPDATES_API_URL


# --- config-12 ---------------------------------------------------------------


def test_the_dead_setting_key_is_gone():
    from app.services import settings as settings_svc

    assert not hasattr(settings_svc.Keys, "UPDATES_CHECK_MODE"), (
        "a settings key nothing reads is a switch an operator will try to flip"
    )


def test_the_docstring_no_longer_describes_a_gate_that_was_removed():
    from app.services import release_check

    doc = release_check.__doc__ or ""
    assert "check_mode" not in doc or "cron scheduler" in doc
    assert "_too_soon" not in inspect.getsource(release_check)


# --- comms-7 -----------------------------------------------------------------


def test_a_redacted_footer_is_reminted_for_the_outgoing_copy():
    from app.services import mail_log

    body = "Hello\n\nManage: https://x.test/manage-notifications/<redacted>\n"
    out = mail_log.remint_footer(body, user_id=7)
    assert "<redacted>" not in out
    assert re.search(r"/manage-notifications/7\.\d+\.[A-Za-z0-9._~-]+", out), out


def test_an_unknown_recipient_loses_the_link_rather_than_keeping_a_dead_one():
    from app.services import mail_log

    body = "Manage: https://x.test/manage-notifications/<redacted>"
    out = mail_log.remint_footer(body, user_id=None)
    assert "<redacted>" not in out
    assert out.endswith("/manage-notifications/")


def test_an_ordinary_body_is_untouched():
    from app.services import mail_log

    body = "Your share expires soon. https://x.test/share/abc"
    assert mail_log.remint_footer(body, user_id=1) == body


def test_the_resend_route_sends_the_reminted_body():
    from app.routers.admin import mail as mail_router

    src = inspect.getsource(mail_router.resend_mail)
    assert "remint_footer" in src
    # The STORED row keeps the redacted form - re-minting must not put a live
    # token back into the browsable log.
    assert "body_text=orig.body_text" in src
    assert "text_body=out_text" in src


# --- crypto-11 ---------------------------------------------------------------


def test_the_rotation_script_named_in_the_docs_exists():
    from app.utils import crypto

    doc = crypto.__doc__ or ""
    root = pathlib.Path(__file__).resolve().parents[1] / "scripts"
    named = [
        n for n in re.findall(r"scripts/(\w+)\.py", doc)
        # The docstring records what the wrong path used to be; the claim under
        # test is the one it makes now.
        if f"used to be scripts/{n}.py" not in doc
    ]
    assert named, "no rotation script is named at all"
    for name in named:
        assert (root / f"{name}.py").exists(), f"scripts/{name}.py does not exist"


def test_the_token_length_in_the_docstring_is_the_real_one():
    from app.utils.crypto import random_token, refresh_token_hash

    doc = refresh_token_hash.__doc__ or ""
    m = re.search(r"64 raw bytes \((\d+) chars", doc)
    assert m, doc
    assert len(random_token(64)) == int(m.group(1))


def test_the_rotation_scripts_safety_note_matches_its_transaction_shape():
    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "scripts" / "rotate_jwt_secret.py").read_text(encoding="utf-8")
    doc = src[: src.index('"""', src.index('"""') + 3)]
    assert "own transaction" not in doc, (
        "the script commits once at the end; per-table transactions would mean "
        "something quite different to an operator deciding whether to interrupt"
    )
    assert src.count("db.commit()") == 1
