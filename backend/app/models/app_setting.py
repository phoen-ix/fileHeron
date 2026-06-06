"""Generic key-value app settings store (Phase 9).

Each row is a single configuration value an admin has overridden via
the web UI. Cleartext for non-sensitive values; Fernet-encrypted (via
`utils.crypto.encrypt_setting` / `decrypt_setting`) for secrets like
`oidc_client_secret`.

The settings table is the *override* layer - `services/settings.py`
falls back to `config.settings` (env) when no DB row exists. So
existing env-driven deployments keep working without writing any
rows.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .user import User  # noqa: F401  (only for type hints)




class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    # `value` holds either cleartext (is_encrypted=False) or the Fernet
    # ciphertext (is_encrypted=True, the value is then ASCII-only).
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_encrypted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
