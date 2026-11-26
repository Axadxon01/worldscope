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
from urllib.parse import quote_plus

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


_ME: dict = {}


def username() -> str:
    """The bot's @name, learned from Telegram at start-up (or TELEGRAM_BOT_NAME), for t.me links."""
    return os.environ.get("TELEGRAM_BOT_NAME", "") or _ME.get("username", "")


async def setup_webhook() -> None:
    if not enabled():
        return
    me = await api("getMe")
    if me.get("ok"):
        _ME.update(me.get("result") or {})
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
            {"command": "qarz", "description": "Davlat qarzlari tahlili: /qarz UZB"},
            {"command": "xavf", "description": "Davlat xavf indeksi: /xavf UZB"},
            {"command": "sanksiya", "description": "Sanksiya ro'yxatlarida qidirish: /sanksiya Ivanov"},
            {"command": "mening", "description": "Mening kuzatuv ro'yxatim"},
            {"command": "bogla", "description": "Sayt akkauntini bog'lash: /bogla KOD"},
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
/qarz UZB — davlat qarzlari va qarzdan keyingi o'zgarishlar
/xavf UZB — davlat xavf indeksi (0–100) va uning tarkibi
/sanksiya Nom — OFAC, BMT, YeI va Buyuk Britaniya ro'yxatlarida nom qidirish
/bogla KOD — saytdagi akkauntni shu chatga bog'lash (kod "Profil" sahifasida)
/mening — kuzatayotgan davlat va kompaniyalaringiz holati
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
        if args.strip().lower().startswith("bogla_"):  # t.me/<bot>?start=bogla_CODE from the account page
            await link_account(chat_id, args.strip()[6:])
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
    elif cmd == "/qarz":
        iso = resolve(args.strip()) if args.strip() else None
        if not iso:
            await send(chat_id, "Davlatni yozing, masalan: <code>/qarz UZB</code> yoki <code>/qarz Pokiston</code>")
            return
        await send(chat_id, debt_text(iso))
    elif cmd == "/xavf":
        iso = resolve(args.strip()) if args.strip() else None
        if not iso:
            await send(chat_id, "Davlatni yozing, masalan: <code>/xavf UZB</code> yoki <code>/xavf Turkiya</code>")
            return
        await send(chat_id, await risk_text(iso))
    elif cmd == "/sanksiya":
        await send(chat_id, sanctions_text(args.strip()))
    elif cmd == "/bogla":
        await link_account(chat_id, args.strip())
    elif cmd == "/mening":
        await send(chat_id, mine_text(str(chat_id)))
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


async def risk_text(iso: str) -> str:
    import risk  # local, like debt below

    try:
        rows, _ = await risk.build()
    except Exception:
        return "Jahon banki ma'lumotlarini hozir olib bo'lmadi, birozdan keyin urinib ko'ring."
    r = next((x for x in rows if x["iso"] == iso), None)
    title = f"🧭 <b>{esc(analyze.display_name(iso))}: xavf indeksi</b>"
    if not r or r["score"] is None:
        return f"{title}\nMa'lumot yetarli emas."
    scored = sum(x["score"] is not None for x in rows)
    lines = [title, f"<b>{r['score']}</b>/100 — {r['band']} ({r['rank']}-o'rin {scored} tadan, 1 = eng xavfli)", ""]
    for c in r["components"]:
        v = "—" if c["value"] is None else f"{c['value']:.0f}"
        lines.append(f"• {esc(c['label'])} ({c['weight']}%): {v}")
    lines += ["", f"{PUBLIC_URL}/risk?c={iso}"]
    return "\n".join(lines)


async def link_account(chat_id, code: str) -> None:
    import accounts

    u = accounts.link_chat(code, str(chat_id))
    if u:
        await send(
            chat_id, f"✅ Bog'landi: <b>{esc(u['username'])}</b>. Kuzatuv ogohlantirishlari endi shu yerga keladi."
        )
    else:
        await send(chat_id, "Kod noto'g'ri yoki eskirgan. Saytda <b>Profil</b> sahifasidan yangi kod oling.")


