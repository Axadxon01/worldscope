"""Load reference data and the source list from backend/config into the database.

Runs after every migration (see bin/start-api and the compose `migrate` service) and is
idempotent: rows are upserted by their natural key, so running it twice changes nothing.
Everything happens in one transaction; a bad file leaves the database as it was.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Connection, func, select, update
from sqlalchemy.dialects.postgresql import insert

from worldscope.catalog import (
    CatalogError,
    CountryRow,
    SourceEntry,
    Taxonomy,
    load_countries,
    load_sources,
    load_taxonomy,
    slugify,
)
from worldscope.db.models import Country, Region, Source, Topic


@dataclass
class SyncReport:
    regions: int = 0
    countries: int = 0
    topics: int = 0
    sources: int = 0
    sources_enabled: int = 0
    sources_retired: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"regions: {self.regions}",
            f"countries: {self.countries}",
            f"topics: {self.topics}",
            f"sources: {self.sources} ({self.sources_enabled} enabled)",
        ]
        if self.sources_retired:
            out.append(f"retired sources: {', '.join(self.sources_retired)}")
        out.extend(f"warning: {warning}" for warning in self.warnings)
        return out


def _region_plan(countries: list[CountryRow]) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Continents by slug, and subregions by slug with (name, continent slug).

    A subregion named like its continent (Antarctica) is not created; its countries
    belong to the continent directly. A subregion name used under two continents
    (Natural Earth's "Seven seas (open ocean)") gets the continent in its slug.
    """
    continents = {slugify(row.region): row.region for row in countries}
    parents: dict[str, set[str]] = defaultdict(set)
    for row in countries:
        if row.subregion != row.region:
            parents[row.subregion].add(slugify(row.region))
    subregions: dict[str, tuple[str, str]] = {}
    for name, continent_slugs in parents.items():
        for continent in continent_slugs:
            slug = slugify(name) if len(continent_slugs) == 1 else slugify(f"{continent} {name}")
            if slug in continents:
                raise CatalogError(f"subregion slug {slug} collides with a continent")
            subregions[slug] = (name, continent)
    return continents, subregions


def _subregion_slug(row: CountryRow, subregions: dict[str, tuple[str, str]]) -> str:
    if row.subregion == row.region:
        return slugify(row.region)
    plain = slugify(row.subregion)
    if plain in subregions and subregions[plain][1] == slugify(row.region):
        return plain
    return slugify(f"{slugify(row.region)} {row.subregion}")


