# ruff: noqa: E501  (a table of translations reads best one phrase per line)
"""Russian next to Uzbek: the language of the page comes from the "ws_lang" cookie.

Templates write Uzbek and wrap it in _("..."); in Russian the same text is looked up here, and
anything not listed stays Uzbek (the deeper analysis tools are Uzbek only, and say so).
"""

from __future__ import annotations

from contextvars import ContextVar

from markupsafe import Markup

LANGS = {"uz": "O'zbekcha", "ru": "Русский"}
COOKIE = "ws_lang"
_lang: ContextVar[str] = ContextVar("lang", default="uz")


def set_lang(code: str | None) -> str:
    code = code if code in LANGS else "uz"
    _lang.set(code)
    return code


def lang() -> str:
    return _lang.get()


def _(text: str) -> Markup:
    """Only the site's own fixed strings pass through here, so they are marked safe as they are."""
    return Markup(RU.get(text, text) if _lang.get() == "ru" else text)


def pick(line: dict | None, key: str) -> str:
    """An AI-written field in the page's language; '' when that language has none."""
    if not line:
        return ""
    if _lang.get() == "ru":
        return line.get(f"{key}_ru", "") or ""
    return line.get(key, "") or ""


def ago(minutes: int) -> str:
    ru = _lang.get() == "ru"
    if minutes < 1:
        return "только что" if ru else "hozir"
    if minutes < 60:
        return f"{minutes} мин назад" if ru else f"{minutes} daq oldin"
    if minutes < 1440:
        return f"{minutes // 60} ч назад" if ru else f"{minutes // 60} soat oldin"
    return f"{minutes // 1440} дн назад" if ru else f"{minutes // 1440} kun oldin"


