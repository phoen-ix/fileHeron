"""How secrets and requests read in the UI (server v2.24.0), free of Tk.

The list panel and the detail views share these, and the Linux test leg can
reach them. Mirrors the web app's SecretList / SecretDetail wording.
"""
from __future__ import annotations

from typing import Optional

from .i18n import t
from .models import (
    SecretListItem,
    SecretRecipientSummary,
    SecretRequestListItem,
    SecretResponse,
)

# Filter value -> the server states it stands for (empty = all).
SECRET_STATE_FILTERS: dict[str, list[str]] = {
    "active": ["active"],
    "ended": ["burned", "expired", "revoked"],
    "all": [],
}
REQUEST_STATE_FILTERS: dict[str, list[str]] = {
    "open": ["open"],
    "closed": ["fulfilled", "cancelled", "expired"],
    "all": [],
}

# Server state -> (i18n key, pill tone). The tone is a `PillLabel` colour key.
_SECRET_STATES = {
    "active": ("secrets.state.active", "active"),
    "burned": ("secrets.state.burned", "deleted"),
    "expired": ("secrets.state.expired", "expired"),
    "revoked": ("secrets.state.revoked", "revoked"),
}
_REQUEST_STATES = {
    "open": ("secrets.request_state.open", "active"),
    "fulfilled": ("secrets.request_state.fulfilled", "clean"),
    "cancelled": ("secrets.request_state.cancelled", "revoked"),
    "expired": ("secrets.request_state.expired", "expired"),
}


def secret_state(state: str) -> tuple[str, str]:
    """(label, pill tone) for a secret's state."""
    key, tone = _SECRET_STATES.get(state, ("", state))
    return (t(key) if key else state), tone


def request_state(state: str) -> tuple[str, str]:
    """(label, pill tone) for a request's state."""
    key, tone = _REQUEST_STATES.get(state, ("", state))
    return (t(key) if key else state), tone


def label_or_placeholder(label: Optional[str]) -> str:
    return label or t("secrets.no_label")


def from_text(s: SecretListItem | SecretResponse) -> str:
    """Who a received secret is from - the sender, or how an answer to my
    request came in when nobody signed in wrote it."""
    if s.sender is not None:
        return s.sender.display_name
    if s.answered_via == "email":
        return s.answered_by_email or ""
    return t("secrets.via_request_link")


def audience_text(summary: Optional[SecretRecipientSummary]) -> str:
    if summary is None:
        return "-"
    parts: list[str] = []
    if summary.users:
        parts.append(t("secrets.audience.users", n=summary.users))
    if summary.groups:
        parts.append(t("secrets.audience.groups", n=summary.groups))
    if summary.emails:
        parts.append(t("secrets.audience.emails", n=summary.emails))
    if summary.link:
        parts.append(t("secrets.audience.link"))
    return " · ".join(parts) or "-"


def views_cell(s: SecretListItem, *, box: str) -> str:
    """Received: what I may still view. Sent: how often it was viewed."""
    if box == "received":
        if s.my_views_left is None:
            return t("secrets.views.unlimited")
        return t("secrets.views.left", n=s.my_views_left)
    used = s.views_used or 0
    if s.max_views is None:
        return t("secrets.views.used_unlimited", used=used)
    return t("secrets.views.used_of", used=used, max=s.max_views)


def view_limit_text(max_views: Optional[int], scope: str) -> str:
    if max_views is None:
        return t("secrets.views.unlimited")
    key = {
        "per_person": "secrets.limit.per_person",
        "per_recipient": "secrets.limit.per_recipient",
        "total": "secrets.limit.total",
    }.get(scope, "secrets.limit.per_person")
    return t(key, n=max_views)


def request_party_text(r: SecretRequestListItem, *, box: str) -> str:
    """Asked by me: whom I asked. Asked of me: who asked."""
    if box == "mine":
        return audience_text(r.target_summary)
    return r.requester.display_name


def answer_terms_text(max_views: Optional[int], lifetime_sec: Optional[int]) -> str:
    """How the answer to a request may be read, e.g. "1 view · for 7 days"."""
    parts: list[str] = []
    if max_views is not None:
        parts.append(t("secrets.request.terms_view_one") if max_views == 1
                     else t("secrets.request.terms_views", n=max_views))
    if lifetime_sec is not None:
        days = lifetime_sec / 86400
        if days >= 1 and float(days).is_integer():
            parts.append(t("secrets.request.terms_day_one") if days == 1
                         else t("secrets.request.terms_days", n=int(days)))
        else:
            hours = max(1, round(lifetime_sec / 3600))
            parts.append(t("secrets.request.terms_hour_one") if hours == 1
                         else t("secrets.request.terms_hours", n=hours))
    return " · ".join(parts) or t("secrets.views.unlimited")
