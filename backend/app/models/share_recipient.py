"""Share recipients - a join row per (share, recipient).

A recipient is EITHER a user OR a group, never both. Phase 3a only writes
recipient_user_id; Phase 4 introduces real groups and starts using
recipient_group_id (which becomes a FK to `groups` then).

Composite primary key on (share_id, recipient_user_id, recipient_group_id)
isn't great because of the nullables - instead we use a synthetic id +
indices. Uniqueness enforced at the application level (P4 service layer).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

# In SQLite, BigInteger PRIMARY KEY does NOT autoincrement. Type variant
# lets prod use BIGINT and tests use INTEGER (ROWID alias).
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .share import Share
    from .user import User


class ShareRecipient(Base):
    __tablename__ = "share_recipients"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)

    share_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shares.id", ondelete="CASCADE"), nullable=False, index=True
    )

    recipient_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # A real FK since Phase 4 (202605020916), which the model never caught up with.
    # Declaring it here is what makes the create_all test schema behave like
    # production: without it SQLite keeps orphaned recipient rows after a group is
    # deleted while MariaDB cascades them away, so the loss of a share's historical
    # recipient record cannot be seen by any test.
    recipient_group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True
    )

    share: Mapped[Share] = relationship("Share", back_populates="recipients")
    recipient_user: Mapped[User | None] = relationship("User", foreign_keys=[recipient_user_id])
