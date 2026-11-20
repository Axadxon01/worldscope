"""Password hashing (Argon2id) and the rules a new password must meet."""

from __future__ import annotations

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi's defaults follow RFC 9106's low-memory profile (Argon2id, 64 MiB, t=3).
_hasher = PasswordHasher()

MIN_LENGTH = 12
MAX_LENGTH = 256


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@cache
def dummy_hash() -> str:
    """A real hash to verify against when the email is unknown, so a failed login takes
    the same time whether or not the account exists."""
    return _hasher.hash("worldscope-timing-equaliser")


def password_problem(password: str, email: str) -> str | None:
    """Why this password is not acceptable, or None.

    Length is the rule that matters (NIST SP 800-63B); there are no composition rules.
    The checks below only catch passwords that are long but still trivially guessable.
    """
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    if len(set(password)) < 4:
        return "Use more than a few different characters."
    lowered = password.lower()
    local_part = email.lower().split("@", 1)[0]
    if len(local_part) >= 4 and local_part in lowered:
        return "Don't include your email address in the password."
    if lowered in {"worldscope", "worldscopeintelligence"} or lowered.startswith("worldscope"):
        return "Don't base the password on the product name."
    return None
