# WorldScope Lite

Yengil versiya: bitta Python dasturi, bitta SQLite fayli. Docker, PostgreSQL, Redis kerak emas.
To'liq WorldScope (`frontend/`, `backend/`) o'zgarishsiz qoladi; bu papka undan mustaqil.

## Nima qiladi

- **Yig'adi** (har 30 daqiqada va "Yangilash" tugmasi bilan):
  - **Google News**: istalgan qidiruv va "Dunyo" bosh sahifasi. Kirishni bloklaydigan saytlarning
    sarlavhalari ham shu orqali keladi, masalan `site:reuters.com Uzbekistan`.
  - **GDELT**: butun dunyo matbuoti, 100 dan ortiq tilda.
  - **Telegram**: ochiq kanallar `t.me/s/kanal` veb-ko'rinishi orqali (Kun.uz, Daryo, BBC Русская служба oldindan qo'shilgan).
  - **RSS/Atom**: istalgan lenta.
- **Tahlil qiladi, kalitsiz**: mavzu va davlat teglari (o'zbek, rus, ingliz tillarida), bir xil
  voqea haqidagi xabarlarni guruhlaydi va nechta manba yozganini ko'rsatadi, eng ko'p tilga olingan davlat va mavzular.
- **AI tahlil** (`ANTHROPIC_API_KEY` bo'lsa): tanlangan davr va mavzu bo'yicha o'zbek, rus yoki
  ingliz tilida brif. Har bir da'vo manbaga `[raqam]` bilan bog'lanadi; fakt, taraf bayonoti va tahlil ajratiladi.

Faqat sarlavha, qisqa matn va havola saqlanadi. Paywall va bot himoyasi aylanib o'tilmaydi.
Instagram'ning ochiq API'si yo'q va scraping qoidalariga zid, shuning uchun qo'shilmagan.

## Kompyuterda ishga tushirish

```bash
cd lite
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                  # ixtiyoriy, AI tahlil uchun
uvicorn app:app --reload
```

Keyin http://localhost:8000 ni oching. Birinchi yig'ish 1-2 daqiqa oladi.

## Render'ga joylash (bepul)

1. Render → **New → Web Service** → `Axadxon01/worldscope` reposini tanlang.
2. **Root Directory**: `lite`, **Runtime**: Python, **Instance**: Free.
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. **Health Check Path**: `/healthz`
6. Environment: `APP_PASSWORD` (saytga kirish paroli, majburiy tavsiya) va xohlasangiz `ANTHROPIC_API_KEY`.

Bepul tarif cheklovlari: 15 daqiqa ishlatilmasa uxlaydi va ochilganda ~1 daqiqada uyg'onib,
yangiliklarni qayta yig'adi. Disk vaqtinchalik, shuning uchun qayta ishga tushganda saqlangan tahlillar va
qo'shgan manbalaringiz o'chadi. Doimiy manbalarni `sources_default.json` ga yozing.

## Sozlamalar

| O'zgaruvchi | Standart | Ma'nosi |
| --- | --- | --- |
| `APP_PASSWORD` | yo'q | Bo'lsa, sayt parol so'raydi (foydalanuvchi nomi istalgan) |
| `ANTHROPIC_API_KEY` | yo'q | AI tahlilni yoqadi |
| `WS_MODEL` | `claude-haiku-5-5` | Tahlil modeli; chuqurroq tahlil uchun `claude-sonnet-5-5` |
| `WS_REFRESH_MIN` | `30` | Necha daqiqada bir yig'ish |
| `WS_KEEP_DAYS` | `7` | Necha kunlik yangilik saqlanadi |
| `WS_DB_PATH` | `lite/data/worldscope.db` | SQLite fayli |

## Testlar

```bash
cd lite && pip install pytest && python -m pytest -q tests
```

Testlar internetsiz ishlaydi: har bir manba turi uchun namunaviy javoblar, teglash, guruhlash,
barcha sahifalar, parol va AI brifning havolalari tekshiriladi.
