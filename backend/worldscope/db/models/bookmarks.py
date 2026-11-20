"""Bookmarks: the first user-owned table, protected by row-level security."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from worldscope.db.base import Base


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "article_id", name="uq_bookmarks_user_article"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Tables whose rows belong to one user. Each gets FORCE ROW LEVEL SECURITY with a
# policy matching user_id to the `app.user_id` setting of the current transaction.
# A test fails if a table with a user_id column is missing here.
USER_OWNED_TABLES = ("bookmarks",)
# Tables with a user_id that only the auth service reads, never on a user's behalf.
AUTH_SYSTEM_TABLES = ("sessions", "audit_log")
