"""Site URL resolver — kv override beats the ``APP_URL`` env.

Every place that puts a fileHeron URL in front of a user (email
templates, public-link builders, in-app notification ``link_url``,
post-OIDC browser redirects) reads from here. The override lets an
admin change the deployed URL via the Settings UI without
restarting the container.

Two surfaces deliberately stay on the env value:

- ``services/webauthn.py`` RP origin allowlist — credentials are
  bound to the RP ID; runtime change silently invalidates every
  registered passkey.
- ``services/oidc.py`` redirect_uri builder — IdPs validate exact
  match against the URI registered at provider-config time;
  runtime change silently breaks SSO until the IdP allowlist is
  updated.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings as _env
from . import settings as settings_svc


def get_site_url(db: Session) -> str:
    """Return the effective site URL for user-facing links. Always
    trailing-slash-stripped so callers can ``f"{get_site_url(db)}/x"``
    without doubling slashes."""
    override = settings_svc.get(db, settings_svc.Keys.SITE_URL)
    raw = (override or _env.APP_URL or "").rstrip("/")
    return raw