def mine_text(chat_id: str) -> str:
    import accounts
    import sanctions
    import watch

    u = accounts.by_chat(chat_id)
    if not u:
        return (
            "Bu chat saytdagi akkauntga bog'lanmagan. "
            "Saytda <b>Profil</b> → kod oling va <code>/bogla KOD</code> yuboring."
        )
    items = watch.items(u["id"])
    if not items:
        return f"Kuzatuv ro'yxati bo'sh. Saytda qo'shing: {PUBLIC_URL}/watch"
    lines = [f"📌 <b>{esc(u['username'])}: kuzatuv ro'yxati</b>", ""]
    for it in items:
        if it["kind"] == "country":
            st = it["state"]
            known = st.get("score") is not None
            score = f"{st['score']} ({esc(st.get('band', ''))})" if known else "hali tekshirilmagan"
            lines.append(f"🧭 {esc(analyze.display_name(it['value']))}: xavf {score}")
        else:
            n = len(watch.matches(it["value"])) if sanctions.loaded() else 0
            found = f"⚠️ {n} ta moslik" if n else "moslik yo'q"
            lines.append(f"⚖️ {esc(it['value'])}: {found}")
    lines += ["", f"{PUBLIC_URL}/watch"]
    return "\n".join(lines)


def sanctions_text(q: str) -> str:
    import sanctions

    if len(q) < 3:
        return "Nomni yozing, masalan: <code>/sanksiya Sberbank</code>"
    if not sanctions.loaded():
        return "Sanksiya ro'yxatlari hali yuklanmagan, birozdan keyin urinib ko'ring."
    found = sanctions.search(q, limit=6)
    head = f"🔎 <b>Sanksiya ro'yxatlari: {esc(q)}</b>"
    if not found:
        return f"{head}\nTopilmadi. Bu “ro'yxatda yo'q” degani emas — nom boshqacha yozilgan bo'lishi mumkin."
    lines = [head, ""]
    for r in found:
        where = ", ".join(analyze.display_name(c) for c in r["countries"][:3])
        lines.append(f"• <b>{esc(r['name'])}</b> — {r['list']}, {esc(', '.join(r['programs'][:2]))}"
                     f"{' · ' + esc(where) if where else ''} ({sanctions.match_label(r['score'])})")  # fmt: skip
    lines += ["", "⚖️ Nom mosligi — bu o'sha shaxs ekanini anglatmaydi. Rasmiy manbada tekshiring.",
              f"{PUBLIC_URL}/sanctions?q={quote_plus(q)}"]  # fmt: skip
    return "\n".join(lines)


STATE_UZ = {"improved": "▲", "worsened": "▼", "mixed": "◆", "unclear": "·"}


def debt_text(iso: str) -> str:
    """The stored debt report in a few lines, or a pointer to the page."""
    link = f"{PUBLIC_URL}/debt?c={iso}"
    row = db.one("SELECT data FROM debt_reports WHERE key = ?", (f"{iso}-uz",))
    title = f"💰 <b>{esc(analyze.display_name(iso))}: qarzlar</b>"
    if not row:
        return f"{title}\nAI tahlili hali yo'q. Raqamlar va tahlil sahifasi: {link}"
    r = analyze_json(row["data"])
    import debt  # local: the bot module stays light when debts are not used

    areas = dict(debt.AREAS)
    lines = [title, "", esc(r.get("summary", "")), ""]
    for e in r.get("effects", []):
        if e.get("finding"):
            lines.append(
                f"{STATE_UZ.get(e['state'], '·')} <b>{esc(areas.get(e['area'], e['area']))}</b>: {esc(e['finding'])}"
            )
    lines += ["", link]
    return "\n".join(lines)


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
