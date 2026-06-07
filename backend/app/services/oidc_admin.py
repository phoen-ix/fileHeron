"""OIDC provider admin: presets, CRUD lookups, secret decryption.

Separated from `services/oidc.py` (which now houses the auth callback
flow + JWKS/discovery/claim helpers) so admins editing providers don't
drag in the entire login pipeline. The two modules are independent -
`oidc.py` imports `get_client_secret` from here (one-way dependency).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.oidc_provider import OIDCPreset, OIDCProvider
from ..models.user import User
from ..utils.crypto import decrypt_setting

logger = logging.getLogger("fileheron.oidc_admin")


# ---------------------------------------------------------------------------
# Preset metadata. The frontend reads this via
# `GET /api/admin/settings/sso/presets` so the AdminSettingsSSOEdit form
# can render the right helper inputs without hardcoding strings on both
# sides. `issuer_template` is rendered with Python-style `{name}` slots
# the UI fills with `tenant`/`host`/`realm` etc.
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    OIDCPreset.entra.value: {
        "label": "Microsoft Entra ID",
        "issuer_template": "https://login.microsoftonline.com/{tenant}/v2.0",
        "issuer_template_fields": [
            {"key": "tenant", "label": "Tenant ID or domain", "placeholder": "contoso.onmicrosoft.com"}
        ],
        "default_groups_claim": "groups",
        "supports_groups": True,
        "notes": (
            "Microsoft Entra emits group object IDs (not names) by default. "
            "Use the GUIDs from Entra's group blade in admin/employee groups."
        ),
    },
    OIDCPreset.google.value: {
        "label": "Google Workspace",
        "issuer": "https://accounts.google.com",
        "issuer_template_fields": [],
        "default_groups_claim": "",
        "supports_groups": False,
        "notes": (
            "Google does not expose Workspace groups in OIDC ID tokens. "
            "Role mapping must rely on local roles set when admins invite users."
        ),
    },
    OIDCPreset.authentik.value: {
        "label": "Authentik",
        "issuer_template": "https://{host}/application/o/{slug}/",
        "issuer_template_fields": [
            {"key": "host", "label": "Authentik host", "placeholder": "auth.example.com"},
            {"key": "slug", "label": "Application slug", "placeholder": "fileheron"},
        ],
        "default_groups_claim": "groups",
        "supports_groups": True,
        "notes": "Authentik groups are emitted by name in the `groups` claim by default.",
    },
    OIDCPreset.keycloak.value: {
        "label": "Keycloak",
        "issuer_template": "https://{host}/realms/{realm}",
        "issuer_template_fields": [
            {"key": "host", "label": "Keycloak host", "placeholder": "keycloak.example.com"},
            {"key": "realm", "label": "Realm", "placeholder": "fileheron"},
        ],
        "default_groups_claim": "realm_access.roles",
        "supports_groups": True,
        "notes": (
            "Keycloak nests realm roles under `realm_access.roles`. Add a "
            "`groups` mapper in the client if you'd rather match group names."
        ),
    },
    OIDCPreset.custom.value: {
        "label": "Custom OIDC",
        "issuer_template": "",
        "issuer_template_fields": [],
        "default_groups_claim": "groups",
        "supports_groups": True,
        "notes": "Provide the issuer URL, client ID and secret yourself.",
    },
}


def preset_meta(preset: OIDCPreset | str) -> dict[str, Any]:
    key = preset.value if isinstance(preset, OIDCPreset) else preset
    return PROVIDER_PRESETS.get(key, PROVIDER_PRESETS[OIDCPreset.custom.value])


# ---------------------------------------------------------------------------
# Provider lookups
# ---------------------------------------------------------------------------


def list_enabled_providers(db: Session) -> list[OIDCProvider]:
    return (
        db.query(OIDCProvider)
        .filter(OIDCProvider.enabled.is_(True))
        .order_by(OIDCProvider.name.asc())
        .all()
    )


def list_all_providers(db: Session) -> list[OIDCProvider]:
    return db.query(OIDCProvider).order_by(OIDCProvider.name.asc()).all()


def get_provider(db: Session, provider_id: str) -> OIDCProvider:
    row = (
        db.query(OIDCProvider).filter(OIDCProvider.id == provider_id).one_or_none()
    )
    if row is None:
        raise AppError(404, "OIDC_PROVIDER_NOT_FOUND", "OIDC provider not found.")
    return row


def get_enabled_provider(db: Session, provider_id: str) -> OIDCProvider:
    p = get_provider(db, provider_id)
    if not p.enabled:
        raise AppError(403, "OIDC_PROVIDER_DISABLED", "This OIDC provider is disabled.")
    if not is_provider_usable(p):
        raise AppError(
            503,
            "OIDC_PROVIDER_INCOMPLETE",
            "This OIDC provider is missing required configuration.",
        )
    return p


def get_provider_for_user(db: Session, user: User) -> OIDCProvider | None:
    if not user.oidc_provider_id:
        return None
    return (
        db.query(OIDCProvider)
        .filter(OIDCProvider.id == user.oidc_provider_id)
        .one_or_none()
    )


def is_any_enabled(db: Session) -> bool:
    return db.query(OIDCProvider).filter(OIDCProvider.enabled.is_(True)).first() is not None


def is_provider_usable(provider: OIDCProvider) -> bool:
    """All three required fields populated."""
    return bool(
        provider.issuer_url
        and provider.client_id
        and provider.client_secret_encrypted
    )


def get_client_secret(provider: OIDCProvider) -> str:
    """Fernet-decrypt the secret. "" if none is set; raises (fail-closed) if a
    stored secret can't be decrypted."""
    if not provider.client_secret_encrypted:
        return ""
    try:
        return decrypt_setting(provider.client_secret_encrypted)
    except Exception as e:
        # Fail closed + loud (audit L2): silently returning "" sends an EMPTY
        # client secret to the IdP token endpoint, surfacing as a confusing
        # generic auth failure. A decrypt failure almost always means JWT_SECRET
        # was rotated without re-encrypting the provider secrets - say so.
        logger.error(
            "oidc_admin.get_client_secret: decryption failed provider=%s: %s",
            provider.id, e,
        )
        raise AppError(
            500,
            "OIDC_SECRET_UNAVAILABLE",
            "The OIDC client secret could not be decrypted; re-save it on the provider.",
        ) from e
