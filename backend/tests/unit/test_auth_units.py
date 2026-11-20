from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.unit.test_api import FakeRedis, FakeSession, client_with
from worldscope.api.schemas import AcceptInvitationIn, LoginIn
from worldscope.auth.passwords import (
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)
from worldscope.auth.service import invitation_status
from worldscope.auth.tokens import email_key, hash_token, new_token, plausible
from worldscope.core.config import get_settings
from worldscope.db.models import Invitation


def test_passwords_hash_with_argon2id_and_verify() -> None:
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "correct horse battery staple")
    assert not verify_password(stored, "Correct horse battery staple")
    assert not verify_password("not-a-hash", "anything")
    assert not needs_rehash(stored)


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "at least 12"),
        ("x" * 257, "at most 256"),
        ("aaaaaaaaaaaaaaaa", "different characters"),
        ("my-anna.smith-password", "email address"),
        ("WorldScope2026!!", "product name"),
    ],
)
def test_weak_passwords_are_explained(password: str, problem: str) -> None:
    message = password_problem(password, "anna.smith@example.org")
    assert message is not None
    assert problem in message


def test_good_passphrases_pass() -> None:
    assert password_problem("correct horse battery staple", "anna@example.org") is None
    assert password_problem("Tashkent-Samarkand 1991", "ann@example.org") is None


def test_tokens_are_random_and_hashed_with_the_key() -> None:
    first, second = new_token(), new_token()
    assert first != second
    assert len(first) >= 43
    assert hash_token(first, b"key-one") != hash_token(first, b"key-two")
    assert len(hash_token(first, b"key-one")) == 32
    assert plausible(first)
    assert not plausible("")
    assert not plausible("x" * 500)
    assert email_key("A@Example.org ") == email_key("a@example.org")


def test_invitation_status() -> None:
    now = datetime.now(UTC)
    invitation = Invitation(expires_at=now + timedelta(days=1))
    assert invitation_status(invitation, now) == "pending"
    invitation.expires_at = now - timedelta(seconds=1)
    assert invitation_status(invitation, now) == "expired"
    invitation.revoked_at = now
    assert invitation_status(invitation, now) == "revoked"
    invitation.used_at = now
    assert invitation_status(invitation, now) == "used"


def test_display_names_are_tidied_and_checked() -> None:
    body = AcceptInvitationIn(token="t", display_name="  Ana \n  Lyst ", password="p")
    assert body.display_name == "Ana Lyst"
    for bad in ("   ", "Ana​Lyst\u0007"):
        with pytest.raises(ValidationError):
            AcceptInvitationIn(token="t", display_name=bad, password="p")


def test_login_body_rejects_bad_emails_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LoginIn(email="not-an-email", password="x")
    with pytest.raises(ValidationError):
        LoginIn.model_validate({"email": "a@example.org", "password": "x", "role": "admin"})


def test_internal_key_is_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "i" * 40)
    get_settings.cache_clear()
    client: TestClient = client_with(FakeSession(), FakeRedis())
    assert client.get("/api/v1/auth/me").status_code == 403
    assert client.get("/api/v1/auth/me", headers={"X-Internal-Key": "wrong"}).status_code == 403
    # The status check and health stay reachable without the key.
    assert client.get("/api/v1/system/status").status_code == 200
    assert client.get("/health").status_code == 200