RU = {
    # the frame of every page
    "Bugun": "Сегодня", "Globus": "Глобус", "Davlatlar": "Страны", "Sanksiyalar": "Санкции", "Tahlil": "Анализ",
    "Mening": "Моё", "Kirish": "Войти", "Profil va Telegram": "Профиль и Telegram", "Boshqaruv": "Управление",
    "Chiqish": "Выйти", "Bo'limlar": "Разделы", "Asosiy bo'limlar": "Основные разделы",
    "yangilandi": "обновлено", "Yangiliklar har 25 daqiqada avtomatik yig'iladi": "Новости собираются каждые 25 минут",
    "Tun / pergament rejimi": "Ночной / дневной режим", "Rejimni almashtirish": "Сменить режим",
    "WorldScope yangiliklarni o'zi yozmaydi: sarlavha, qisqa matn va havola saqlanadi, to'liq matn manbaning o'zida.":
        "WorldScope не пишет новости сам: хранятся заголовок, краткий текст и ссылка, полный текст — у источника.",
    "Xato topdingizmi?": "Нашли ошибку?", "Til": "Язык", "Asosiy qismga o'tish": "Перейти к содержанию",
    "WorldScope — dunyo yangiliklari oddiy tilda": "WorldScope — мировые новости простым языком",
    "Dunyo yangiliklari oddiy o'zbek tilida: bugun nima bo'ldi, bizga qanday ta'sir qiladi, davlatlar o'rtasidagi aloqalar globusda, sanksiyalar ro'yxati.":
        "Мировые новости простым языком: что случилось сегодня, как это касается Узбекистана, связи стран на глобусе, санкционные списки.",
    "Bu bo'lim hozircha faqat o'zbek tilida.": "Этот раздел пока только на узбекском.",
    # the front page
    "Qaytganingiz bilan!": "С возвращением!", "Oxirgi tashrifingizdan beri": "С вашего прошлого визита",
    "ta muhim voqea": "важных событий", "shundan": "из них", "tasi sizni qiziqtirgan mavzularda":
        "по вашим интересам", "Yangilari": "Новые отмечены", "belgisi bilan.": ".", "Yangi": "Новое",
    "Bugun dunyoda nima bo'ldi?": "Что произошло в мире сегодня?",
    "Eng ko'p nashr yozgan voqealar — oddiy tilda, 3 daqiqada.": "События, о которых пишут больше всего, — простым языком, за 3 минуты.",
    "Siz uchun": "Для вас", "⚙ Qiziqishlarni tanlash": "⚙ Выбрать интересы", "Siz tanlaganlar:": "Ваш выбор:",
    "Bugun siz tanlagan davlat va mavzular bo'yicha yuqoridagilardan boshqa yangi voqea yo'q.":
        "Сегодня по вашим странам и темам других событий нет.",
    "Sizni qaysi davlatlar va mavzular qiziqtiradi?": "Какие страны и темы вам интересны?",
    "Tanlang — shu yerda faqat siz uchun muhim voqealar chiqadi va keyingi safar nima yangi ekanini aytamiz.":
        "Выберите — здесь будут только важные для вас события, а в следующий раз мы покажем, что нового.",
    "Tanlash": "Выбрать", "Dunyo xaritasi": "Карта мира", "To'liq globus →": "Весь глобус →",
    "Davlatlar o'rtasida hozir nima bo'layotgani. Rasmchani bosing — oddiy tilda tushuntiradi.":
        "Что сейчас происходит между странами. Нажмите на значок — объясним простыми словами.",
    "Istalgan davlatni bosing — uning boshqa davlatlar bilan barcha aloqalari ochiladi.":
        "Нажмите на любую страну — откроются все её связи с другими странами.",
    "Bugungi xarita hali tayyorlanmoqda. Hozircha globusda yangiliklarda tez-tez birga tilga olingan davlatlar kulrang chiziq bilan bog'langan. Istalgan davlatni bosing — uning aloqalari ochiladi.":
        "Сегодняшняя карта ещё готовится. Пока серыми линиями связаны страны, которые часто упоминаются вместе. Нажмите на страну — откроются её связи.",
    "Barcha yangiliklar": "Все новости", "ta voqea · qidirish va saralash": "событий · поиск и фильтры",
    "Haftalik test": "Тест недели", "5 ta savol: bu hafta dunyoni qanchalik kuzatdingiz?":
        "5 вопросов: насколько внимательно вы следили за миром на этой неделе?",
    "Har kuni ertalab Telegramda": "Каждое утро в Telegram", "Kunning asosiy voqealari —": "Главное за день —",
    "Ertalabki bildirishnoma": "Утреннее уведомление",
    "Har kuni ertalab siz uchun eng muhim voqea — telefoningizga.": "Каждое утро — самое важное для вас событие на телефон.",
    "🔔 Yoqish": "🔔 Включить", "Davom etish": "Дальше",
    "Sizni nima qiziqtiradi?": "Что вам интересно?",
    "Bir nechtasini bosing. Keyin bosh sahifada \"Siz uchun\" bo'limida aynan shular chiqadi. Istalgan vaqt o'zgartirasiz.":
        "Отметьте несколько. На главной в разделе «Для вас» появятся именно они. Изменить можно в любой момент.",
    "Mavzular": "Темы", "Keyinroq": "Позже", "Saqlash": "Сохранить",
    "Yangiliklar hali yig'ilmagan": "Новости ещё не собраны",
    # story cards and the story page
    "🇺🇿 Bizga ta'siri:": "🇺🇿 Что это значит для нас:", "manba": "источн.", "ta manba": "источников",
    "Asl sarlavha va": "Оригинальный заголовок и", "↗ Ulashish": "↗ Поделиться", "Ulashish": "Поделиться",
    "Voqea qanday rivojlandi": "Как развивалось событие", "kun davomida yozildi": "дн. о нём писали",
    "ning aloqalarini globusda ko'rish →": ": связи на глобусе →", "Voqea": "Событие",
    "Voqea topilmadi": "Событие не найдено",
    "U eskirgan bo'lishi mumkin: sayt yangiliklarni bir hafta saqlaydi.": "Возможно, оно устарело: новости хранятся неделю.",
    "Bugungi voqealar →": "События сегодня →",
    "WorldScope matnni o'zi yozmaydi: har bir havola asl nashrga olib boradi. Oddiy tildagi qatorlarni AI yozgan va xato qilishi mumkin.":
        "WorldScope не пишет тексты сам: каждая ссылка ведёт к первоисточнику. Простые пояснения пишет ИИ, он может ошибаться.",
    "Xato topdingizmi? Xabar bering": "Нашли ошибку? Сообщите",
    # the full feed
    "Qidirish: davlat, mavzu, nashr…": "Поиск: страна, тема, издание…", "Barcha mavzular": "Все темы",
    "Ko'rsatish": "Показать", "Tozalash": "Сбросить", "Davlat:": "Страна:", "olib tashlash": "убрать",
    "davlat sahifasi →": "страница страны →", "Ko'p tilga olingan davlatlar:": "Чаще всего упоминаются:",
    "Mavzular:": "Темы:", "Voqealar": "События", "Har bir xabar alohida": "Каждое сообщение отдельно",
    "Bu filtrlar bo'yicha yangilik topilmadi.": "По этим фильтрам новостей нет.", "← Oldingi": "← Назад",
    "Keyingi →": "Дальше →", "sahifa": "стр.", "ta": "",
    "6 soat": "6 часов", "24 soat": "24 часа", "2 kun": "2 дня", "7 kun": "7 дней",
    # tools
    "Tahlil asboblari": "Инструменты анализа",
    "Chuqurroq bilmoqchi bo'lsangiz — har bir asbob bitta savolga javob beradi.":
        "Хотите глубже — каждый инструмент отвечает на один вопрос.",
    "Hamma xabarlar: qidirish, mavzu va davlat bo'yicha saralash.": "Все сообщения: поиск, темы, страны.",
    "Ikki davlat": "Две страны", "Ikki davlat o'rtasida nima bo'lyapti va keyin nima bo'lishi mumkin?":
        "Что происходит между двумя странами и что может быть дальше?",
    "Xavf indeksi": "Индекс риска", "Qaysi davlat bilan ishlash xavfliroq? 0 dan 100 gacha ball.":
        "С какой страной работать рискованнее? Балл от 0 до 100.",
    "Bu kompaniya yoki odam qora ro'yxatdami?": "Есть ли компания или человек в чёрном списке?",
    "Qarzlar": "Долги", "Davlat kimga va qancha qarzdor?": "Кому и сколько должна страна?",
    "OAV talqini": "Подача в СМИ", "Bitta voqeani turli davlatlar gazetalari qanday yozyapti?":
        "Как СМИ разных стран пишут об одном событии?",
    "Bashoratlar": "Прогнозы", "AI bashoratlari qanchalik to'g'ri chiqdi? O'zingiz ham ovoz bering.":
        "Насколько точны прогнозы ИИ? Голосуйте сами.",
    "AI tahlil": "ИИ-анализ", "Tanlagan mavzuingiz bo'yicha manbali qisqa tahlil.": "Краткий анализ с источниками по вашей теме.",
    "Manbalar": "Источники", "Yangiliklar qayerdan yig'iladi; manba qo'shish va o'chirish.":
        "Откуда собираются новости; добавить и убрать источник.",
    # quiz
    "🧩 Haftalik test": "🧩 Тест недели",
    "Bu haftaning testi hali tayyor emas": "Тест этой недели ещё не готов",
    # accounts
    "Ro'yxatdan o'tish": "Регистрация", "Login": "Логин", "Parol": "Пароль", "Akkaunt yaratish": "Создать аккаунт",
    "Parol yana bir marta": "Пароль ещё раз", "Login (3–32 belgi)": "Логин (3–32 символа)",
    "Parol (kamida 8 belgi)": "Пароль (не менее 8 символов)", "Akkauntingiz bormi?": "Уже есть аккаунт?",
    "Akkauntingiz yo'qmi?": "Нет аккаунта?", "Ro'yxatdan o'tish — bepul, 20 soniya": "Регистрация — бесплатно, 20 секунд",
    "Yangiliklar, globus va tahlillar hammaga ochiq. Akkaunt shaxsiy qismlarni yoqadi: davlat va kompaniyalarni kuzatish, ogohlantirishlar, Telegram dayjesti, AI tahlillarini yaratish.":
        "Новости, глобус и анализ открыты всем. Аккаунт включает личное: слежение за странами и компаниями, оповещения, дайджест в Telegram, создание ИИ-анализов.",
    "Bepul. Akkaunt bilan davlat va kompaniyalarni kuzatasiz, o'zgarish bo'lsa ogohlantirish olasiz, Telegramga shaxsiy dayjest ulaysiz va AI tahlillarini yaratasiz.":
        "Бесплатно. С аккаунтом вы следите за странами и компаниями, получаете оповещения, подключаете личный дайджест в Telegram и создаёте ИИ-анализы.",
    # feedback and offline
    "Rahmat! Xabaringiz administratorga yetkazildi.": "Спасибо! Сообщение передано администратору.",
    "← Ortga qaytish": "← Вернуться", "Nima noto'g'ri?": "Что не так?", "Yuborish": "Отправить",
    "Noto'g'ri tarjima, xato fakt, ishlamayotgan tugma yoki taklif — hammasi foydali. Qisqa yozing.":
        "Неверный перевод, ошибка в фактах, неработающая кнопка или идея — всё полезно. Коротко.",
    "Javob kerak bo'lsa — Telegram yoki email (ixtiyoriy)": "Если нужен ответ — Telegram или email (необязательно)",
    "Sahifa:": "Страница:", "📡 Internet yo'q": "📡 Нет интернета", "Qayta urinish": "Повторить",
    "Ulanish tiklangach, sahifa yangilanadi. Avval ochgan sahifalaringiz internetsiz ham ochiladi.":
        "Когда связь появится, страница обновится. Открытые ранее страницы работают и без интернета.",
    # topics
    "urush yoki to'qnashuv": "война и конфликты", "qurol va armiya": "оружие и армия",
    "davlatlar o'rtasidagi muzokara": "переговоры между странами", "pul va iqtisod": "деньги и экономика",
    "savdo: mol sotish va sotib olish": "торговля", "gaz, neft va elektr": "газ, нефть и электричество",
    "texnologiya": "технологии", "internet xavfsizligi": "кибербезопасность", "tabiat va ob-havo": "природа и климат",
    "sog'liq": "здоровье", "odamlar hayoti": "общество",
    # my page
    "Mening sahifam": "Моя страница", "🎯 Qiziqishlarim": "🎯 Мои интересы", "Qiziqishlarni o'zgartirish": "Изменить интересы",
    "📬 Telegramda har kuni ertalab olish": "📬 Получать каждое утро в Telegram",
}  # fmt: skip

