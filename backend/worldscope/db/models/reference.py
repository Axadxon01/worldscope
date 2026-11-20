"""Reference data: regions, countries and topics. Loaded from backend/config by `sync`."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Double,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    Text,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from worldscope.db.base import Base, Timestamps

REGION_KINDS = ("continent", "subregion")
TOPIC_KINDS = ("category", "emerging")


class Region(Timestamps, Base):
    """A UN geoscheme continent or subregion (from Natural Earth's REGION_UN / SUBREGION)."""

    __tablename__ = "regions"
    __table_args__ = (CheckConstraint("kind IN ('continent', 'subregion')", name="kind"),)

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"), index=True
    )
    # Focus regions (Central Asia) are shown on their own throughout the product.
    is_focus: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Country(Timestamps, Base):
    """A country or territory with an ISO 3166-1 code."""

    __tablename__ = "countries"
    __table_args__ = (
        CheckConstraint("iso3 ~ '^[A-Z]{3}$'", name="iso3_format"),
        CheckConstraint("iso2 ~ '^[A-Z]{2}$'", name="iso2_format"),
        CheckConstraint("label_lat BETWEEN -90 AND 90", name="label_lat_range"),
        CheckConstraint("label_lon BETWEEN -180 AND 180", name="label_lon_range"),
    )

    iso3: Mapped[str] = mapped_column(String(3), primary_key=True)
    iso2: Mapped[str] = mapped_column(String(2), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    short_name: Mapped[str] = mapped_column(String(100))
    # The UN subregion the country belongs to.
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"), index=True
    )
    # Natural Earth's label point: always inside the country, so country-level items
    # can be placed on the map without inventing a location.
    label_lat: Mapped[float] = mapped_column(Double)
    label_lon: Mapped[float] = mapped_column(Double)
    wikidata_qid: Mapped[str | None] = mapped_column(String(16))


class Topic(Timestamps, Base):
    """A category (the 12 from taxonomy.yaml) or, later, an emerging topic."""

    __tablename__ = "topics"
    __table_args__ = (CheckConstraint("kind IN ('category', 'emerging')", name="kind"),)

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int | None] = mapped_column(SmallInteger)
