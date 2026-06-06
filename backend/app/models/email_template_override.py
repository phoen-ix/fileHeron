"""Admin-authored email-template overrides (v1.25.0).

Each row overrides ONE email template for ONE locale. The admin authors a
Markdown ``body_markdown`` (+ optional ``subject``); the renderer in
``services/email.py`` consults this table first and falls back to the built-in
filesystem Jinja template when no row exists. "Reset to default" simply deletes
the row, so the built-in copy is always the safety net.

Keyed by ``(slug, locale)`` — independent upsert/delete per pair. Bodies are
free text and can be large, so they live here rather than in the hot
``app_settings`` kv table.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .user import User  # noqa: F401  (only for type hints)

# In SQLite, a BigInteger PK does not autoincrement — only INTEGER PK does.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class EmailTemplateOverride(Base):
    __tablename__ = "email_template_override"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    # NULL subject ⇒ inherit the built-in subject from subjects.json.
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("slug", "locale", name="uq_email_tpl_slug_locale"),
    )
