"""Admin email-template editor endpoints (v1.25.0)."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.email_template_override import EmailTemplateOverride
from app.models.user import UserRole

PW = "Pass12345678!"
_BASE = "/api/admin/settings/email-templates"


async def _admin_token(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_returns_all_slugs_and_locales(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.get(_BASE, headers=_h(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {l["code"] for l in body["locales"]} == {"en", "de"}
    assert len(body["items"]) == 21
    item = next(i for i in body["items"] if i["slug"] == "share_created")
    assert item["has_override"] == {"en": False, "de": False}
    assert any(p["token"] == "[SHARE_LINK]" for p in body["placeholders"]["share_created"])


@pytest.mark.asyncio
async def test_put_persists_and_audits(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        f"{_BASE}/share_created/de",
        json={"subject": "Neu von [SENDER]", "body_markdown": "Hallo [RECIPIENT]!"},
        headers=_h(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_override"] is True
    row = db.query(EmailTemplateOverride).filter_by(slug="share_created", locale="de").one()
    assert row.body_markdown == "Hallo [RECIPIENT]!"
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.email_template_changed)
        .one()
    )
    assert audit.target_id == "share_created:de"
    # Body is never recorded in the audit metadata.
    assert "Hallo" not in str(audit.extra)
    assert audit.extra == {"slug": "share_created", "locale": "de"}


@pytest.mark.asyncio
async def test_put_rejects_unknown_placeholder(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.put(
        f"{_BASE}/share_created/en",
        json={"body_markdown": "Hi [BOGUS_TOKEN]"},
        headers=_h(token),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNKNOWN_PLACEHOLDER"


@pytest.mark.asyncio
async def test_put_requires_auth_link_for_reset_password(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    # Missing [RESET_LINK] → rejected.
    bad = await client.put(
        f"{_BASE}/reset_password/en",
        json={"body_markdown": "Hi [RECIPIENT], you asked for a reset."},
        headers=_h(token),
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "MISSING_REQUIRED_PLACEHOLDER"
    # Present → accepted.
    ok = await client.put(
        f"{_BASE}/reset_password/en",
        json={"body_markdown": "Hi [RECIPIENT], reset: [RESET_LINK]"},
        headers=_h(token),
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_delete_resets_to_default(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    await client.put(
        f"{_BASE}/share_approved/en",
        json={"body_markdown": "Custom [RECIPIENT]"},
        headers=_h(token),
    )
    resp = await client.delete(f"{_BASE}/share_approved/en", headers=_h(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_override"] is False
    assert db.query(EmailTemplateOverride).filter_by(slug="share_approved", locale="en").count() == 0
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.email_template_reset)
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_preview_renders_sample_data(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    resp = await client.post(
        f"{_BASE}/reset_password/en/preview",
        json={"subject": "Reset for [RECIPIENT]", "body_markdown": "Reset: [reset]([RESET_LINK])"},
        headers=_h(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Grace" in body["subject"]
    # Sample auth link uses the canonical path (so masking is exercised too).
    assert "/reset-password/SAMPLETOKEN" in body["html"]
    assert "Heron" in body["html"]


@pytest.mark.asyncio
async def test_unknown_template_and_locale_404(make_user, client, login_as):
    token = await _admin_token(make_user, login_as)
    assert (await client.get(f"{_BASE}/nope/en", headers=_h(token))).status_code == 404
    assert (await client.get(f"{_BASE}/share_created/fr", headers=_h(token))).status_code == 404


@pytest.mark.asyncio
async def test_non_admin_forbidden(make_user, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)
    assert (await client.get(_BASE, headers=_h(token))).status_code in (401, 403)
