"""Read access to the source list for the Sources page."""

from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from worldscope.db.models import Country, Source


async def list_sources(db: AsyncSession) -> list[tuple[Source, str | None]]:
    """Active sources (not retired): enabled first, then by review state and name."""
    review_order = case(
        (Source.review_status == "approved", 0),
        (Source.review_status == "pending", 1),
        else_=2,
    )
    rows = await db.execute(
        select(Source, Country.name)
        .outerjoin(Country, Country.iso3 == Source.publisher_country_iso3)
        .where(Source.retired_at.is_(None))
        .order_by(Source.enabled.desc(), review_order, Source.name)
    )
    return [(source, country) for source, country in rows.all()]
