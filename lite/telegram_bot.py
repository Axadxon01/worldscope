"""Telegram bot: daily digest, followed pairs and alerts when a relationship moves.

Set TELEGRAM_BOT_TOKEN (from @BotFather). The app registers its webhook by itself at start-up,
using PUBLIC_URL and a secret derived from the token, so Telegram's calls are the only ones the
webhook accepts. Commands are in Uzbek.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re

import httpx

import analyze
import collectors
import db
import pairs

log = logging.getLogger("worldscope.telegram")


def esc(text) -> str:
    """Escape for Telegram HTML text; apostrophes stay readable (attributes use html.escape)."""
    return html.escape(str(text), quote=False)


PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://worldscope-lite.onrender.com").rstrip("/")
ALERT_DELTA = int(os.environ.get("WS_ALERT_DELTA", "2"))


def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def enabled() -> bool:
    return bool(token())


def webhook_secret() -> str:
    """Telegram echoes this in X-Telegram-Bot-Api-Secret-Token; only it may call the webhook."""
    return hashlib.sha256(("worldscope-webhook:" + token()).encode()).hexdigest()[:48]


async def api(method: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        r = await client.post(f"https://api.telegram.org/bot{token()}/{method}", json=params)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        log.warning("telegram %s failed: %s", method, str(data.get("description", r.status_code))[:200])
    return data


async def send(chat_id: str | int, text: str) -> bool:
    res = await api(
        "sendMessage", chat_id=chat_id, text=text[:4000], parse_mode="HTML", link_preview_options={"is_disabled": True}
    )
    if res.get("error_code") == 403:  # the user blocked the bot: stop writing to them
        with db.tx() as c:
            c.execute("UPDATE tg_subscribers SET active = 0 WHERE chat_id = ?", (str(chat_id),))
    return bool(res.get("ok"))


async def setup_webhook() -> None:
    if not enabled():
        return
    await api(
        "setWebhook",
        url=f"{PUBLIC_URL}/telegram/webhook",
        secret_token=webhook_secret(),
        allowed_updates=["message"],
        drop_pending_updates=False,
    )
    await api(
        "setMyCommands",
        commands=[
            {"command": "bugun", "description": "Bugungi asosiy voqealar"},
            {"command": "kuzat", "description": "Juftlikni kuzatish: /kuzat UZB AUS"},
            {"command": "kuzatuvlar", "description": "Kuzatayotgan juftliklarim"},
            {"command": "juft", "description": "Juftlik xulosasi: /juft USA RUS"},
            {"command": "stop", "description": "Kunlik xabarlarni to'xtatish"},
            {"command": "yordam", "description": "Buyruqlar"},
        ],
    )


# ------------------------------------------------------------------ countries by code or name


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower().translate(analyze._APOS).replace("'", ""))


_NAMES: dict[str, str] = {}
for _iso in pairs.SOVEREIGN:
    if _iso in analyze.COUNTRY_INFO:
        for _n in {
            _iso,
            analyze.display_name(_iso),
            analyze.COUNTRY_INFO[_iso]["name"],
            analyze.COUNTRY_INFO[_iso].get("name_long", ""),
            pairs.search_name(_iso),
        }:
            if _n:
                _NAMES.setdefault(_norm(_n), _iso)


def resolve(word: str) -> str | None:
    """'uzb', "O'zbekiston", 'Uzbekistan', 'aqsh' → ISO3 code."""
    return _NAMES.get(_norm(word))


def _two_countries(args: str) -> tuple[str, str] | None:
    parts = [p for p in re.split(r"[\s,;/–-]+", args.strip()) if p]
    # try every split point so multi-word names ("janubiy koreya") work
    for i in range(1, len(parts)):
        a, b = resolve(" ".join(parts[:i])), resolve(" ".join(parts[i:]))
        if a and b and a != b:
            return a, b
    return None


# ------------------------------------------------------------------ texts

HELP = """<b>WorldScope</b> — xalqaro yangiliklar tahlili.

