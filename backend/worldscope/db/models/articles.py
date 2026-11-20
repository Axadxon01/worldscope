"""Articles: one row per collected item. Headline, feed summary and link only; never full text."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    String,
    Text,
    false,
    func,
    text,  # noqa: TID251 (index expression and server defaults, no user input)
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from worldscope.db.base import Base, Timestamps

PROCESSING_STATES = ("new", "processing", "processed", "failed")

# Search vectors use the stemming dictionary of the article's own language, falling
# back to `simple` (no stemming) for languages PostgreSQL has no dictionary for, such
# as Uzbek. Queries combine the dictionaries, so one search box covers every language.
SEARCH_VECTOR = (
    "to_tsvector("
    "CASE language WHEN 'en' THEN 'english'::regconfig "
    "WHEN 'ru' THEN 'russian'::regconfig ELSE 'simple'::regconfig END, "
    "coalesce(title, '') || ' ' || coalesce(snippet, ''))"
)


class Article(Timestamps, Base):
    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint("length(title) > 0", name="title_not_empty"),
        CheckConstraint("octet_length(url_hash) = 32", name="url_hash_sha256"),
        CheckConstraint(
            "processing_status IN ('new', 'processing', 'processed', 'failed')",
            name="processing_status",
        ),
        Index("ix_articles_published_at", text("published_at DESC")),
        Index("ix_articles_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Shown in URLs instead of the sequential id.
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, server_default=func.gen_random_uuid()
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    canonical_url: Mapped[str] = mapped_column(Text)
    # SHA-256 of canonical_url. Uniqueness lives here because a btree index on very
    # long URLs can exceed PostgreSQL's index row size.
    url_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary, index=True)
    title: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    near_dup_of: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="SET NULL"))
    processing_status: Mapped[str] = mapped_column(String(16), server_default="new")
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=false())
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(SEARCH_VECTOR, persisted=True))
