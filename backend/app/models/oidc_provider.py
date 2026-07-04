"""OIDC providers - Phase 10 multi-provider model.

Replaces the singleton `app_settings.oidc.*` rows from Phase 9. An
operator can enable 2-3 providers concurrently for different user
populations (employees on Entra, partners on Google, …). Each user
binds to one provider only - `users.oidc_provider_id` is a single FK.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .user import User  # noqa: F401


class OIDCPreset(str, enum.Enum):
    """Drives the AdminSettingsSSOEdit form: which fields prefill, which
    are hidden, what the issuer template looks like."""
    entra = "entra"
    google = "google"
    authentik = "authentik"
    keycloak = "keycloak"
    custom = "custom"




def _new_uuid() -> str:
    return str(uuid.uuid4())


class OIDCProvider(Base):
    __tablename__ = "oidc_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    preset: Mapped[OIDCPreset] = mapped_column(
        SAEnum(OIDCPreset, native_enum=False, length=20), nullable=False
    )
    issuer_url: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # Fernet ciphertext (str); empty string means "not yet set". Use
    # `utils.crypto.encrypt_setting` / `decrypt_setting`.
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Override only when the IdP requires a specific redirect URI different
    # from `${APP_URL}/api/auth/oidc/callback/{id}`.
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
