"""Load and validate the files in backend/config: sources, taxonomy and countries.

These files are the source of truth for reference data and the source list. Loading
fails with a message naming the bad entry, so a typo stops the sync instead of
silently producing a half-loaded database.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator

from worldscope.db.models.sources import OWNERSHIP

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

Ownership = Literal[
    "public_broadcaster",
    "state",
    "state_funded",
    "government",
    "intergovernmental",
    "private",
    "nonprofit",
]
assert set(Ownership.__args__) == set(OWNERSHIP)  # type: ignore[attr-defined]


class CatalogError(ValueError):
    """A config file is missing, malformed or inconsistent."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceEntry(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=2, max_length=200)
    type: Literal["rss", "official", "api"]
    url: HttpUrl
    homepage: HttpUrl
    publisher_country: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    language: str = Field(pattern=r"^(multi|[a-z]{2})$")
    focus: list[str] = Field(default_factory=list)
    ownership: Ownership | None
    is_primary: bool
    licence: str | None = Field(default=None, min_length=3)
    storage_policy: Literal["link_only", "snippet"]
    terms_url: HttpUrl | None = None
    review: Literal["pending", "approved", "rejected"]
    review_note: str | None = None
    enabled: bool
    rate_limit: str | None = None
    fetch_interval_minutes: int = Field(default=15, ge=1, le=1440)
    note: str | None = None

    @model_validator(mode="after")
    def _enabled_only_when_approved(self) -> SourceEntry:
        if self.enabled and self.review != "approved":
            raise ValueError("enabled: true needs review: approved")
        if self.review == "approved":
            missing = [
                name
                for name in ("ownership", "licence", "terms_url")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"an approved source needs {', '.join(missing)}")
        return self


class Category(_Strict):
    slug: str = Field(pattern=r"^[a-z][a-z-]{1,62}$")
    name: str
    covers: str


class FocusRegion(_Strict):
    slug: str
    countries: list[str]


class Taxonomy(_Strict):
    categories: list[Category]
    focus_regions: list[FocusRegion]


class CountryRow(_Strict):
    iso3: str = Field(pattern=r"^[A-Z]{3}$")
    iso2: str = Field(pattern=r"^[A-Z]{2}$")
    name: str
    name_long: str
    region: str
    subregion: str
    label_lat: float = Field(ge=-90, le=90)
    label_lon: float = Field(ge=-180, le=180)
    wikidata_qid: str | None = Field(default=None, pattern=r"^Q\d+$")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"{path.name} not found in {path.parent}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path.name} is not valid YAML: {exc}") from exc


def _describe(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "entry"
    return f"{where}: {first['msg']}"


def load_sources(path: Path | None = None) -> list[SourceEntry]:
    path = path or CONFIG_DIR / "sources.yaml"
    data = _read_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise CatalogError(f"{path.name} must contain a top-level `sources:` list")
    entries = []
    for index, raw in enumerate(data["sources"]):
        label = raw.get("id", f"#{index + 1}") if isinstance(raw, dict) else f"#{index + 1}"
        try:
            entries.append(SourceEntry.model_validate(raw))
        except ValidationError as exc:
            raise CatalogError(f"{path.name}, source {label}: {_describe(exc)}") from exc
    for field in ("id", "url"):
        counts = Counter(str(getattr(entry, field)) for entry in entries)
        duplicates = sorted(value for value, count in counts.items() if count > 1)
        if duplicates:
            raise CatalogError(f"{path.name}: duplicate {field} {duplicates[0]}")
    return entries


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    path = path or CONFIG_DIR / "taxonomy.yaml"
    try:
        return Taxonomy.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise CatalogError(f"{path.name}: {_describe(exc)}") from exc


def load_countries(path: Path | None = None) -> list[CountryRow]:
    path = path or CONFIG_DIR / "countries.csv"
    try:
        lines = [
            ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")
        ]
    except FileNotFoundError as exc:
        raise CatalogError(f"{path.name} not found; run scripts/build_countries.py") from exc
    rows = []
    for raw in csv.DictReader(lines):
        cleaned = {key: (value or None) for key, value in raw.items()}
        try:
            rows.append(CountryRow.model_validate(cleaned))
        except ValidationError as exc:
            raise CatalogError(f"{path.name}, {raw.get('iso3')}: {_describe(exc)}") from exc
    if not rows:
        raise CatalogError(f"{path.name} has no rows")
    return rows