/bugun — bugungi asosiy voqealar va ularning mohiyati
/kuzat UZB AUS — ikki davlat munosabatini kuzatish (kod yoki nom: <i>/kuzat O'zbekiston Xitoy</i>)
/kuzatuvlar — kuzatayotgan juftliklaringiz
/bekor UZB AUS — kuzatuvni o'chirish
/juft USA RUS — saqlangan tahlil xulosasi
/stop — kunlik xabarlarni to'xtatish, /start — qayta yoqish

Har kuni ertalab qisqa digest keladi. Kuzatilgan juftlik har hafta qayta tahlil qilinadi; munosabat keskin
o'zgarsa darhol xabar beraman. Batafsil sahifalar saytda (parol bilan)."""


def pair_name(a: str, b: str) -> str:
    return f"{esc(analyze.display_name(a))} ↔ {esc(analyze.display_name(b))}"


def _pair_line(a: str, b: str, data: dict) -> str:
    e = esc
    return (
        f"<b>{e(analyze.display_name(a))} ↔ {e(analyze.display_name(b))}</b>: "
        f"{data['score']:+d}, {e(data.get('label', ''))}\n💡 {e(data.get('essence', ''))}"
    )


def pair_link(a: str, b: str, lang: str = "uz") -> str:
    return f"{PUBLIC_URL}/pair?a={a}&b={b}&lang={lang}"


# ------------------------------------------------------------------ commands


async def handle(update: dict, analyze_pair=None) -> None:
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    if not chat.get("id") or not text.startswith("/"):
        if chat.get("id") and text:
            await send(chat["id"], "Buyruqlar ro'yxati: /yordam")
        return
    chat_id = str(chat["id"])
    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    now = collectors.now_iso()

    if cmd == "/start":
        name = (chat.get("first_name") or chat.get("title") or "")[:60]
        with db.tx() as c:
            c.execute(
                "INSERT INTO tg_subscribers (chat_id, created, active, name) VALUES (?,?,1,?) "
                "ON CONFLICT (chat_id) DO UPDATE SET active = 1",
                (chat_id, now, name),
            )
        await send(chat_id, "Xush kelibsiz! Endi har kuni ertalab asosiy voqealarni yuboraman.\n\n" + HELP)
    elif cmd == "/stop":
        with db.tx() as c:
            c.execute("UPDATE tg_subscribers SET active = 0 WHERE chat_id = ?", (chat_id,))
        await send(
            chat_id,
            "Kunlik xabarlar to'xtatildi. Kuzatuvlaringiz bo'yicha ogohlantirishlar qoladi. Qayta yoqish: /start",
        )
    elif cmd in ("/yordam", "/help"):
        await send(chat_id, HELP)
    elif cmd == "/bugun":
        row = db.one("SELECT body, created FROM digests ORDER BY id DESC LIMIT 1")
        await send(chat_id, row["body"] if row else "Bugungi digest hali tayyor emas. Ertalab yuboriladi.")
    elif cmd in ("/kuzat", "/bekor", "/juft"):
        pair = _two_countries(args)
        if not pair:
            await send(
                chat_id,
                f"Ikki davlatni yozing, masalan: <code>{cmd} UZB AUS</code> yoki <code>{cmd} O'zbekiston Xitoy</code>",
            )
            return
        a, b = pair
        k = pairs.key(a, b, "uz")
        row = db.one("SELECT data FROM pairs WHERE key = ?", (k,))
        if cmd == "/kuzat":
            with db.tx() as c:
                c.execute(
                    "INSERT INTO tg_follows (chat_id, key, a, b, lang, created) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT DO NOTHING",
                    (chat_id, k, a, b, "uz", now),
                )
            body = f"✅ Kuzatuvga olindi: <b>{pair_name(a, b)}</b>.\n"
            if row:
                body += "\n" + _pair_line(a, b, analyze_json(row["data"]))
            else:
                body += "Birinchi tahlil haftalik yangilanishda tayyorlanadi."
            await send(chat_id, body + f"\n\n{pair_link(a, b)}")
        elif cmd == "/bekor":
            with db.tx() as c:
                c.execute("DELETE FROM tg_follows WHERE chat_id = ? AND key = ?", (chat_id, k))
            await send(chat_id, "Kuzatuv o'chirildi.")
        else:
            if row:
                await send(chat_id, _pair_line(a, b, analyze_json(row["data"])) + f"\n\n{pair_link(a, b)}")
            else:
                await send(
                    chat_id, f"Bu juftlik hali tahlil qilinmagan. Saytda yarating yoki kuzatuvga oling: /kuzat {a} {b}"
                )
    elif cmd == "/kuzatuvlar":
        rows = db.rows("SELECT a, b, key FROM tg_follows WHERE chat_id = ? ORDER BY created", (chat_id,))
        if not rows:
            await send(chat_id, "Hali juftlik kuzatmayapsiz. Masalan: /kuzat UZB CHN")
            return
        lines = []
        for r in rows:
            d = db.one("SELECT data FROM pairs WHERE key = ?", (r["key"],))
            score = f"{analyze_json(d['data'])['score']:+d}" if d else "—"
            lines.append(f"• {pair_name(r['a'], r['b'])}: {score}")
        await send(chat_id, "<b>Kuzatuvlaringiz</b>\n" + "\n".join(lines))
    else:
        await send(chat_id, "Bunday buyruq yo'q. /yordam")


def analyze_json(text: str) -> dict:
    import json

    return json.loads(text)


# ------------------------------------------------------------------ digest

DIGEST_SYSTEM = """You write the morning digest of WorldScope for Uzbek readers. From numbered news items
(the widest-reported stories of the last 24 hours, Central Asia first), pick the 5 that matter most for
someone in Uzbekistan who follows world affairs. Return JSON only:
{"items": [{"n": 3, "emoji": "one fitting emoji", "title": "max 10 words, Uzbek Latin",
            "essence": "one sentence, max 25 words, Uzbek Latin: why it matters, not just what happened"}]}
Use only item numbers from the list; never invent facts. Exactly 5 items, most important first."""


def digest_html(picked: list[dict], date: str, ai: bool) -> str:
    e = esc
    lines = [f"🌍 <b>WorldScope — {date}</b>", ""]
    for i, p in enumerate(picked, 1):
        head = f"{p.get('emoji', '•')} <b>{e(p['title'])}</b>"
        lines.append(head)
        if p.get("essence"):
            lines.append(e(p["essence"]))
        lines.append(
            f'<a href="{html.escape(p["url"], quote=True)}">{e(p["publisher"])}</a>'
            + (f" va yana {p['more']} ta manba" if p.get("more") else "")
        )
        if i < len(picked):
            lines.append("")
    lines += ["", f"Batafsil: {PUBLIC_URL}" + ("" if ai else "\n<i>(AI o'chiq: sarlavhalar tahlilsiz)</i>")]
    return "\n".join(lines)


def build_digest(articles: list[dict], use_ai: bool) -> tuple[str, str]:
    """Returns (Telegram HTML, model). Runs in a thread; the AI part is optional."""
    groups = analyze.cluster(articles)
    groups.sort(
        key=lambda g: (
            any("UZB" in a["countries"] or "central-asia" in a["categories"] for a in g),
            len({a["publisher"] for a in g}),
        ),
        reverse=True,
    )
    items = [g[0] | {"more": len({a["publisher"] for a in g}) - 1} for g in groups[:25]]
    date = collectors.now_iso()[:10]
    if not items:
        return "Bugun yangiliklar yig'ilmadi.", ""
    if use_ai:
        try:
            text, model = analyze.complete(
                DIGEST_SYSTEM, "News items:\n" + "\n".join(analyze.numbered(items)), json_mode=True
            )
            raw = analyze._json_from(text)
            picked = []
            for it in raw.get("items") or []:
                try:
                    n = int(it.get("n"))
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= len(items) and all(p["n"] != n for p in picked):
                    src = items[n - 1]
                    picked.append(
                        {
                            "n": n,
                            "emoji": str(it.get("emoji", "•"))[:4],
                            "title": str(it.get("title") or src["title"])[:140],
                            "essence": str(it.get("essence", ""))[:300],
                            "url": src["url"],
                            "publisher": src["publisher"],
                            "more": src["more"],
                        }
                    )
            if picked:
                return digest_html(picked[:5], date, True), model
        except analyze.AIError as e:
            log.warning("digest AI failed, sending headlines: %s", e)
    picked = [
        {
            "n": i + 1,
            "emoji": "•",
            "title": a["title"],
            "essence": "",
            "url": a["url"],
            "publisher": a["publisher"],
            "more": a["more"],
        }
        for i, a in enumerate(items[:5])
    ]
    return digest_html(picked, date, False), ""


async def broadcast(text: str) -> int:
    sent = 0
    for r in db.rows("SELECT chat_id FROM tg_subscribers WHERE active = 1"):
        if await send(r["chat_id"], text):
            sent += 1
    return sent


# ------------------------------------------------------------------ alerts


def alert_text(a: str, b: str, new: dict, old: dict) -> str:
    e = esc
    arrow = "📈" if new["score"] > old["score"] else "📉"
    pc = new.get("previous_check") or {}
    lines = [
        f"{arrow} <b>{e(analyze.display_name(a))} ↔ {e(analyze.display_name(b))}</b>: munosabat o'zgardi",
        f"{old['score']:+d} ({e(old.get('label', ''))}) → <b>{new['score']:+d}</b> ({e(new.get('label', ''))})",
        "",
        f"💡 {e(new.get('essence', ''))}",
    ]
    if pc.get("change"):
        lines += ["", f"Nima o'zgardi: {e(pc['change'])}"]
    lines += ["", pair_link(a, b)]
    return "\n".join(lines)


def moved(new: dict, old: dict | None) -> bool:
    return bool(old) and abs(int(new["score"]) - int(old["score"])) >= ALERT_DELTA
