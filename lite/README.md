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
- **AI tahlil** (`GEMINI_API_KEY` yoki `ANTHROPIC_API_KEY` bo'lsa): tanlangan davr va mavzu bo'yicha o'zbek, rus yoki
  ingliz tilida brif. Har bir da'vo manbaga `[raqam]` bilan bog'lanadi; fakt, taraf bayonoti va tahlil ajratiladi.

- **Xarita** (`/map`): AI yangiliklardan davlatlar orasidagi muhim bog'liqliklarni topib, dunyo
  xaritasida o'q bilan chizadi. Har bir o'q ustida mohiyatni bir gapda aytadigan bulutcha turadi. Bosilganda
  "tub mantiq" (manfaat, motiv, ehtimoliy oqibat), ishonch darajasi va dalil-manbalar ochiladi. `/map#3`
  havolasi 3-bog'liqlikni to'g'ridan-to'g'ri ochadi. AI bo'lmasa ham, bir xabarda birga tilga olingan
  davlatlar kulrang chiziq bilan ko'rsatiladi. Chegaralar saytning o'zida (Natural Earth), tashqi xizmat
  yoki kalit kerak emas.

- **Ikki davlat** (`/pair`): 196 davlatning (BMT a'zolari, Vatikan, Falastin, Tayvan) istalgan ikkitasini
  ro'yxatdan yoki xaritadan bosib tanlaysiz. Davlatlar uch guruhda: qudratli, o'rta va rivojlanayotgan
  (`pairs.py` dagi `TIER_1`/`TIER_2` ro'yxatlarini o'zgartirish mumkin). Dastur Google News'dan aynan shu
  juftlik haqidagi yangiliklarni qidiradi, AI esa munosabat darajasini (−5…+5), yaxshi va yomon tomonlarni,
  sohalar holatini, kuchlar nisbatini, uchinchi tomonlarni va ehtimolligi ko'rsatilgan 3 ta ssenariyni yozadi.
  Yangilikdan olingan da'volar raqam bilan manbaga bog'lanadi, umumiy bilimdan olinganlari "umumiy" deb belgilanadi.
  Natija saqlanadi; `/pair?a=UZB&b=AUS` havolasi uni qayta ochadi.

Faqat sarlavha, qisqa matn va havola saqlanadi. Paywall va bot himoyasi aylanib o'tilmaydi.
Instagram'ning ochiq API'si yo'q va scraping qoidalariga zid, shuning uchun qo'shilmagan.

## Kompyuterda ishga tushirish

```bash
cd lite
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GEMINI_API_KEY=...                           # ixtiyoriy, AI tahlil uchun (bepul tarif bor)
export ANTHROPIC_API_KEY=sk-ant-...                  # ixtiyoriy, Claude bilan tahlil
uvicorn app:app --reload
```

Keyin http://localhost:8000 ni oching. Birinchi yig'ish 1-2 daqiqa oladi.

## Render'ga joylash (bepul)

1. Render → **New → Web Service** → `Axadxon01/worldscope` reposini tanlang.
2. **Root Directory**: `lite`, **Runtime**: Python, **Instance**: Free.
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. **Health Check Path**: `/healthz`
6. Environment: `APP_PASSWORD` (saytga kirish paroli, majburiy tavsiya) va xohlasangiz `GEMINI_API_KEY` va/yoki `ANTHROPIC_API_KEY`.

Doimiy saqlash uchun `DATABASE_URL` qo'shing: Render Postgres (bepul tarifi 30 kunda o'chadi) yoki
Neon (bepul, muddatsiz). Ikkalasida ham "connection string"ni nusxalab, Environment'ga qo'ying.

Xavfsizlik: faqat `http(s)` havolalar saqlanadi; o'zgartiruvchi so'rovlar (POST) faqat shu saytning
o'z sahifalaridan qabul qilinadi; Content-Security-Policy va boshqa himoya sarlavhalari yoqilgan.
Bir vaqtda faqat bitta AI ish bajariladi va kunlik limit sarlavhada ko'rinadi.

Bepul tarif cheklovlari: 15 daqiqa ishlatilmasa uxlaydi va ochilganda ~1 daqiqada uyg'onib,
yangiliklarni qayta yig'adi. Disk vaqtinchalik, shuning uchun qayta ishga tushganda saqlangan tahlillar va
qo'shgan manbalaringiz o'chadi. Doimiy manbalarni `sources_default.json` ga yozing.

## Sozlamalar

| O'zgaruvchi | Standart | Ma'nosi |
| --- | --- | --- |
| `APP_PASSWORD` | yo'q | Bo'lsa, sayt parol so'raydi (foydalanuvchi nomi istalgan) |
| `GEMINI_API_KEY` | yo'q | Gemini bilan AI tahlil (aistudio.google.com/apikey, bepul tarif bor) |
| `ANTHROPIC_API_KEY` | yo'q | Claude bilan AI tahlil |
| `WS_PROVIDER` | `anthropic` | Ikkala kalit bo'lsa, qaysi biri birinchi sinaladi (`gemini` yoki `anthropic`); biri ishlamasa ikkinchisiga o'tadi |
| `WS_GEMINI_MODEL` | avtomatik | Bo'sh bo'lsa eng yangi barqaror Gemini Flash modeli o'zi tanlanadi |
| `WS_MODEL` | `claude-haiku-5-5` | Claude modeli; chuqurroq tahlil uchun `claude-sonnet-5-5` |
| `WS_REFRESH_MIN` | `30` | Necha daqiqada bir yig'ish |
| `WS_KEEP_DAYS` | `7` | Necha kunlik yangilik saqlanadi |
| `WS_DB_PATH` | `lite/data/worldscope.db` | SQLite fayli (`DATABASE_URL` bo'lmasa) |
| `DATABASE_URL` | yo'q | PostgreSQL manzili. Bo'lsa, tahlillar, xaritalar, juftliklar va manbalar qayta ishga tushganda o'chmaydi |
| `WS_DB_SCHEMA` | `lite` | Postgres'dagi sxema; bazani boshqa ilova bilan bo'lishish mumkin, ularning jadvallariga tegilmaydi |
| `WS_AI_DAILY_LIMIT` | `60` | Bir kunda nechta AI ish (tahlil, xarita, juftlik) bajarilishi mumkin |

## Testlar

```bash
cd lite && pip install pytest && python -m pytest -q tests
```

Postgres'da sinash uchun: `TEST_DATABASE_URL=postgresql://... python -m pytest -q tests`.
Testlar internetsiz ishlaydi: har bir manba turi uchun namunaviy javoblar, teglash, guruhlash,
barcha sahifalar, parol va AI brifning havolalari tekshiriladi.