def sync_reference(conn: Connection, countries: list[CountryRow], taxonomy: Taxonomy) -> SyncReport:
    report = SyncReport()
    continents, subregions = _region_plan(countries)

    for slug, name in continents.items():
        conn.execute(
            insert(Region)
            .values(slug=slug, name=name, kind="continent", parent_id=None)
            .on_conflict_do_update(
                index_elements=[Region.slug],
                set_={
                    "name": name,
                    "kind": "continent",
                    "parent_id": None,
                    "updated_at": func.now(),
                },
            )
        )
    region_ids = dict(conn.execute(select(Region.slug, Region.id)).all())
    for slug, (name, continent) in subregions.items():
        parent = region_ids[continent]
        conn.execute(
            insert(Region)
            .values(slug=slug, name=name, kind="subregion", parent_id=parent)
            .on_conflict_do_update(
                index_elements=[Region.slug],
                set_={
                    "name": name,
                    "kind": "subregion",
                    "parent_id": parent,
                    "updated_at": func.now(),
                },
            )
        )
    region_ids = dict(conn.execute(select(Region.slug, Region.id)).all())

    members: dict[str, set[str]] = defaultdict(set)
    for row in countries:
        region_slug = _subregion_slug(row, subregions)
        members[region_slug].add(row.iso3)
        values = {
            "iso2": row.iso2,
            "name": row.name_long,
            "short_name": row.name,
            "region_id": region_ids[region_slug],
            "label_lat": row.label_lat,
            "label_lon": row.label_lon,
            "wikidata_qid": row.wikidata_qid,
        }
        conn.execute(
            insert(Country)
            .values(iso3=row.iso3, **values)
            .on_conflict_do_update(
                index_elements=[Country.iso3], set_={**values, "updated_at": func.now()}
            )
        )

    focus_slugs = set()
    for focus in taxonomy.focus_regions:
        if focus.slug not in region_ids:
            raise CatalogError(f"taxonomy.yaml: focus region {focus.slug} is not in the data")
        expected, actual = set(focus.countries), members.get(focus.slug, set())
        if expected != actual:
            raise CatalogError(
                f"taxonomy.yaml: {focus.slug} lists {sorted(expected)} but the data has "
                f"{sorted(actual)}; check countries.csv before changing either"
            )
        focus_slugs.add(focus.slug)
    conn.execute(update(Region).values(is_focus=Region.slug.in_(focus_slugs)))

    for position, category in enumerate(taxonomy.categories, start=1):
        values = {
            "name": category.name,
            "kind": "category",
            "description": category.covers,
            "position": position,
        }
        conn.execute(
            insert(Topic)
            .values(slug=category.slug, **values)
            .on_conflict_do_update(
                index_elements=[Topic.slug], set_={**values, "updated_at": func.now()}
            )
        )

    known = {row.iso3 for row in countries}
    stale = set(conn.execute(select(Country.iso3)).scalars()) - known
    if stale:
        report.warnings.append(
            f"countries in the database but not in countries.csv (kept): {sorted(stale)}"
        )
    report.regions = conn.execute(select(func.count()).select_from(Region)).scalar_one()
    report.countries = conn.execute(select(func.count()).select_from(Country)).scalar_one()
    report.topics = conn.execute(
        select(func.count()).select_from(Topic).where(Topic.kind == "category")
    ).scalar_one()
    return report


def _source_values(entry: SourceEntry) -> dict[str, object]:
    return {
        "name": entry.name,
        "type": entry.type,
        "url": str(entry.url),
        "homepage": str(entry.homepage),
        "publisher_country_iso3": entry.publisher_country,
        "language": entry.language,
        "focus": list(entry.focus),
        "ownership": entry.ownership,
        "is_primary": entry.is_primary,
        "licence": entry.licence,
        "storage_policy": entry.storage_policy,
        "terms_url": str(entry.terms_url) if entry.terms_url else None,
        "review_status": entry.review,
        "review_note": entry.review_note,
        "enabled": entry.enabled,
        "fetch_interval_seconds": entry.fetch_interval_minutes * 60,
        "rate_limit": entry.rate_limit,
        "note": entry.note,
        "retired_at": None,
    }


def sync_sources(conn: Connection, entries: list[SourceEntry], report: SyncReport) -> None:
    countries = set(conn.execute(select(Country.iso3)).scalars())
    for entry in entries:
        if entry.publisher_country and entry.publisher_country not in countries:
            raise CatalogError(
                f"sources.yaml, source {entry.id}: "
                f"unknown publisher_country {entry.publisher_country}"
            )
    for entry in entries:
        values = _source_values(entry)
        conn.execute(
            insert(Source)
            .values(slug=entry.id, **values)
            .on_conflict_do_update(
                index_elements=[Source.slug], set_={**values, "updated_at": func.now()}
            )
        )
    listed = [entry.id for entry in entries]
    retired = conn.execute(
        update(Source)
        .where(Source.slug.not_in(listed), Source.retired_at.is_(None))
        .values(enabled=False, retired_at=datetime.now(UTC), updated_at=func.now())
        .returning(Source.slug)
    ).scalars()
    report.sources_retired = sorted(retired)
    report.sources = len(entries)
    report.sources_enabled = sum(1 for entry in entries if entry.enabled)


def sync_all(conn: Connection) -> SyncReport:
    """Validate every file first, then write; the caller owns the transaction."""
    countries, taxonomy, sources = load_countries(), load_taxonomy(), load_sources()
    report = sync_reference(conn, countries, taxonomy)
    sync_sources(conn, sources, report)
    return report
