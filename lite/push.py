"""Web Push: the browser's own notifications, for the morning news and watch-list alerts.

The VAPID key pair identifies this site to the browsers' push services. It comes from WS_VAPID_PRIVATE
(a PEM, if set) or is made once and kept in the database; it is left out of backups, so a restored
copy makes a new key and people simply subscribe again.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC, datetime

import db

log = logging.getLogger("worldscope.push")
KEY_META = "vapid:private"
CONTACT = os.environ.get("WS_VAPID_CONTACT", "mailto:admin@worldscope.invalid")
MAX_FAILS = 3
MAX_SUBS = 20_000  # an open endpoint: a ceiling keeps a flood of fake subscriptions out


def _private_pem() -> str:
    env = os.environ.get("WS_VAPID_PRIVATE", "").strip()
    if env:
        return env
    row = db.one("SELECT value FROM meta WHERE key = ?", (KEY_META,))
    if row:
        return row["value"]
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with db.tx() as c:
        c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (KEY_META, pem))
    return db.one("SELECT value FROM meta WHERE key = ?", (KEY_META,))["value"]


def public_key() -> str:
    """The application server key the browser needs, base64url without padding."""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(_private_pem().encode(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def subscribe(sub: dict, user_id: int = 0, prefs: str = "") -> bool:
    endpoint = str(sub.get("endpoint", ""))
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        return False
    known = db.one("SELECT 1 AS x FROM push_subs WHERE endpoint = ?", (endpoint[:1000],))
    if not known and db.one("SELECT COUNT(*) AS n FROM push_subs")["n"] >= MAX_SUBS:
        return False
    with db.tx() as c:
        c.execute(
            "INSERT INTO push_subs (endpoint, keys, user_id, prefs, created) VALUES (?,?,?,?,?) "
            "ON CONFLICT (endpoint) DO UPDATE SET keys = excluded.keys, user_id = excluded.user_id, "
            "prefs = excluded.prefs, fails = 0",
            (endpoint[:1000], json.dumps({"p256dh": keys["p256dh"], "auth": keys["auth"]}), user_id, prefs[:200],
             datetime.now(UTC).isoformat(timespec="seconds")),
        )  # fmt: skip
    return True


def unsubscribe(endpoint: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))


def subscribers(user_id: int | None = None) -> list[dict]:
    if user_id is None:
        return db.rows("SELECT * FROM push_subs")
    return db.rows("SELECT * FROM push_subs WHERE user_id = ?", (user_id,))


def send(sub: dict, title: str, body: str, url: str = "/") -> bool:
    """One notification; a subscription that the push service calls gone is removed."""
    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": title[:80], "body": body[:180], "url": url}, ensure_ascii=False)
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": json.loads(sub["keys"])},
            data=payload,
            vapid_private_key=_private_pem(),
            vapid_claims={"sub": CONTACT},
            ttl=12 * 3600,
            timeout=15,
        )
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", 0)
        gone = status in (404, 410)
        with db.tx() as c:
            if gone:
                c.execute("DELETE FROM push_subs WHERE endpoint = ?", (sub["endpoint"],))
            else:
                c.execute("UPDATE push_subs SET fails = fails + 1 WHERE endpoint = ?", (sub["endpoint"],))
                c.execute("DELETE FROM push_subs WHERE endpoint = ? AND fails >= ?", (sub["endpoint"], MAX_FAILS))
        log.info("push failed (%s)%s", status or "?", ", subscription removed" if gone else "")
        return False
    except Exception:
        log.exception("push failed")
        return False