# Country names in Russian for the countries people meet most; the rest fall back to English.
RU_NAMES = {
    "UZB": "Узбекистан", "KAZ": "Казахстан", "KGZ": "Кыргызстан", "TJK": "Таджикистан", "TKM": "Туркменистан",
    "AFG": "Афганистан", "RUS": "Россия", "CHN": "Китай", "USA": "США", "TUR": "Турция", "IRN": "Иран",
    "IND": "Индия", "PAK": "Пакистан", "UKR": "Украина", "DEU": "Германия", "GBR": "Великобритания",
    "FRA": "Франция", "JPN": "Япония", "KOR": "Южная Корея", "PRK": "Северная Корея", "SAU": "Саудовская Аравия",
    "ARE": "ОАЭ", "ISR": "Израиль", "PSE": "Палестина", "AZE": "Азербайджан", "ARM": "Армения", "GEO": "Грузия",
    "BLR": "Беларусь", "POL": "Польша", "ITA": "Италия", "ESP": "Испания", "NLD": "Нидерланды", "BEL": "Бельгия",
    "CHE": "Швейцария", "AUT": "Австрия", "SWE": "Швеция", "NOR": "Норвегия", "FIN": "Финляндия", "DNK": "Дания",
    "EST": "Эстония", "LVA": "Латвия", "LTU": "Литва", "CZE": "Чехия", "SVK": "Словакия", "HUN": "Венгрия",
    "ROU": "Румыния", "BGR": "Болгария", "SRB": "Сербия", "GRC": "Греция", "PRT": "Португалия", "IRL": "Ирландия",
    "MDA": "Молдова", "MNG": "Монголия", "IRQ": "Ирак", "SYR": "Сирия", "LBN": "Ливан", "JOR": "Иордания",
    "EGY": "Египет", "QAT": "Катар", "KWT": "Кувейт", "OMN": "Оман", "YEM": "Йемен", "BHR": "Бахрейн",
    "BGD": "Бангладеш", "LKA": "Шри-Ланка", "NPL": "Непал", "MMR": "Мьянма", "THA": "Таиланд", "VNM": "Вьетнам",
    "MYS": "Малайзия", "SGP": "Сингапур", "IDN": "Индонезия", "PHL": "Филиппины", "TWN": "Тайвань",
    "AUS": "Австралия", "NZL": "Новая Зеландия", "CAN": "Канада", "MEX": "Мексика", "BRA": "Бразилия",
    "ARG": "Аргентина", "CHL": "Чили", "COL": "Колумбия", "VEN": "Венесуэла", "CUB": "Куба", "PER": "Перу",
    "ZAF": "ЮАР", "NGA": "Нигерия", "ETH": "Эфиопия", "KEN": "Кения", "SDN": "Судан", "SSD": "Южный Судан",
    "LBY": "Ливия", "DZA": "Алжир", "MAR": "Марокко", "TUN": "Тунис", "SOM": "Сомали", "COD": "ДР Конго",
    "MLI": "Мали", "NER": "Нигер", "TCD": "Чад", "CAF": "ЦАР", "CMR": "Камерун", "AGO": "Ангола",
}  # fmt: skip
