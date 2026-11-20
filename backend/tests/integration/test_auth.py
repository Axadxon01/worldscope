"""Accounts end to end: setup, sign-in, limits, sessions, invitations, roles, audit."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import (
    PASSWORD,
    SETUP_CODE,
    bearer,
    invite,
    join,
    scalar,
    setup_admin,
)
from worldscope.core.config import get_settings

pytestmark = pytest.mark.integration


def login(
    api: TestClient, email: str, password: str = PASSWORD, ip: str = "203.0.113.7"
) -> Any:  # the test client's response type
    return api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"X-Client-IP": ip},
    )


# --- The internal key -----------------------------------------------------------------


def test_api_refuses_calls_without_the_internal_key(api: TestClient) -> None:
    for headers in ({"X-Internal-Key": ""}, {"X-Internal-Key": "wrong"}):
        response = api.post(
            "/api/v1/auth/login", json={"email": "a@example.org", "password": "x"}, headers=headers
        )
        assert response.status_code == 403
    # Health and the status check stay public for the hosting platform.
    assert api.get("/health/ready", headers={"X-Internal-Key": ""}).status_code == 200


# --- First administrator --------------------------------------------------------------


def test_setup_creates_the_first_admin_once(api: TestClient, db: Engine) -> None:
    assert api.get("/api/v1/auth/setup").json() == {"state": "open"}

    wrong = api.post(
        "/api/v1/auth/setup",
        json={"code": "nope", "email": "a@example.org", "display_name": "A", "password": PASSWORD},
    )
    assert wrong.status_code == 403

    admin = setup_admin(api)
    assert api.get("/api/v1/auth/me", headers=admin.headers).json()["user"]["role"] == "admin"
    assert api.get("/api/v1/auth/setup").json() == {"state": "closed"}

    again = api.post(
        "/api/v1/auth/setup",
        json={
            "code": SETUP_CODE,
            "email": "b@example.org",
            "display_name": "B",
            "password": PASSWORD,
        },
    )
    assert again.status_code == 409
    assert scalar(db, "SELECT count(*) FROM users") == 1
    actions = scalar(db, "SELECT array_agg(action ORDER BY id) FROM audit_log")
    assert actions == ["setup_failed", "setup_completed"]


def test_setup_is_unavailable_without_a_code(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SETUP_CODE")
    get_settings.cache_clear()
    assert api.get("/api/v1/auth/setup").json() == {"state": "unconfigured"}
    response = api.post(
        "/api/v1/auth/setup",
        json={"code": "x", "email": "a@example.org", "display_name": "A", "password": PASSWORD},
    )
    assert response.status_code == 409


def test_setup_rejects_a_weak_password(api: TestClient) -> None:
    response = api.post(
        "/api/v1/auth/setup",
        json={
            "code": SETUP_CODE,
            "email": "a@example.org",
            "display_name": "A",
            "password": "short",
        },
    )
    assert response.status_code == 422
    assert "12 characters" in response.json()["detail"]


# --- Sign-in --------------------------------------------------------------------------


def test_login_with_correct_and_wrong_passwords(api: TestClient, db: Engine) -> None:
    setup_admin(api)
    ok = login(api, "ADMIN@example.org")  # emails are case-insensitive
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == "admin@example.org"
    assert scalar(db, "SELECT host(ip) FROM sessions ORDER BY created_at DESC LIMIT 1") == (
        "203.0.113.7"
    )

    wrong = login(api, "admin@example.org", "not the password at all")
    unknown = login(api, "nobody@example.org", "not the password at all")
    assert wrong.status_code == unknown.status_code == 401
    # The same message either way: the response never says whether an account exists.
    assert wrong.json() == unknown.json() == {"detail": "Email or password is incorrect."}


def test_account_locks_after_five_failures(api: TestClient) -> None:
    setup_admin(api)
    for _ in range(5):
        assert login(api, "admin@example.org", "wrong password 123").status_code == 401
    locked = login(api, "admin@example.org")  # even the right password waits now
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    # Unknown emails lock the same way, so locking reveals nothing either.
    for _ in range(5):
        login(api, "ghost@example.org", "wrong password 123")
    assert login(api, "ghost@example.org", "wrong password 123").status_code == 429


def test_login_is_rate_limited_per_ip(api: TestClient) -> None:
    for attempt in range(20):
        assert login(api, f"user{attempt}@example.org", ip="198.51.100.1").status_code == 401
    assert login(api, "user99@example.org", ip="198.51.100.1").status_code == 429
    assert login(api, "user99@example.org", ip="198.51.100.2").status_code == 401


def test_client_ip_header_is_ignored_when_not_a_valid_address(api: TestClient, db: Engine) -> None:
    setup_admin(api)
    assert login(api, "admin@example.org", ip="<script>").status_code == 200
    assert scalar(db, "SELECT host(ip) FROM sessions ORDER BY created_at DESC LIMIT 1") != (
        "<script>"
    )


# --- Sessions -------------------------------------------------------------------------


def test_logout_ends_the_session(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    assert api.post("/api/v1/auth/logout", headers=admin.headers).status_code == 204
    assert api.get("/api/v1/auth/me", headers=admin.headers).status_code == 401
    assert scalar(db, "SELECT count(*) FROM sessions") == 0


def test_expired_and_unknown_sessions_are_refused(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    assert api.get("/api/v1/auth/me", headers=bearer("made-up-token")).status_code == 401
    assert api.get("/api/v1/auth/me").status_code == 401
    with db.begin() as conn:
        conn.execute(text("UPDATE sessions SET expires_at = now() - interval '1 second'"))
    assert api.get("/api/v1/auth/me", headers=admin.headers).status_code == 401
    assert scalar(db, "SELECT count(*) FROM sessions") == 0


def test_sessions_extend_while_used_but_not_past_the_maximum(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    with db.begin() as conn:
        conn.execute(
            text(
                "UPDATE sessions SET last_seen_at = now() - interval '2 hours', "
                "expires_at = now() + interval '1 hour'"
            )
        )
    assert api.get("/api/v1/auth/me", headers=admin.headers).status_code == 200
    days_left = scalar(db, "SELECT extract(epoch FROM expires_at - now()) / 86400 FROM sessions")
    assert 6.9 < days_left <= 7

    with db.begin() as conn:
        conn.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '30 days', "
                "last_seen_at = now() - interval '2 hours'"
            )
        )
    assert api.get("/api/v1/auth/me", headers=admin.headers).status_code == 401


def test_session_tokens_are_stored_only_as_hashes(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    stored = scalar(db, "SELECT encode(token_hash, 'hex') FROM sessions")
    assert admin.token not in stored
    assert len(stored) == 64


# --- Passwords ------------------------------------------------------------------------


def test_password_change_signs_out_other_browsers(api: TestClient) -> None:
    admin = setup_admin(api)
    other = login(api, admin.email).json()["token"]
    new = "a completely different passphrase"

    wrong = api.post(
        "/api/v1/auth/password",
        json={"current_password": "wrong", "new_password": new},
        headers=admin.headers,
    )
    assert wrong.status_code == 403
    weak = api.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "admin-password-123"},
        headers=admin.headers,
    )
    assert weak.status_code == 422  # contains the email's local part

    changed = api.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": new},
        headers=admin.headers,
    )
    assert changed.status_code == 204
    assert api.get("/api/v1/auth/me", headers=admin.headers).status_code == 200
    assert api.get("/api/v1/auth/me", headers=bearer(other)).status_code == 401
    assert login(api, admin.email, new).status_code == 200


# --- Invitations ----------------------------------------------------------------------


def test_invitation_flow(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    token = invite(api, admin, "Analyst@Example.org")
    preview = api.post("/api/v1/invitations/preview", json={"token": token})
    assert preview.json()["email"] == "analyst@example.org"
    assert preview.json()["role"] == "analyst"

    accepted = api.post(
        "/api/v1/invitations/accept",
        json={"token": token, "display_name": "  Ana   Lyst ", "password": PASSWORD},
    )
    assert accepted.status_code == 201
    assert accepted.json()["user"]["display_name"] == "Ana Lyst"

    # Single use.
    reused = api.post(
        "/api/v1/invitations/accept",
        json={"token": token, "display_name": "Again", "password": PASSWORD},
    )
    assert reused.status_code == 404
    assert api.post("/api/v1/invitations/preview", json={"token": token}).status_code == 404

    listed = api.get("/api/v1/admin/invitations", headers=admin.headers).json()
    assert [(row["email"], row["status"], row["invited_by"]) for row in listed] == [
        ("analyst@example.org", "used", "Ada Admin")
    ]
    assert scalar(db, "SELECT count(*) FROM invitations WHERE token_hash IS NULL") == 0


def test_expired_revoked_and_replaced_invitations_do_not_work(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    first = invite(api, admin, "x@example.org")
    second = invite(api, admin, "x@example.org")  # replaces the first
    assert api.post("/api/v1/invitations/preview", json={"token": first}).status_code == 404
    assert api.post("/api/v1/invitations/preview", json={"token": second}).status_code == 200

    pending = [
        row
        for row in api.get("/api/v1/admin/invitations", headers=admin.headers).json()
        if row["status"] == "pending"
    ]
    assert len(pending) == 1
    revoke = api.delete(f"/api/v1/admin/invitations/{pending[0]['id']}", headers=admin.headers)
    assert revoke.status_code == 204
    assert api.post("/api/v1/invitations/preview", json={"token": second}).status_code == 404

    third = invite(api, admin, "y@example.org")
    with db.begin() as conn:
        conn.execute(text("UPDATE invitations SET expires_at = now() - interval '1 minute'"))
    accept = api.post(
        "/api/v1/invitations/accept",
        json={"token": third, "display_name": "Y", "password": PASSWORD},
    )
    assert accept.status_code == 404


def test_cannot_invite_an_existing_account(api: TestClient) -> None:
    admin = setup_admin(api)
    response = api.post(
        "/api/v1/admin/invitations",
        json={"email": "ADMIN@example.org", "role": "analyst"},
        headers=admin.headers,
    )
    assert response.status_code == 409


# --- Roles and account administration -------------------------------------------------


def test_analysts_cannot_use_admin_endpoints(api: TestClient) -> None:
    admin = setup_admin(api)
    analyst = join(api, admin, "analyst@example.org")
    for method, path in (
        ("get", "/api/v1/admin/users"),
        ("get", "/api/v1/admin/invitations"),
        ("post", f"/api/v1/admin/users/{admin.id}/disable"),
    ):
        assert getattr(api, method)(path, headers=analyst.headers).status_code == 403
    denied = api.post(
        "/api/v1/admin/invitations",
        json={"email": "z@example.org", "role": "admin"},
        headers=analyst.headers,
    )
    assert denied.status_code == 403


def test_disabling_an_account_signs_it_out(api: TestClient) -> None:
    admin = setup_admin(api)
    analyst = join(api, admin, "analyst@example.org")
    disabled = api.post(f"/api/v1/admin/users/{analyst.id}/disable", headers=admin.headers)
    assert disabled.json()["disabled"] is True
    assert api.get("/api/v1/auth/me", headers=analyst.headers).status_code == 401
    assert login(api, analyst.email).status_code == 401

    api.post(f"/api/v1/admin/users/{analyst.id}/enable", headers=admin.headers)
    assert login(api, analyst.email).status_code == 200


def test_the_last_admin_cannot_be_disabled(api: TestClient) -> None:
    admin = setup_admin(api)
    second = join(api, admin, "second@example.org", role="admin")
    own = api.post(f"/api/v1/admin/users/{admin.id}/disable", headers=admin.headers)
    assert own.status_code == 409
    assert (
        api.post(f"/api/v1/admin/users/{second.id}/disable", headers=admin.headers).status_code
        == 200
    )
    users = api.get("/api/v1/admin/users", headers=admin.headers).json()
    assert {user["email"]: user["disabled"] for user in users} == {
        "admin@example.org": False,
        "second@example.org": True,
    }


# --- Audit log ------------------------------------------------------------------------


def test_audit_log_is_append_only(api: TestClient, db: Engine) -> None:
    admin = setup_admin(api)
    login(api, admin.email, "wrong password 123")
    actions = scalar(db, "SELECT array_agg(action ORDER BY id) FROM audit_log")
    assert actions == ["setup_completed", "login_failed"]
    for statement in ("UPDATE audit_log SET action = 'edited'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"), db.begin() as conn:
            conn.execute(text(statement))
    # Deleting an account keeps its entries and only drops the link to it.
    with db.begin() as conn:
        conn.execute(text("DELETE FROM users"))
    assert scalar(db, "SELECT count(*) FROM audit_log WHERE user_id IS NULL") == 2


def test_daily_purge_removes_only_expired_sessions(api: TestClient, db: Engine) -> None:
    from worldscope.tasks.system import purge_expired_sessions

    admin = setup_admin(api)
    login(api, admin.email)
    with db.begin() as conn:
        conn.execute(
            text(
                "UPDATE sessions SET expires_at = now() - interval '1 minute' "
                "WHERE id = (SELECT id FROM sessions ORDER BY created_at LIMIT 1)"
            )
        )
    assert purge_expired_sessions() == 1
    assert scalar(db, "SELECT count(*) FROM sessions") == 1
