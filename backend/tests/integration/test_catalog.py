"""Reference data and sources loaded from backend/config."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.integration.conftest import scalar, setup_admin
from worldscope.catalog import CatalogError, load_sources
from worldscope.services.catalog_sync import SyncReport, sync_all, sync_sources

pytestmark = pytest.mark.integration


def test_reference_data_is_loaded(db: Engine) -> None:
    assert scalar(db, "SELECT count(*) FROM countries") > 230
    assert scalar(db, "SELECT count(*) FROM topics WHERE kind = 'category'") == 12
    central_asia = scalar(
        db,
        "SELECT array_agg(c.iso3 ORDER BY c.iso3) FROM countries c "
        "JOIN regions r ON r.id = c.region_id WHERE r.slug = 'central-asia' AND r.is_focus",
    )
    assert central_asia == ["KAZ", "KGZ", "TJK", "TKM", "UZB"]
    assert scalar(db, "SELECT name FROM countries WHERE iso3 = 'UZB'") == "Uzbekistan"
    # Every country sits in a region whose parent chain ends at a continent.
    orphans = scalar(
        db,
        "SELECT count(*) FROM countries c JOIN regions r ON r.id = c.region_id "
        "LEFT JOIN regions p ON p.id = r.parent_id "
        "WHERE NOT (r.kind = 'continent' OR p.kind = 'continent')",
    )
    assert orphans == 0


def test_sync_is_idempotent(db: Engine) -> None:
    touched = "SELECT max(updated_at) FROM countries"
    stamp = scalar(db, touched)
    before = scalar(
        db, "SELECT md5(string_agg(slug || enabled::text, ',' ORDER BY slug)) FROM sources"
    )
    with db.begin() as conn:
        report = sync_all(conn)
    after = scalar(
        db, "SELECT md5(string_agg(slug || enabled::text, ',' ORDER BY slug)) FROM sources"
    )
    assert before == after
    assert report.sources_retired == []
    # Unchanged rows are not rewritten.
    assert scalar(db, touched) == stamp


def test_removed_sources_are_retired_not_deleted(db: Engine) -> None:
    entries = [entry for entry in load_sources() if entry.id != "bbc-world"]
    with db.begin() as conn:
        report = SyncReport()
        sync_sources(conn, entries, report)
    assert report.sources_retired == ["bbc-world"]
    assert scalar(db, "SELECT retired_at IS NOT NULL FROM sources WHERE slug = 'bbc-world'")
    with db.begin() as conn:
        sync_all(conn)  # back in the file, back in service
    assert scalar(db, "SELECT retired_at IS NULL FROM sources WHERE slug = 'bbc-world'")


def test_database_refuses_enabling_an_unreviewed_source(db: Engine) -> None:
    with pytest.raises(Exception, match="enabled_requires_approval"), db.begin() as conn:
        conn.execute(text("UPDATE sources SET enabled = true WHERE review_status = 'pending'"))


def test_a_failed_sync_changes_nothing(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reference rows are written before sources; a bad source must roll all of it back."""
    with db.begin() as conn:
        conn.execute(text("UPDATE countries SET name = 'Probe' WHERE iso3 = 'UZB'"))
    entries = load_sources()
    broken = [entries[0].model_copy(update={"publisher_country": "ZZZ"}), *entries[1:]]
    monkeypatch.setattr("worldscope.services.catalog_sync.load_sources", lambda: broken)
    with pytest.raises(CatalogError, match="unknown publisher_country ZZZ"), db.begin() as conn:
        sync_all(conn)
    assert scalar(db, "SELECT name FROM countries WHERE iso3 = 'UZB'") == "Probe"
    monkeypatch.undo()
    with db.begin() as conn:
        sync_all(conn)
    assert scalar(db, "SELECT name FROM countries WHERE iso3 = 'UZB'") == "Uzbekistan"


def test_sources_endpoint_lists_enabled_sources_first(api: TestClient) -> None:
    admin = setup_admin(api)
    response = api.get("/api/v1/sources", headers=admin.headers)
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == len(load_sources())
    enabled = [source for source in sources if source["enabled"]]
    assert sources[: len(enabled)] == enabled
    assert all(source["review_status"] == "approved" for source in enabled)
    fcdo = next(source for source in sources if source["slug"] == "uk-fcdo")
    assert fcdo["publisher_country"] == {"iso3": "GBR", "name": "United Kingdom"}
    assert api.get("/api/v1/sources").status_code == 401
