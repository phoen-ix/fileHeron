"""/api/admin/settings/email-templates/* - admin-editable email templates.

Per (slug, locale) Markdown overrides for outbound emails. The built-in
filesystem templates remain the default; an override only takes effect once
saved, and "reset to default" deletes the row. See ``services/email.py`` for the
override-aware render pipeline and ``services/email_placeholders.py`` for the
friendly-token registry.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.email_template_override import EmailTemplateOverride
from ...models.user import Locale, User
from ...schemas.email_templates_settings import (
    EmailTemplateItem,
    EmailTemplateLocale,
    EmailTemplatesListResponse,
    EmailTemplateSummaryItem,
    PlaceholderMeta,
    PreviewEmailTemplateRequest,
    PreviewEmailTemplateResponse,
    TestSendEmailTemplateRequest,
    TestSendEmailTemplateResponse,
    UpdateEmailTemplateRequest,
)
from ...services import email as email_svc
from ...services import email_placeholders as ep
from ...services import site as site_svc
from ...services.audit import record_audit_event
from ...utils.timeutil import utc_now

router = APIRouter(tags=["admin"])

_GROUP_ORDER = ["shares", "account", "security", "system"]
_LOCALE_LABELS = {"en": "English", "de": "Deutsch"}


def _locale_codes() -> list[str]:
    return [loc.value for loc in Locale]


def _assert_slug(slug: str) -> None:
    if not ep.is_editable(slug):
        raise AppError(404, "UNKNOWN_TEMPLATE", "Unknown email template.")


def _assert_locale(locale: str) -> None:
    if locale not in _locale_codes():
        raise AppError(404, "UNKNOWN_LOCALE", "Unsupported locale.")


def _seed_ctx(slug: str) -> dict:
    """Map each placeholder's context key to its friendly token, so rendering the
    built-in template yields the default body/subject with [TOKENS] in place."""
    spec = ep.REGISTRY[slug]
    return {
        p.context_key: p.token
        for p in spec.placeholders
        if p.context_key not in ("app_name", "app_url")
    }


def _default_subject(slug: str, locale: str) -> str:
    return email_svc._resolve_subject(locale, slug, _seed_ctx(slug), app_name="[APP_NAME]")


def _default_body(slug: str, locale: str) -> str:
    """The built-in text template rendered with friendly tokens - the editor's
    starting point when no override exists. Best-effort."""
    try:
        return email_svc._render(
            locale, slug, "txt", _seed_ctx(slug),
            app_url="[APP_URL]", app_name="[APP_NAME]",
        ).strip() + "\n"
    except Exception:  # noqa: BLE001 - seeding is non-critical
        return ""


def _placeholders(slug: str) -> list[PlaceholderMeta]:
    return [PlaceholderMeta(**p) for p in ep.placeholders_for_ui(slug)]


def _get_override(db: Session, slug: str, locale: str) -> EmailTemplateOverride | None:
    return (
        db.query(EmailTemplateOverride)
        .filter_by(slug=slug, locale=locale)
        .one_or_none()
    )


def _item(db: Session, slug: str, locale: str) -> EmailTemplateItem:
    row = _get_override(db, slug, locale)
    default_subject = _default_subject(slug, locale)
    default_body = _default_body(slug, locale)
    return EmailTemplateItem(
        slug=slug,
        group=ep.REGISTRY[slug].group,
        locale=locale,
        has_override=row is not None,
        subject=(row.subject if (row and row.subject) else default_subject),
        body_markdown=(row.body_markdown if row else ""),
        default_subject=default_subject,
        default_body=default_body,
        placeholders=_placeholders(slug),
    )


def _validate_content(slug: str, subject: str | None, body: str) -> None:
    """Reject unknown placeholders, missing required auth links, broken markdown."""
    used = ep.tokens_in(body) | ep.tokens_in(subject or "")
    unknown = sorted(used - ep.known_tokens(slug))
    if unknown:
        raise AppError(
            400, "UNKNOWN_PLACEHOLDER",
            "The template uses unknown placeholders.",
            details={"unknown": unknown},
        )
    missing = sorted(ep.required_tokens(slug) - ep.tokens_in(body))
    if missing:
        raise AppError(
            400, "MISSING_REQUIRED_PLACEHOLDER",
            "A required link placeholder is missing from the body.",
            details={"missing": missing},
        )
    try:
        email_svc._md.render(body)
    except Exception:  # noqa: BLE001
        raise AppError(400, "INVALID_MARKDOWN", "The body could not be parsed.")


# ---------------------------------------------------------------------------


@router.get("/settings/email-templates", response_model=EmailTemplatesListResponse)
def list_email_templates(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailTemplatesListResponse:
    codes = _locale_codes()
    overrides = {
        (o.slug, o.locale)
        for o in db.query(EmailTemplateOverride.slug, EmailTemplateOverride.locale).all()
    }
    slugs = sorted(ep.REGISTRY, key=lambda s: (_GROUP_ORDER.index(ep.REGISTRY[s].group), s))
    items = [
        EmailTemplateSummaryItem(
            slug=slug,
            group=ep.REGISTRY[slug].group,
            has_override={c: (slug, c) in overrides for c in codes},
        )
        for slug in slugs
    ]
    return EmailTemplatesListResponse(
        locales=[
            EmailTemplateLocale(code=c, label=_LOCALE_LABELS.get(c, c.upper()))
            for c in codes
        ],
        groups=_GROUP_ORDER,
        items=items,
        placeholders={slug: _placeholders(slug) for slug in slugs},
    )


@router.get(
    "/settings/email-templates/{slug}/{locale}", response_model=EmailTemplateItem
)
def get_email_template(
    slug: str, locale: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailTemplateItem:
    _assert_slug(slug)
    _assert_locale(locale)
    return _item(db, slug, locale)


@router.put(
    "/settings/email-templates/{slug}/{locale}", response_model=EmailTemplateItem
)
def update_email_template(
    slug: str, locale: str,
    payload: UpdateEmailTemplateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailTemplateItem:
    _assert_slug(slug)
    _assert_locale(locale)
    subject = (payload.subject or "").strip() or None
    _validate_content(slug, subject, payload.body_markdown)

    row = _get_override(db, slug, locale)
    if row is None:
        row = EmailTemplateOverride(slug=slug, locale=locale)
        db.add(row)
    row.subject = subject
    row.body_markdown = payload.body_markdown
    row.updated_at = utc_now()
    row.updated_by_id = admin.id
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.email_template_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id=f"{slug}:{locale}",
        metadata={"slug": slug, "locale": locale},
        request=request,
    )
    db.commit()
    return _item(db, slug, locale)


@router.delete(
    "/settings/email-templates/{slug}/{locale}", response_model=EmailTemplateItem
)
def reset_email_template(
    slug: str, locale: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailTemplateItem:
    _assert_slug(slug)
    _assert_locale(locale)
    row = _get_override(db, slug, locale)
    if row is not None:
        db.delete(row)
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.email_template_reset,
            actor_user_id=admin.id,
            target_type="settings",
            target_id=f"{slug}:{locale}",
            metadata={"slug": slug, "locale": locale},
            request=request,
        )
        db.commit()
    return _item(db, slug, locale)


@router.post(
    "/settings/email-templates/{slug}/{locale}/preview",
    response_model=PreviewEmailTemplateResponse,
)
def preview_email_template(
    slug: str, locale: str,
    payload: PreviewEmailTemplateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> PreviewEmailTemplateResponse:
    _assert_slug(slug)
    _assert_locale(locale)
    subject = (payload.subject or "").strip() or None
    _validate_content(slug, subject, payload.body_markdown)
    app_url = site_svc.get_site_url(db)
    ov = SimpleNamespace(subject=subject, body_markdown=payload.body_markdown)
    rendered_subject, text, html = email_svc.render_override(
        ov, slug, locale, ep.sample_ctx(slug, app_url=app_url),
        app_url=app_url,
        app_name=site_svc.get_app_name(db),
        site_timezone=site_svc.get_site_timezone(db),
    )
    return PreviewEmailTemplateResponse(subject=rendered_subject, text=text, html=html)


@router.post(
    "/settings/email-templates/{slug}/{locale}/test-send",
    response_model=TestSendEmailTemplateResponse,
)
async def test_send_email_template(
    slug: str, locale: str,
    payload: TestSendEmailTemplateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TestSendEmailTemplateResponse:
    """Render the (possibly unsaved) edits with sample data and send to the
    requesting admin's own address. Recipient is server-resolved - never client
    supplied - so this can't be used to mail arbitrary people."""
    _assert_slug(slug)
    _assert_locale(locale)
    subject = (payload.subject or "").strip() or None
    _validate_content(slug, subject, payload.body_markdown)
    app_url = site_svc.get_site_url(db)
    ov = SimpleNamespace(subject=subject, body_markdown=payload.body_markdown)
    rendered_subject, text, html = email_svc.render_override(
        ov, slug, locale, ep.sample_ctx(slug, app_url=app_url),
        app_url=app_url,
        app_name=site_svc.get_app_name(db),
        site_timezone=site_svc.get_site_timezone(db),
    )
    cfg = email_svc.resolve_smtp_config(db)
    if not cfg.is_configured:
        return TestSendEmailTemplateResponse(
            ok=False,
            error_class="NotConfigured",
            error_message="SMTP host is empty. Configure SMTP under Settings → Email first.",
            sent_to=None,
        )
    try:
        await email_svc._send_resolved(
            to=admin.email, subject=rendered_subject,
            text_body=text, html_body=html, category=slug,
        )
    except Exception as exc:  # noqa: BLE001 - surface the SMTP error to the admin
        payload_out = email_svc._smtp_error_payload(exc)
        return TestSendEmailTemplateResponse(**payload_out, sent_to=admin.email)
    return TestSendEmailTemplateResponse(ok=True, sent_to=admin.email)
