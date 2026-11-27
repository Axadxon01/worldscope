"""Accounts: each person signs in with their own name and password.

Until the first account exists the site is opened with the setup password (APP_PASSWORD) on the
sign-in page, which at the same time asks the person to choose their own name and password and makes
them the administrator. From then on the setup password opens nothing: a password that was ever
shared or shown somewhere stops mattering. The administrator adds other people on the admin page.

Passwords are stored as PBKDF2-SHA256 with a random salt. A session is a random token in a
cookie; the database keeps only its SHA-256, so a copy of the database cannot be used to sign in.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

import db

ROUNDS = 240_000
SESSION_DAYS = 30
COOKIE = "ws_session"
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8
CODE_MINUTES = 15
_exists_cache = {"yes": False}
_fails: dict[str, list[float]] = {}
FAIL_WINDOW = 600  # seconds
FAIL_LIMIT = 8


class AccountError(ValueError):
    """A message for the person, in Uzbek."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ROUNDS)
    return f"pbkdf2${ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


_DUMMY = hash_password(secrets.token_hex(8))  # same work for unknown names: no timing hint


def _valid_new(username: str, password: str) -> None:
    if not USERNAME.match(username or ""):
        raise AccountError("Login 3–32 belgi: lotin harflari, raqamlar, nuqta, _ yoki -")
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"Parol kamida {MIN_PASSWORD} belgi bo'lsin")
    if password.lower() == username.lower():
        raise AccountError("Parol login bilan bir xil bo'lmasin")


# ------------------------------------------------------------------ users


def any_users() -> bool:
    if _exists_cache["yes"]:
        return True
    yes = db.one("SELECT 1 AS x FROM users LIMIT 1") is not None
    _exists_cache["yes"] = yes
    return yes


def mode(setup_password: str) -> str:
    """accounts: sign-in required · setup: only the setup password, to make the first admin · open."""
    if any_users():
        return "accounts"
    return "setup" if setup_password else "open"


def create(username: str, password: str, role: str = "user") -> dict:
    username = (username or "").strip()
    _valid_new(username, password)
    if db.one("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)):
        raise AccountError("Bu login band")
    with db.tx() as c:
        row = c.execute(
            "INSERT INTO users (username, pw, role, created) VALUES (?,?,?,?) RETURNING id",
            (username, hash_password(password), "admin" if role == "admin" else "user", _now()),
        ).fetchone()
    _exists_cache["yes"] = True
    return get(row["id"])


def get(user_id: int) -> dict | None:
    return db.one("SELECT id, username, role, created, last_login, tg_chat, plan FROM users WHERE id = ?", (user_id,))


def list_users() -> list[dict]:
    return db.rows("SELECT id, username, role, created, last_login, tg_chat, plan FROM users ORDER BY id")


def delete(user_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM watch_items WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    _exists_cache["yes"] = False  # re-checked on the next request


def admins() -> int:
    return db.one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'")["n"]


def set_password(user_id: int, password: str, end_sessions: bool = True) -> None:
    user = get(user_id)
    if not user:
        raise AccountError("Foydalanuvchi topilmadi")
    _valid_new(user["username"], password)
    with db.tx() as c:
        c.execute("UPDATE users SET pw = ? WHERE id = ?", (hash_password(password), user_id))
        if end_sessions:
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def temp_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def authenticate(username: str, password: str) -> dict | None:
    row = db.one("SELECT id, pw FROM users WHERE LOWER(username) = LOWER(?)", ((username or "").strip(),))
    ok = check_password(password or "", row["pw"] if row else _DUMMY)
    if not row or not ok:
        return None
    with db.tx() as c:
        c.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
    return get(row["id"])


# ------------------------------------------------------------------ throttling


def allowed(key: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
    _fails[key] = recent
    return len(recent) < FAIL_LIMIT


def failed(key: str) -> None:
    _fails.setdefault(key, []).append(time.monotonic())


def forget(key: str) -> None:
    _fails.pop(key, None)


# ------------------------------------------------------------------ sessions


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC).replace(microsecond=0)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (now.isoformat(),))
        c.execute(
            "INSERT INTO sessions (token, user_id, created, expires) VALUES (?,?,?,?)",
            (_digest(token), user_id, now.isoformat(), (now + timedelta(days=SESSION_DAYS)).isoformat()),
        )
    return token


def session_user(token: str | None) -> dict | None:
    if not token or len(token) > 200:
        return None
    row = db.one(
        """SELECT u.id, u.username, u.role, u.tg_chat, s.expires FROM sessions s
           JOIN users u ON u.id = s.user_id WHERE s.token = ?""",
        (_digest(token),),
    )
    if not row or row["expires"] < _now():
        return None
    row.pop("expires")
    return row


def end_session(token: str | None) -> None:
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token = ?", (_digest(token),))


# ------------------------------------------------------------------ Telegram link


def link_code(user_id: int) -> str:
    """A short code the person sends to the bot (/bogla CODE) to receive their alerts there."""
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    exp = (datetime.now(UTC) + timedelta(minutes=CODE_MINUTES)).replace(microsecond=0).isoformat()
    with db.tx() as c:
        c.execute("UPDATE users SET tg_code = ?, tg_code_exp = ? WHERE id = ?", (code, exp, user_id))
    return code


def link_chat(code: str, chat_id: str) -> dict | None:
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        return None
    row = db.one("SELECT id, tg_code_exp FROM users WHERE tg_code = ?", (code,))
    if not row or (row["tg_code_exp"] or "") < _now():
        return None
    with db.tx() as c:
        c.execute("UPDATE users SET tg_chat = NULL WHERE tg_chat = ?", (str(chat_id),))  # one person per chat
        c.execute(
            "UPDATE users SET tg_chat = ?, tg_code = NULL, tg_code_exp = NULL WHERE id = ?", (str(chat_id), row["id"])
        )
    return get(row["id"])


def unlink_chat(user_id: int) -> None:
    with db.tx() as c:
        c.execute("UPDATE users SET tg_chat = NULL WHERE id = ?", (user_id,))


def by_chat(chat_id) -> dict | None:
    return db.one("SELECT id, username, role, tg_chat FROM users WHERE tg_chat = ?", (str(chat_id),))
