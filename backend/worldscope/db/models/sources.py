"""News sources (loaded from backend/config/sources.yaml) and their fetch history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    false,
    text,  # noqa: TID251 (server defaults only, no user input)
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from worldscope.db.base import Base, Timestamps

SOURCE_TYPES = ("rss", "official", "api")
OWNERSHIP = (
    "public_broadcaster",
    "state",
    "state_funded",
    "government",
    "intergovernmental",
    "private",
    "nonprofit",
)
STORAGE_POLICIES = ("link_only", "snippet")
REVIEW_STATES = ("pending", "approved", "rejected")


class Source(Timestamps, Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("type IN ('rss', 'official', 'api')", name="type"),
        CheckConstraint(
            "ownership IS NULL OR ownership IN ('public_broadcaster', 'state', 'state_funded',"
            " 'government', 'intergovernmental', 'private', 'nonprofit')",
            name="ownership",
        ),
        CheckConstraint("storage_policy IN ('link_only', 'snippet')", name="storage_policy"),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')", name="review_status"
        ),
        # Nothing is collected from a source whose terms have not been approved.
        CheckConstraint(
            "NOT enabled OR (review_status = 'approved' AND retired_at IS NULL)",
            name="enabled_requires_approval",
        ),
        CheckConstraint("fetch_interval_seconds >= 60", name="fetch_interval"),
        CheckConstraint(
            "review_status <> 'approved' OR (licence IS NOT NULL AND terms_url IS NOT NULL)",
            name="approved_has_terms",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(Text, unique=True)
    homepage: Mapped[str] = mapped_column(Text)
    publisher_country_iso3: Mapped[str | None] = mapped_column(
        ForeignKey("countries.iso3", ondelete="RESTRICT"), index=True
    )
    language: Mapped[str] = mapped_column(String(16))
    focus: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'::varchar[]")
    )
    ownership: Mapped[str | None] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Both filled in during the terms review; required once a source is approved.
    licence: Mapped[str | None] = mapped_column(Text)
    storage_policy: Mapped[str] = mapped_column(String(16))
    terms_url: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(16))
    review_note: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    fetch_interval_seconds: Mapped[int] = mapped_column(Integer, server_default=text("900"))
    rate_limit: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    # Set when the source disappears from sources.yaml; it is then never collected.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class SourceFetchRun(Base):
    """One attempt to collect from a source. Kept for 90 days."""

    __tablename__ = "source_fetch_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'failed')", name="status"),
        Index("ix_source_fetch_runs_source_started", "source_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    items_seen: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_new: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
