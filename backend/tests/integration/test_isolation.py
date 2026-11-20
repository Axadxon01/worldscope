"""User isolation: one user can never see or change another's rows.

Checked at two layers. Through the API, every user-owned endpoint is tried across
accounts. In the database, row-level security is probed directly with the app's own
role, so the protection holds even for a query that forgets its user filter.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import add_article, join, scalar, setup_admin
from worldscope.db.models import AUTH_SYSTEM_TABLES, USER_OWNED_TABLES

pytestmark = pytest.mark.integration


def test_runs_as_an_ordinary_role(db: Engine) -> None:
    is_superuser = scalar(db, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    assert is_superuser is False, (
        "Integration tests must connect as a non-superuser: superusers bypass row-level "
        "security. See infra/postgres/init/10-app-role.sh."
    )


def test_every_table_with_a_user_id_is_protected(db: Engine) -> None:
    with db.connect() as conn:
        with_user_id = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'user_id'"
                )
            ).scalars()
        )
        protected = set(
            conn.execute(
                text(
                    "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                    "AND relnamespace = 'public'::regnamespace"
                )
            ).scalars()
        )
        policies = set(
            conn.execute(
                text("SELECT tablename FROM pg_policies WHERE schemaname = 'public'")
            ).scalars()
        )
    assert with_user_id - set(AUTH_SYSTEM_TABLES) == set(USER_OWNED_TABLES)
    assert set(USER_OWNED_TABLES) <= protected
    assert set(USER_OWNED_TABLES) <= policies


def test_bookmarks_are_private_through_the_api(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    alice = join(api, admin, "alice@example.org")
    bob = join(api, admin, "bob@example.org")
    article = add_article(db)

    mine = api.post(
        "/api/v1/bookmarks",
        json={"article_id": article, "note": "follow up"},
        headers=alice.headers,
    )
    assert mine.status_code == 201
    alice_bookmark = mine.json()["id"]

    assert api.get("/api/v1/bookmarks", headers=bob.headers).json() == {"bookmarks": []}
    stolen = api.delete(f"/api/v1/bookmarks/{alice_bookmark}", headers=bob.headers)
    assert stolen.status_code == 404
    assert len(api.get("/api/v1/bookmarks", headers=alice.headers).json()["bookmarks"]) == 1

    # Bob bookmarking the same article makes his own row; Alice's note is untouched.
    api.post("/api/v1/bookmarks", json={"article_id": article, "note": "bob"}, headers=bob.headers)
    alice_rows = api.get("/api/v1/bookmarks", headers=alice.headers).json()["bookmarks"]
    bob_rows = api.get("/api/v1/bookmarks", headers=bob.headers).json()["bookmarks"]
    assert [row["note"] for row in alice_rows] == ["follow up"]
    assert [row["note"] for row in bob_rows] == ["bob"]

    assert (
        api.delete(f"/api/v1/bookmarks/{alice_bookmark}", headers=alice.headers).status_code == 204
    )
    assert api.get("/api/v1/bookmarks", headers=alice.headers).json() == {"bookmarks": []}
    assert len(api.get("/api/v1/bookmarks", headers=bob.headers).json()["bookmarks"]) == 1


def test_bookmarks_need_a_real_article_and_a_session(api: TestClient) -> None:
    admin = setup_admin(api)
    missing = api.post(
        "/api/v1/bookmarks", json={"article_id": str(uuid.uuid4())}, headers=admin.headers
    )
    assert missing.status_code == 404
    assert api.get("/api/v1/bookmarks").status_code == 401


@pytest.mark.parametrize("table", USER_OWNED_TABLES)
def test_row_level_security_blocks_queries_without_a_user_filter(
    api: TestClient, db: Engine, table: str
) -> None:
    admin = setup_admin(api)
    alice = join(api, admin, "alice@example.org")
    bob = join(api, admin, "bob@example.org")
    article = add_article(db)
    for account in (alice, bob):
        api.post("/api/v1/bookmarks", json={"article_id": article}, headers=account.headers)

    with db.connect() as conn:
        # No user set: nothing is visible at all.
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608
        conn.rollback()

        # Acting for Bob, an unfiltered query sees only Bob's rows.
        conn.execute(text("SELECT set_config('app.user_id', :id, true)"), {"id": bob.id})
        owners = conn.execute(text(f"SELECT DISTINCT user_id::text FROM {table}")).scalars()  # noqa: S608
        assert list(owners) == [bob.id]
        # ...and cannot change Alice's.
        changed = conn.execute(
            text(f"DELETE FROM {table} WHERE user_id = :alice"),  # noqa: S608
            {"alice": alice.id},
        )
        assert changed.rowcount == 0
        conn.rollback()

    # Writing a row for someone else is refused outright.
    other_article = add_article(db, "A second article")
    with db.connect() as conn:
        conn.execute(text("SELECT set_config('app.user_id', :id, true)"), {"id": bob.id})
        insert_for_alice = text(
            "INSERT INTO bookmarks (user_id, article_id) "
            "SELECT :alice, id FROM articles WHERE public_id = :article"
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(insert_for_alice, {"alice": alice.id, "article": other_article})


def test_deleting_an_account_removes_its_rows_only(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    alice = join(api, admin, "alice@example.org")
    bob = join(api, admin, "bob@example.org")
    article = add_article(db)
    for account in (alice, bob):
        api.post("/api/v1/bookmarks", json={"article_id": article}, headers=account.headers)

    with db.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": alice.id})
    assert len(api.get("/api/v1/bookmarks", headers=bob.headers).json()["bookmarks"]) == 1
    with db.begin() as conn:
        conn.execute(text("SELECT set_config('app.user_id', :id, true)"), {"id": alice.id})
        assert conn.execute(text("SELECT count(*) FROM bookmarks")).scalar() == 0
