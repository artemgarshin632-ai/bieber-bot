import os
import sqlite3
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes,
)

load_dotenv()

TOKEN = os.getenv("BUSINESS_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_FILE = "business.db"

# ─── States ───────────────────────────────────────────────────────────────────
(
    MAIN,
    AC_AREA, AC_FLOOR, AC_PHONE, AC_NOTES,
    AC_CALC,
    SVC_PHONE, SVC_DATE,
    CAR_MODEL, CAR_PHONE, CAR_DATE,
    GATES_WIDTH, GATES_ADDR, GATES_PHONE, GATES_NOTES,
    GATES_CALC_W, GATES_CALC_WGT,
    REVIEW,
) = range(18)

STATUS_LABELS = {
    "new": "🆕 Новая",
    "in_progress": "🔄 В работе",
    "done": "✅ Выполнена",
    "cancelled": "❌ Отменена",
}

SERVICE_LABELS = {
    "ac_install": "❄️ Установка кондиционера",
    "ac_service": "🔧 ТО кондиционера",
    "car_ac": "🚗 Заправка авто кондиционера",
    "gates": "🚪 Откатные ворота",
}

# ─── Database ─────────────────────────────────────────────────────────────────

def db_init():
    con = sqlite3.connect(DB_FILE)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            name TEXT,
            service TEXT,
            status TEXT DEFAULT 'new',
            details TEXT,
            created_at TEXT,
            scheduled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            service TEXT,
            remind_at TEXT,
            sent INTEGER DEFAULT 0
        );
    """)
    con.commit()
    con.close()


def db_save_client(tid, name, phone):
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "INSERT OR REPLACE INTO clients (telegram_id, name, phone, created_at) VALUES (?,?,?,?)",
        (tid, name, phone, datetime.now().isoformat()),
    )
    con.commit()
    con.close()


def db_create_order(tid, name, service, details, scheduled_at=None):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO orders (telegram_id, name, service, details, created_at, scheduled_at) VALUES (?,?,?,?,?,?)",
        (tid, name, service, json.dumps(details, ensure_ascii=False),
         datetime.now().isoformat(), scheduled_at),
    )
    oid = cur.lastrowid
    con.commit()
    con.close()
    return oid


def db_get_orders(tid=None):
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    if tid:
        rows = con.execute(
            "SELECT * FROM orders WHERE telegram_id=? ORDER BY created_at DESC", (tid,)
        ).fetchall()
    else:
        rows = con.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def db_update_status(order_id, status):
    con = sqlite3.connect(DB_FILE)
    con.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    con.commit()
    con.close()


def db_add_reminder(tid, service, remind_at: datetime):
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "INSERT INTO reminders (telegram_id, service, remind_at) VALUES (?,?,?)",
        (tid, service, remind_at.isoformat()),
    )
    con.commit()
    con.close()


def db_due_reminders():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM reminders WHERE sent=0 AND remind_at <= ?",
        (datetime.now().isoformat(),),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def db_mark_sent(rid):
    con = sqlite3.connect(DB_FILE)
    con.execute("UPDATE reminders SET sent=1 WHERE id=?", (rid,))
    con.commit()
    con.close()


def db_all_client_ids():
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("SELECT telegram_id FROM clients").fetchall()
    con.close()
    return [r[0] for r in rows]


def db_stats():
    con = sqlite3.connect(DB_FILE)
    clients = con.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    new = con.execute("SELECT COUNT(*) FROM orders WHERE status='new'").fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM orders WHERE status='done'").fetchone()[0]
    by_svc = con.execute("SELECT service, COUNT(*) FROM orders GROUP BY service").fetchall()
    con.close()
    return clients, total, new, done, by_svc

# ─── Calculators ──────────────────────────────────────────────────────────────

def calc_ac(area: float) -> str:
    kw = area * 0.1 * 1.2
    if kw <= 2.1:
        model, hint = "07-серия (2.0 кВт)", "до 18 м²"
    elif kw <= 2.6:
        model, hint = "09-серия (2.5 кВт)", "18–25 м²"
    elif kw <= 3.5:
        model, hint = "12-серия (3.5 кВт)", "25–35 м²"
    elif kw <= 5.3:
        model, hint = "18-серия (5.0 кВт)", "35–50 м²"
    else:
        model, hint = "24-серия (7.0 кВт) или мульти-сплит", "от 50 м²"
    btu = kw * 3412
    return (
        f"📐 Площадь: *{area} м²*\n"
        f"⚡ Рекомендуемая мощность: *{kw:.1f} кВт* (~{btu:.0f} BTU)\n"
        f"✅ Рекомендуемая серия: *{model}*\n"
        f"ℹ️ Подходит для помещений {hint}\n\n"
        f"_Точный расчёт зависит от высоты потолков, остекления и ориентации окон._"
    )


def calc_gates(width: float, weight: float) -> str:
    if weight <= 400:
        motor, price = "привод до 400 кг (CAME BX-74)", "15 000 – 22 000 ₽"
    elif weight <= 800:
        motor, price = "привод до 800 кг (CAME BK-1200)", "22 000 – 35 000 ₽"
    elif weight <= 1800:
        motor, price = "привод до 1800 кг (CAME BX-78KA)", "35 000 – 55 000 ₽"
    else:
        motor, price = "промышленный привод", "от 55 000 ₽"
    space = width * 1.5
    return (
        f"📏 Ширина проёма: *{width} м*\n"
        f"⚖️ Вес полотна: *{weight} кг*\n\n"
        f"✅ Рекомендуемый {motor}\n"
        f"💰 Ориентировочная стоимость привода: *{price}*\n"
        f"📐 Необходимое место для отката: *{space:.1f} м*\n\n"
        f"_Для точного расчёта мастер приедет бесплатно!_"
    )

# ─── FAQ texts ────────────────────────────────────────────────────────────────

FAQ_AC = (
    "❓ *FAQ — Кондиционеры*\n\n"
    "*Как часто нужно обслуживать кондиционер?*\n"
    "Раз в год — лучше весной перед сезоном. Чистка фильтров, проверка фреона, дезинфекция.\n\n"
    "*Сколько занимает установка?*\n"
    "Стандартная установка сплит-системы — 3–5 часов.\n\n"
    "*Есть ли гарантия?*\n"
    "Гарантия на работы — 1 год, на оборудование — гарантия производителя (2–3 года).\n\n"
    "*Нужно сверлить стены?*\n"
    "Да, для трассы делается отверстие 60–80 мм. Аккуратно заделываем.\n\n"
    "*Привозите оборудование?*\n"
    "Да, продаём и привозим кондиционеры, помогаем с выбором."
)

FAQ_CAR = (
    "❓ *FAQ — Заправка авто кондиционера*\n\n"
    "*Как понять, что пора заправлять?*\n"
    "— Кондиционер плохо охлаждает\n"
    "— Компрессор включается с задержкой\n"
    "— Прошло 2+ года с последней заправки\n\n"
    "*Какой фреон используется?*\n"
    "R-134a (авто до ~2015) или R-1234yf (современные). Определяем по марке авто.\n\n"
    "*Сколько времени занимает?*\n"
    "30–60 минут. Делаем диагностику, заправляем, добавляем масло и детектор утечки.\n\n"
    "*Нужна запись?*\n"
    "Желательно, чтобы не ждать в очереди.\n\n"
    "*Гарантия?*\n"
    "При отсутствии механических повреждений — гарантия на заправку 1 сезон."
)

FAQ_GATES = (
    "❓ *FAQ — Откатные ворота*\n\n"
    "*Чем откатные лучше распашных?*\n"
    "Не нужно место перед воротами, работают при снеге, надёжнее и долговечнее.\n\n"
    "*Нужен фундамент?*\n"
    "Нужна закладная (швеллер) для опорного столба — выполняем под ключ.\n\n"
    "*Сколько служат автоматические ворота?*\n"
    "Привод рассчитан на 50 000–100 000 циклов открывания/закрывания.\n\n"
    "*Есть ли ручное управление при отключении света?*\n"
    "Да, все приводы имеют механическую разблокировку.\n\n"
    "*Можно добавить видеодомофон?*\n"
    "Да, интегрируем домофон, камеру и управление с телефона."
)

# ─── Keyboards ────────────────────────────────────────────────────────────────

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❄️ Кондиционеры", callback_data="menu_ac")],
        [InlineKeyboardButton("🚗 Заправка авто кондиционера", callback_data="menu_car")],
        [InlineKeyboardButton("🚪 Откатные ворота", callback_data="menu_gates")],
        [InlineKeyboardButton("📋 Мои заявки", callback_data="my_orders")],
        [InlineKeyboardButton("⭐ Оставить отзыв", callback_data="review")],
    ])


def kb_ac():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Оставить заявку", callback_data="ac_request")],
        [InlineKeyboardButton("🧮 Калькулятор мощности", callback_data="ac_calc")],
        [InlineKeyboardButton("🔧 Техобслуживание", callback_data="ac_service")],
        [InlineKeyboardButton("❓ FAQ", callback_data="ac_faq")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])


def kb_car():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Записаться на заправку", callback_data="car_book")],
        [InlineKeyboardButton("❓ FAQ", callback_data="car_faq")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])


def kb_gates():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Оставить заявку", callback_data="gates_request")],
        [InlineKeyboardButton("🧮 Калькулятор ворот", callback_data="gates_calc")],
        [InlineKeyboardButton("❓ FAQ", callback_data="gates_faq")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])


def kb_cancel():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Главное меню", callback_data="back_main")]])


def kb_after_calc(request_cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Оставить заявку", callback_data=request_cb)],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])

# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_admin(update: Update) -> bool:
    return ADMIN_ID and update.effective_user.id == ADMIN_ID


async def notify_admin(context, order_id, user, service, details):
    if not ADMIN_ID:
        return
    label = SERVICE_LABELS.get(service, service)
    phone = details.get("phone", "—")
    field_names = {
        "area": "Площадь", "floor": "Этаж", "notes": "Пожелания",
        "car_model": "Авто", "date": "Время", "address": "Адрес", "width": "Ширина",
    }
    extra = "\n".join(
        f"   {field_names.get(k, k)}: {v}"
        for k, v in details.items() if k != "phone"
    )
    text = (
        f"🔔 *Новая заявка #{order_id}*\n\n"
        f"Услуга: {label}\n"
        f"👤 {user.full_name} (@{user.username or '—'})\n"
        f"📞 {phone}\n"
        f"{extra}\n\n"
        f"Изменить статус: /setstatus {order_id} in\\_progress"
    )
    try:
        await context.bot.send_message(ADMIN_ID, text, parse_mode="Markdown")
    except Exception:
        pass


async def go_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "👋 *Добро пожаловать!*\n\n"
        "Мы занимаемся:\n"
        "❄️ Продажей и установкой кондиционеров\n"
        "🚗 Заправкой автомобильных кондиционеров\n"
        "🚪 Установкой откатных ворот\n\n"
        "Выберите, что вас интересует:"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb_main(), parse_mode="Markdown")
    return MAIN

# ─── Conversation entry ───────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await go_main(update, context)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # ── Navigation ──
    if data in ("back_main", "cancel"):
        return await go_main(update, context)

    if data == "menu_ac":
        await q.edit_message_text(
            "❄️ *Кондиционеры*\n\nПродажа, установка и обслуживание сплит-систем.",
            reply_markup=kb_ac(), parse_mode="Markdown",
        )
        return MAIN

    if data == "menu_car":
        await q.edit_message_text(
            "🚗 *Заправка автокондиционера*\n\nДиагностика и заправка фреоном.",
            reply_markup=kb_car(), parse_mode="Markdown",
        )
        return MAIN

    if data == "menu_gates":
        await q.edit_message_text(
            "🚪 *Откатные ворота*\n\nПродажа, изготовление и установка под ключ.",
            reply_markup=kb_gates(), parse_mode="Markdown",
        )
        return MAIN

    # ── My orders ──
    if data == "my_orders":
        uid = q.from_user.id
        orders = db_get_orders(uid)
        if not orders:
            await q.edit_message_text(
                "📋 У вас пока нет заявок.\n\nВыберите услугу в меню!",
                reply_markup=kb_back(),
            )
            return MAIN
        lines = ["📋 *Ваши последние заявки:*\n"]
        for o in orders[:5]:
            svc = SERVICE_LABELS.get(o["service"], o["service"])
            st = STATUS_LABELS.get(o["status"], o["status"])
            lines.append(f"🔹 *#{o['id']}* — {svc}\n   Статус: {st}\n   Дата: {o['created_at'][:10]}\n")
        await q.edit_message_text("\n".join(lines), reply_markup=kb_back(), parse_mode="Markdown")
        return MAIN

    # ── Review ──
    if data == "review":
        await q.edit_message_text(
            "⭐ *Оставьте отзыв*\n\nНапишите несколько слов о нашей работе:",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return REVIEW

    # ── FAQ ──
    if data == "ac_faq":
        await q.edit_message_text(FAQ_AC, reply_markup=kb_back(), parse_mode="Markdown")
        return MAIN
    if data == "car_faq":
        await q.edit_message_text(FAQ_CAR, reply_markup=kb_back(), parse_mode="Markdown")
        return MAIN
    if data == "gates_faq":
        await q.edit_message_text(FAQ_GATES, reply_markup=kb_back(), parse_mode="Markdown")
        return MAIN

    # ── Calculators ──
    if data == "ac_calc":
        await q.edit_message_text(
            "🧮 *Калькулятор мощности кондиционера*\n\nВведите площадь комнаты в м²:",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return AC_CALC

    if data == "gates_calc":
        await q.edit_message_text(
            "🧮 *Калькулятор ворот*\n\nШаг 1 из 2 — введите ширину проёма в метрах (например: 4.5):",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return GATES_CALC_W

    # ── AC request ──
    if data == "ac_request":
        context.user_data["service"] = "ac_install"
        await q.edit_message_text(
            "❄️ *Заявка на установку кондиционера*\n\nШаг 1 из 4 — укажите площадь помещения в м²:",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return AC_AREA

    # ── AC service ──
    if data == "ac_service":
        context.user_data["service"] = "ac_service"
        await q.edit_message_text(
            "🔧 *Техническое обслуживание*\n\nЧистка, дезинфекция, проверка фреона.\n\nВведите ваш номер телефона:",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return SVC_PHONE

    # ── Car booking ──
    if data == "car_book":
        context.user_data["service"] = "car_ac"
        await q.edit_message_text(
            "🚗 *Запись на заправку*\n\nШаг 1 из 3 — марка и модель авто (например: Toyota Camry 2020):",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return CAR_MODEL

    # ── Gates request ──
    if data == "gates_request":
        context.user_data["service"] = "gates"
        await q.edit_message_text(
            "🚪 *Заявка на ворота*\n\nШаг 1 из 4 — укажите ширину проёма в метрах (например: 4.5):",
            reply_markup=kb_cancel(), parse_mode="Markdown",
        )
        return GATES_WIDTH

    return MAIN

# ─── AC install flow ──────────────────────────────────────────────────────────

async def ac_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        area = float(update.message.text.strip().replace(",", "."))
        assert 1 <= area <= 1000
    except Exception:
        await update.message.reply_text("Введите корректную площадь числом (например: 25):", reply_markup=kb_cancel())
        return AC_AREA
    context.user_data["area"] = area
    await update.message.reply_text(
        f"✅ Площадь: {area} м²\n\nШаг 2 из 4 — на каком этаже находится помещение?",
        reply_markup=kb_cancel(),
    )
    return AC_FLOOR


async def ac_floor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["floor"] = update.message.text.strip()
    await update.message.reply_text("Шаг 3 из 4 — введите ваш номер телефона:", reply_markup=kb_cancel())
    return AC_PHONE


async def ac_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 4 из 4 — есть ли особые пожелания? (или напишите «нет»):",
        reply_markup=kb_cancel(),
    )
    return AC_NOTES


async def ac_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ud = context.user_data
    ud["notes"] = update.message.text.strip()
    details = {k: ud[k] for k in ("area", "floor", "phone", "notes")}
    oid = db_create_order(user.id, user.full_name, "ac_install", details)
    db_save_client(user.id, user.full_name, ud["phone"])
    db_add_reminder(user.id, "ac_install", datetime.now() + timedelta(days=365))
    await notify_admin(context, oid, user, "ac_install", details)
    await update.message.reply_text(
        f"✅ *Заявка #{oid} принята!*\n\n"
        f"❄️ Установка кондиционера\n"
        f"📐 Площадь: {details['area']} м², этаж: {details['floor']}\n"
        f"📞 Телефон: {details['phone']}\n\n"
        f"Мы свяжемся с вами в ближайшее время!\n"
        f"_Через год напомним о плановом ТО_ 🔧",
        parse_mode="Markdown", reply_markup=kb_main(),
    )
    context.user_data.clear()
    return MAIN

# ─── AC calculator ────────────────────────────────────────────────────────────

async def ac_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        area = float(update.message.text.strip().replace(",", "."))
        assert area > 0
    except Exception:
        await update.message.reply_text("Введите площадь числом (например: 25):", reply_markup=kb_cancel())
        return AC_CALC
    await update.message.reply_text(
        calc_ac(area), parse_mode="Markdown",
        reply_markup=kb_after_calc("ac_request"),
    )
    return MAIN

# ─── AC service flow ──────────────────────────────────────────────────────────

async def svc_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text(
        "Когда вам удобно? Укажите желаемую дату (например: 20 мая):",
        reply_markup=kb_cancel(),
    )
    return SVC_DATE


async def svc_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ud = context.user_data
    ud["date"] = update.message.text.strip()
    details = {"phone": ud["phone"], "date": ud["date"]}
    oid = db_create_order(user.id, user.full_name, "ac_service", details, scheduled_at=ud["date"])
    db_save_client(user.id, user.full_name, ud["phone"])
    await notify_admin(context, oid, user, "ac_service", details)
    await update.message.reply_text(
        f"✅ *Заявка #{oid} на ТО принята!*\n\n"
        f"📞 Телефон: {details['phone']}\n"
        f"📅 Желаемая дата: {details['date']}\n\n"
        f"Мы подтвердим время в ближайшее время!",
        parse_mode="Markdown", reply_markup=kb_main(),
    )
    context.user_data.clear()
    return MAIN

# ─── Car AC flow ──────────────────────────────────────────────────────────────

async def car_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_model"] = update.message.text.strip()
    await update.message.reply_text(
        f"🚗 Авто: {context.user_data['car_model']}\n\nШаг 2 из 3 — введите ваш номер телефона:",
        reply_markup=kb_cancel(),
    )
    return CAR_PHONE


async def car_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 3 из 3 — когда вам удобно приехать? (например: завтра в 11:00):",
        reply_markup=kb_cancel(),
    )
    return CAR_DATE


async def car_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ud = context.user_data
    ud["date"] = update.message.text.strip()
    details = {"car_model": ud["car_model"], "phone": ud["phone"], "date": ud["date"]}
    oid = db_create_order(user.id, user.full_name, "car_ac", details, scheduled_at=ud["date"])
    db_save_client(user.id, user.full_name, ud["phone"])
    db_add_reminder(user.id, "car_ac", datetime.now() + timedelta(days=365))
    await notify_admin(context, oid, user, "car_ac", details)
    await update.message.reply_text(
        f"✅ *Запись #{oid} принята!*\n\n"
        f"🚗 Авто: {details['car_model']}\n"
        f"📞 Телефон: {details['phone']}\n"
        f"📅 Время: {details['date']}\n\n"
        f"Мы подтвердим запись!\n"
        f"_Через год напомним о следующей заправке_ 🔔",
        parse_mode="Markdown", reply_markup=kb_main(),
    )
    context.user_data.clear()
    return MAIN

# ─── Gates flow ───────────────────────────────────────────────────────────────

async def gates_width(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        w = float(update.message.text.strip().replace(",", "."))
        assert 1 <= w <= 20
    except Exception:
        await update.message.reply_text("Введите ширину числом в метрах (например: 4.5):", reply_markup=kb_cancel())
        return GATES_WIDTH
    context.user_data["width"] = w
    await update.message.reply_text(
        f"✅ Ширина: {w} м\n\nШаг 2 из 4 — введите адрес объекта:",
        reply_markup=kb_cancel(),
    )
    return GATES_ADDR


async def gates_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text.strip()
    await update.message.reply_text("Шаг 3 из 4 — введите ваш номер телефона:", reply_markup=kb_cancel())
    return GATES_PHONE


async def gates_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 4 из 4 — особые пожелания по воротам? (материал, цвет, тип заполнения — или «нет»):",
        reply_markup=kb_cancel(),
    )
    return GATES_NOTES


async def gates_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ud = context.user_data
    ud["notes"] = update.message.text.strip()
    details = {k: ud[k] for k in ("width", "address", "phone", "notes")}
    oid = db_create_order(user.id, user.full_name, "gates", details)
    db_save_client(user.id, user.full_name, ud["phone"])
    await notify_admin(context, oid, user, "gates", details)
    await update.message.reply_text(
        f"✅ *Заявка #{oid} принята!*\n\n"
        f"🚪 Откатные ворота\n"
        f"📏 Ширина: {details['width']} м\n"
        f"📍 Адрес: {details['address']}\n"
        f"📞 Телефон: {details['phone']}\n\n"
        f"Мастер свяжется с вами для замера — *бесплатно*!",
        parse_mode="Markdown", reply_markup=kb_main(),
    )
    context.user_data.clear()
    return MAIN

# ─── Gates calculator ─────────────────────────────────────────────────────────

async def gates_calc_w(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        w = float(update.message.text.strip().replace(",", "."))
        assert w > 0
    except Exception:
        await update.message.reply_text("Введите ширину числом в метрах (например: 4.5):", reply_markup=kb_cancel())
        return GATES_CALC_W
    context.user_data["g_width"] = w
    await update.message.reply_text(
        f"✅ Ширина: {w} м\n\n"
        "Шаг 2 из 2 — введите примерный вес полотна в кг\n"
        "_(Профнастил ~30 кг/м², сетка ~10 кг/м², ковка ~50 кг/м²)_:",
        reply_markup=kb_cancel(), parse_mode="Markdown",
    )
    return GATES_CALC_WGT


async def gates_calc_wgt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wgt = float(update.message.text.strip().replace(",", "."))
        assert wgt > 0
    except Exception:
        await update.message.reply_text("Введите вес числом в кг (например: 250):", reply_markup=kb_cancel())
        return GATES_CALC_WGT
    w = context.user_data.get("g_width", 4)
    await update.message.reply_text(
        calc_gates(w, wgt), parse_mode="Markdown",
        reply_markup=kb_after_calc("gates_request"),
    )
    return MAIN

# ─── Review ───────────────────────────────────────────────────────────────────

async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"⭐ *Новый отзыв*\n\nОт: {user.full_name} (@{user.username or '—'})\n\n«{text}»",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await update.message.reply_text(
        "🙏 *Спасибо за ваш отзыв!* Мы очень ценим обратную связь.",
        parse_mode="Markdown", reply_markup=kb_main(),
    )
    return MAIN

# ─── Admin commands ───────────────────────────────────────────────────────────

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    orders = db_get_orders()
    if not orders:
        await update.message.reply_text("Заявок пока нет.")
        return
    lines = ["📋 *Последние заявки:*\n"]
    for o in orders[:10]:
        det = json.loads(o["details"])
        lines.append(
            f"🔹 *#{o['id']}* — {SERVICE_LABELS.get(o['service'], o['service'])}\n"
            f"   👤 {o['name']}  📞 {det.get('phone', '—')}\n"
            f"   Статус: {STATUS_LABELS.get(o['status'], o['status'])}  📅 {o['created_at'][:10]}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_setstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Использование: /setstatus <id> <статус>\n"
            "Статусы: new | in_progress | done | cancelled"
        )
        return
    try:
        oid = int(args[0])
        status = args[1]
        if status not in STATUS_LABELS:
            await update.message.reply_text(f"Допустимые статусы: {', '.join(STATUS_LABELS)}")
            return
        db_update_status(oid, status)
        orders = db_get_orders()
        order = next((o for o in orders if o["id"] == oid), None)
        if order:
            svc = SERVICE_LABELS.get(order["service"], order["service"])
            try:
                await context.bot.send_message(
                    order["telegram_id"],
                    f"📬 *Обновление по заявке #{oid}*\n\n"
                    f"Услуга: {svc}\n"
                    f"Новый статус: {STATUS_LABELS[status]}\n\n"
                    f"Спасибо, что выбрали нас! 🙏",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        await update.message.reply_text(f"✅ Статус #{oid} → {STATUS_LABELS[status]}")
    except Exception:
        await update.message.reply_text("Ошибка. Пример: /setstatus 5 done")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Использование: /broadcast <текст>")
        return
    text = " ".join(context.args)
    ids = db_all_client_ids()
    sent = 0
    for cid in ids:
        try:
            await context.bot.send_message(cid, text)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Рассылка отправлена {sent} клиентам.")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    clients, total, new, done, by_svc = db_stats()
    svc_lines = "\n".join(
        f"   {SERVICE_LABELS.get(s, s)}: {c}" for s, c in by_svc
    )
    await update.message.reply_text(
        f"📊 *Статистика*\n\n"
        f"👥 Клиентов: {clients}\n"
        f"📋 Всего заявок: {total}\n"
        f"🆕 Новых: {new}\n"
        f"✅ Выполнено: {done}\n\n"
        f"По услугам:\n{svc_lines}",
        parse_mode="Markdown",
    )

# ─── Reminder job ─────────────────────────────────────────────────────────────

async def job_reminders(context: ContextTypes.DEFAULT_TYPE):
    msgs = {
        "ac_install": (
            "🔧 *Напоминание о ТО кондиционера*\n\n"
            "Прошёл год с установки вашего кондиционера! Рекомендуем плановое обслуживание.\n"
            "Нажмите /start чтобы записаться."
        ),
        "car_ac": (
            "🚗 *Напоминание о заправке*\n\n"
            "Прошёл год с заправки автокондиционера! Рекомендуем проверить давление фреона.\n"
            "Нажмите /start чтобы записаться."
        ),
    }
    for r in db_due_reminders():
        msg = msgs.get(r["service"])
        if not msg:
            continue
        try:
            await context.bot.send_message(r["telegram_id"], msg, parse_mode="Markdown")
            db_mark_sent(r["id"])
        except Exception:
            pass

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db_init()

    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(on_callback),
        ],
        states={
            MAIN: [CallbackQueryHandler(on_callback)],
            AC_AREA:       [MessageHandler(filters.TEXT & ~filters.COMMAND, ac_area),   CallbackQueryHandler(on_callback)],
            AC_FLOOR:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ac_floor),  CallbackQueryHandler(on_callback)],
            AC_PHONE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ac_phone),  CallbackQueryHandler(on_callback)],
            AC_NOTES:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ac_notes),  CallbackQueryHandler(on_callback)],
            AC_CALC:       [MessageHandler(filters.TEXT & ~filters.COMMAND, ac_calc),   CallbackQueryHandler(on_callback)],
            SVC_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, svc_phone), CallbackQueryHandler(on_callback)],
            SVC_DATE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, svc_date),  CallbackQueryHandler(on_callback)],
            CAR_MODEL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, car_model), CallbackQueryHandler(on_callback)],
            CAR_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, car_phone), CallbackQueryHandler(on_callback)],
            CAR_DATE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, car_date),  CallbackQueryHandler(on_callback)],
            GATES_WIDTH:   [MessageHandler(filters.TEXT & ~filters.COMMAND, gates_width),   CallbackQueryHandler(on_callback)],
            GATES_ADDR:    [MessageHandler(filters.TEXT & ~filters.COMMAND, gates_addr),    CallbackQueryHandler(on_callback)],
            GATES_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, gates_phone),   CallbackQueryHandler(on_callback)],
            GATES_NOTES:   [MessageHandler(filters.TEXT & ~filters.COMMAND, gates_notes),   CallbackQueryHandler(on_callback)],
            GATES_CALC_W:  [MessageHandler(filters.TEXT & ~filters.COMMAND, gates_calc_w),  CallbackQueryHandler(on_callback)],
            GATES_CALC_WGT:[MessageHandler(filters.TEXT & ~filters.COMMAND, gates_calc_wgt),CallbackQueryHandler(on_callback)],
            REVIEW:        [MessageHandler(filters.TEXT & ~filters.COMMAND, review),    CallbackQueryHandler(on_callback)],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(on_callback, pattern="^(cancel|back_main)$"),
        ],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("setstatus", cmd_setstatus))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.job_queue.run_repeating(job_reminders, interval=3600, first=60)

    print("Бизнес-бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
