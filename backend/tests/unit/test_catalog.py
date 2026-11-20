from __future__ import annotations

from pathlib import Path

import pytest

from worldscope.catalog import (
    CatalogError,
    CountryRow,
    load_countries,
    load_sources,
    load_taxonomy,
    slugify,
)
from worldscope.services.catalog_sync import _region_plan, _subregion_slug

GOOD_SOURCE = """
- id: example-feed
  name: Example Feed
  type: rss
  url: https://example.org/feed.xml
  homepage: https://example.org
  publisher_country: GBR
  language: en
  focus: [global]
  ownership: private
  is_primary: false
  licence: null
  storage_policy: snippet
  terms_url: null
  review: pending
  enabled: false
"""


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text("sources:" + body)
    return path


def test_shipped_config_files_load() -> None:
    sources = load_sources()
    assert len(sources) >= 40
    assert all(entry.review == "approved" for entry in sources if entry.enabled)
    assert len(load_taxonomy().categories) == 12
    countries = load_countries()
    assert {"UZB", "KAZ", "KGZ", "TJK", "TKM"} <= {row.iso3 for row in countries}


def test_a_valid_pending_source_loads(tmp_path: Path) -> None:
    (entry,) = load_sources(write(tmp_path, GOOD_SOURCE))
    assert entry.id == "example-feed"
    assert entry.fetch_interval_minutes == 15


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("enabled: false", "enabled: true"), "enabled: true needs review: approved"),
        (("review: pending", "review: approved"), "needs licence, terms_url"),
        (("type: rss", "type: scraper"), "type"),
        (("url: https://example.org/feed.xml", "url: not a url"), "url"),
        (("language: en", "language: english"), "language"),
        (("publisher_country: GBR", "publisher_country: GB"), "publisher_country"),
        (("is_primary: false", "is_primary: false\n  surprise: 1"), "surprise"),
    ],
)
def test_bad_source_entries_are_named(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    body = GOOD_SOURCE.replace(*change)
    with pytest.raises(CatalogError, match=message) as info:
        load_sources(write(tmp_path, body))
    assert "example-feed" in str(info.value)


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    other = GOOD_SOURCE.replace("https://example.org/feed.xml", "https://example.org/other.xml")
    with pytest.raises(CatalogError, match="duplicate id example-feed"):
        load_sources(write(tmp_path, GOOD_SOURCE + other))


def test_unreadable_files_give_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="not found"):
        load_sources(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("sources: [unclosed")
    with pytest.raises(CatalogError, match="not valid YAML"):
        load_sources(broken)


def row(iso3: str, region: str, subregion: str) -> CountryRow:
    return CountryRow(
        iso3=iso3,
        iso2=iso3[:2],
        name=iso3,
        name_long=iso3,
        region=region,
        subregion=subregion,
        label_lat=0,
        label_lon=0,
    )


def test_region_plan_handles_natural_earth_quirks() -> None:
    rows = [
        row("UZB", "Asia", "Central Asia"),
        row("ATA", "Antarctica", "Antarctica"),
        row("ATF", "Africa", "Seven seas (open ocean)"),
        row("SGS", "Americas", "Seven seas (open ocean)"),
    ]
    continents, subregions = _region_plan(rows)
    assert set(continents) == {"asia", "antarctica", "africa", "americas"}
    assert subregions == {
        "central-asia": ("Central Asia", "asia"),
        "africa-seven-seas-open-ocean": ("Seven seas (open ocean)", "africa"),
        "americas-seven-seas-open-ocean": ("Seven seas (open ocean)", "americas"),
    }
    assert [_subregion_slug(r, subregions) for r in rows] == [
        "central-asia",
        "antarctica",
        "africa-seven-seas-open-ocean",
        "americas-seven-seas-open-ocean",
    ]


def test_slugify() -> None:
    assert slugify("South-Eastern Asia") == "south-eastern-asia"
    assert slugify("Seven seas (open ocean)") == "seven-seas-open-ocean"
