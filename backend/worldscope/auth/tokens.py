"""Random tokens for sessions and invitations, stored only as keyed hashes."""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128


def new_token() -> str:
    """256 random bits, URL-safe (43 characters)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str, key: bytes) -> bytes:
    """HMAC-SHA256 with the server secret: a leaked database alone yields no usable token."""
    return hmac.new(key, token.encode(), hashlib.sha256).digest()


def plausible(token: str | None) -> bool:
    return bool(token) and len(token or "") <= MAX_TOKEN_LENGTH


def email_key(email: str) -> str:
    """Stable, non-reversible key for per-account counters, so Redis never holds emails."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
