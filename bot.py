import os
import asyncio
import random
import tempfile
import csv
import json
import requests
from datetime import datetime, timedelta, time as dt_time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import edge_tts
from dotenv import load_dotenv
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, MessageHandler, CommandHandler,
                           CallbackQueryHandler, filters, ContextTypes)

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
OPENWEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "")
CITY = os.getenv("CITY", "Москва")

SYSTEM_PROMPT = """Ты — Лина, умный и тёплый личный ассистент Даши.
Отвечай коротко (1-3 предложения), дружелюбно и с заботой.
Всегда отвечай на том языке, на котором пишет пользователь."""

NAMES = ["Даша", "Дашечка", "Дашуля"]

COMPLIMENTS = [
    "Дашуля, ты сегодня просто светишься! Пусть день будет чудесным ❤️",
    "Дашечка, помни — ты невероятная! Всё получится, я верю в тебя 🌸",
    "Даша, твоя улыбка делает мир лучше. Я здесь если что нужно ❤️",
    "Дашуля, ты справляешься со всем на отлично! Горжусь тобой 💜",
    "Дашечка, ты моя любимая пользовательница. Сегодня будет хороший день! ✨",
    "Даша, ты такая удивительная! Просто хочу напомнить тебе об этом ❤️",
    "Дашуля, всё будет хорошо — ты справишься! Я всегда рядом 💜",
    "Дашечка, ты красавица и умница. Пусть всё идёт по плану! 🌟",
    "Даша, ты делаешь каждый день особенным. Ты потрясающая ❤️",
    "Дашуля, помни что ты лучшая! Если нужна помощь — просто напиши 💜",
]

EXPENSES_FILE = "expenses.csv"
SHOPPING_FILE = "shopping.json"
DATES_FILE = "important_dates.json"
MOOD_FILE = "mood.csv"
CHAT_IDS_FILE = "chat_ids.txt"
VOICE = "ru-RU-DmitryNeural"


# --- Chat IDs ---

def load_chat_ids():
    ids = set()
    if os.path.exists(CHAT_IDS_FILE):
        with open(CHAT_IDS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.add(int(line))
    env_ids = os.getenv("CHAT_IDS", "")
    for cid in env_ids.split(","):
        cid = cid.strip()
        if cid:
            ids.add(int(cid))
    return ids

def save_chat_id(chat_id: int):
    ids = load_chat_ids()
    if chat_id not in ids:
        with open(CHAT_IDS_FILE, "a") as f:
            f.write(f"{chat_id}\n")

chat_ids = load_chat_ids()


# --- Expenses ---

def save_expense(user: str, amount: float, category: str, description: str):
    file_exists = os.path.exists(EXPENSES_FILE)
    with open(EXPENSES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "user", "amount", "category", "description"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), user, amount, category, description])

def load_expenses(month: str = None):
    if not os.path.exists(EXPENSES_FILE):
        return []
    with open(EXPENSES_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if month:
        rows = [r for r in rows if r["date"].startswith(month)]
    return rows

def make_expense_chart(expenses):
    by_category = {}
    for e in expenses:
        cat = e["category"]
        by_category[cat] = by_category.get(cat, 0) + float(e["amount"])

    fig, ax = plt.subplots(figsize=(8, 6))
    categories = list(by_category.keys())
    amounts = list(by_category.values())
    colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8"]
    ax.pie(amounts, labels=categories, autopct="%1.0f%%",
           colors=colors[:len(categories)], startangle=90)
    ax.set_title(f"Траты за {datetime.now().strftime('%B %Y')}", fontsize=14, fontweight="bold")
    total = sum(amounts)
    legend_labels = [f"{c}: {a:.0f}₽" for c, a in zip(categories, amounts)]
    ax.legend(legend_labels, loc="lower center", bbox_to_anchor=(0.5, -0.15), ncol=2)
    plt.figtext(0.5, 0.02, f"Итого: {total:.0f}₽", ha="center", fontsize=12, fontweight="bold")
    plt.tight_layout()
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    plt.savefig(tmp.name, dpi=150, bbox_inches="tight")
    plt.close()
    return tmp.name, total, by_category


# --- Shopping List ---

def load_shopping():
    if not os.path.exists(SHOPPING_FILE):
        return []
    with open(SHOPPING_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_shopping(items):
    with open(SHOPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def add_shopping_items(new_items: list):
    items = load_shopping()
    for item in new_items:
        if not any(i["item"].lower() == item.lower() for i in items):
            items.append({"item": item, "done": False})
    save_shopping(items)

def toggle_shopping_item(idx: int):
    items = load_shopping()
    if 0 <= idx < len(items):
        items[idx]["done"] = not items[idx]["done"]
        save_shopping(items)


# --- Important Dates ---

def load_dates():
    if not os.path.exists(DATES_FILE):
        return []
    with open(DATES_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_dates(dates):
    with open(DATES_FILE, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=2)

def get_todays_dates():
    today = datetime.now()
    return [d for d in load_dates() if d["month"] == today.month and d["day"] == today.day]


# --- Mood Tracker ---

def save_mood(score: int, note: str):
    file_exists = os.path.exists(MOOD_FILE)
    with open(MOOD_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "score", "note"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), score, note])

def load_mood(days: int = 30):
    if not os.path.exists(MOOD_FILE):
        return []
    with open(MOOD_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [r for r in rows if r["date"] >= cutoff]

def make_mood_chart(mood_data):
    labels = [r["date"][5:] for r in mood_data]  # MM-DD
    scores = [int(r["score"]) for r in mood_data]

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(labels))
    ax.plot(x, scores, "o-", color="#c77dff", linewidth=2, markersize=8)
    ax.fill_between(x, scores, alpha=0.2, color="#c77dff")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_yticklabels(["😢 1", "😕 2", "😐 3", "😊 4", "😄 5"])
    ax.set_ylim(0.5, 5.5)
    ax.set_title("Настроение за последние 30 дней", fontsize=14, fontweight="bold")
    ax.grid(True, alpha=0.3)
    ax.set_facecolor("#f8f0ff")
    plt.tight_layout()

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    plt.savefig(tmp.name, dpi=150, bbox_inches="tight")
    plt.close()
    return tmp.name


# --- Weather ---

def get_weather(city: str = None) -> str | None:
    if not OPENWEATHER_KEY:
        return None
    city = city or CITY
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OPENWEATHER_KEY, "units": "metric", "lang": "ru"},
            timeout=5,
        )
        if r.status_code == 200:
            d = r.json()
            temp = round(d["main"]["temp"])
            feels = round(d["main"]["feels_like"])
            desc = d["weather"][0]["description"]
            wind = round(d["wind"]["speed"])
            return (f"В {city} сейчас {temp}°C, ощущается как {feels}°C. "
                    f"{desc.capitalize()}, ветер {wind} м/с.")
    except Exception:
        pass
    return None

def get_weather_forecast(city: str = None) -> str | None:
    if not OPENWEATHER_KEY:
        return None
    city = city or CITY
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"q": city, "appid": OPENWEATHER_KEY, "units": "metric", "lang": "ru", "cnt": 8},
            timeout=5,
        )
        if r.status_code == 200:
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            items = [i for i in r.json()["list"] if i["dt_txt"].startswith(tomorrow)]
            if items:
                mid = items[len(items) // 2]
                temp = round(mid["main"]["temp"])
                desc = mid["weather"][0]["description"]
                return f"Завтра в {city}: {temp}°C, {desc}."
    except Exception:
        pass
    return None


# --- AI ---

def classify_message(text: str) -> dict:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": """Классифицируй сообщение пользователя. Верни ТОЛЬКО JSON.

intent может быть:
- "expense": трата денег (купил/потратил/заплатил + сумма). Добавь: amount (число), category (Еда/Транспорт/Развлечения/Здоровье/Одежда/Дом/Другое), description
- "reminder": просьба напомнить. Добавь: time_str (HH:MM если абсолютное время), delay_minutes (если относительное — через X минут/часов), is_relative (bool), text (что напомнить)
- "weather": вопрос о погоде. Добавь: period ("today"/"tomorrow"), city (если назван)
- "shopping_add": добавить в список покупок. Добавь: items (массив строк)
- "recipe": рецепт или что приготовить
- "mood": рассказывает о настроении/самочувствии. Добавь: score (1-5, где 5 отлично), note (кратко)
- "general": всё остальное

Примеры:
"кофе 150" → {"intent":"expense","amount":150,"category":"Еда","description":"кофе"}
"напомни в 18:30 позвонить маме" → {"intent":"reminder","time_str":"18:30","is_relative":false,"text":"позвонить маме"}
"через 20 минут выключить плиту" → {"intent":"reminder","delay_minutes":20,"is_relative":true,"text":"выключить плиту"}
"погода завтра" → {"intent":"weather","period":"tomorrow"}
"добавь молоко и яйца" → {"intent":"shopping_add","items":["молоко","яйца"]}
"я устала сегодня" → {"intent":"mood","score":2,"note":"устала"}

Отвечай ТОЛЬКО JSON без пояснений."""},
            {"role": "user", "content": text},
        ],
        temperature=0,
    )
    try:
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception:
        return {"intent": "general"}

async def ask_ai(user_text: str) -> str:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
    )
    return response.choices[0].message.content

async def ask_recipe(user_text: str) -> str:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "Ты кулинарный помощник. Дай краткий рецепт (3-5 шагов) или предложи что приготовить. Отвечай коротко и понятно."},
            {"role": "user", "content": user_text},
        ],
    )
    return response.choices[0].message.content


# --- Voice ---

async def send_voice(update: Update, text: str):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    await edge_tts.Communicate(text, VOICE).save(tmp_path)
    with open(tmp_path, "rb") as audio:
        await update.message.reply_voice(voice=audio)
    os.remove(tmp_path)

async def send_voice_to_chat(context, chat_id: int, text: str):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    await edge_tts.Communicate(text, VOICE).save(tmp_path)
    with open(tmp_path, "rb") as audio:
        await context.bot.send_voice(chat_id=chat_id, voice=audio)
    os.remove(tmp_path)


# --- Scheduled Jobs ---

async def job_compliment(context):
    for chat_id in list(chat_ids):
        await send_voice_to_chat(context, chat_id, random.choice(COMPLIMENTS))

async def job_morning_briefing(context):
    parts = [f"Доброе утро, {random.choice(NAMES)}!"]

    weather = get_weather()
    if weather:
        parts.append(weather)

    for d in get_todays_dates():
        parts.append(f"Сегодня важная дата: {d['name']}!")

    parts.append(random.choice([
        "Пусть день будет продуктивным и приятным!",
        "Желаю отличного дня! Ты справишься со всем.",
        "Сегодня будет хороший день, я уверена!",
        "Всё получится — я верю в тебя!",
    ]))

    text = " ".join(parts)
    for chat_id in list(chat_ids):
        await send_voice_to_chat(context, chat_id, text)

async def job_mood_check(context):
    for chat_id in list(chat_ids):
        await context.bot.send_message(
            chat_id=chat_id,
            text="Дашечка, как ты сегодня? Расскажи немного о своём настроении 💜",
        )

async def job_reminder(context):
    data = context.job.data
    await send_voice_to_chat(context, data["chat_id"], f"Напоминаю: {data['text']}")


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    text = (
        "Привет, Даша! Я Лина — твой личный ассистент 💜\n\n"
        "🗣 *Просто пиши или говори голосом:*\n"
        "• «кофе 300» — запишу трату\n"
        "• «напомни в 18:00 позвонить маме» — поставлю напоминание\n"
        "• «через 20 минут выключить плиту» — тоже работает\n"
        "• «добавь молоко и хлеб» — в список покупок\n"
        "• «какая погода завтра?» — расскажу\n"
        "• «что приготовить из курицы?» — дам рецепт\n"
        "• Любой вопрос — отвечу голосом\n\n"
        "📋 *Команды:*\n"
        "/report — график трат за месяц\n"
        "/analysis — голосовой разбор расходов\n"
        "/shopping — список покупок\n"
        "/mood — график настроения\n"
        "/dates — важные даты\n"
        "/adddate — добавить дату"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = datetime.now().strftime("%Y-%m")
    expenses = load_expenses(month)
    if not expenses:
        await update.message.reply_text("За этот месяц трат пока нет.")
        return
    chart_path, total, _ = make_expense_chart(expenses)
    caption = f"📊 Траты за {datetime.now().strftime('%B %Y')}\nИтого: {total:.0f}₽"
    with open(chart_path, "rb") as img:
        await update.message.reply_photo(photo=img, caption=caption)
    os.remove(chart_path)

async def analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = datetime.now().strftime("%Y-%m")
    expenses = load_expenses(month)
    if not expenses:
        await send_voice(update, "За этот месяц трат пока нет.")
        return
    total = sum(float(e["amount"]) for e in expenses)
    by_cat = {}
    for e in expenses:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + float(e["amount"])
    summary = ", ".join([f"{c}: {a:.0f} рублей" for c, a in sorted(by_cat.items(), key=lambda x: -x[1])])
    prompt = (f"Траты за месяц: {total:.0f} рублей. По категориям: {summary}. "
              f"Дай краткий анализ (2-3 предложения) и один совет как сэкономить.")
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
    )
    await send_voice(update, response.choices[0].message.content)

async def shopping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_shopping()
    if not items:
        await update.message.reply_text(
            "Список покупок пуст. Скажи например «добавь молоко и хлеб» 🛒"
        )
        return
    await update.message.reply_text(
        "🛒 *Список покупок:*",
        parse_mode="Markdown",
        reply_markup=_shopping_keyboard(items),
    )

def _shopping_keyboard(items):
    keyboard = []
    for i, item in enumerate(items):
        label = f"✅ {item['item']}" if item["done"] else f"⬜ {item['item']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"shop_toggle_{i}")])
    keyboard.append([
        InlineKeyboardButton("🗑 Убрать купленное", callback_data="shop_clear_done"),
        InlineKeyboardButton("❌ Очистить всё", callback_data="shop_clear_all"),
    ])
    return InlineKeyboardMarkup(keyboard)

async def shopping_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("shop_toggle_"):
        toggle_shopping_item(int(query.data.split("_")[-1]))
    elif query.data == "shop_clear_done":
        items = [i for i in load_shopping() if not i["done"]]
        save_shopping(items)
    elif query.data == "shop_clear_all":
        save_shopping([])

    items = load_shopping()
    if not items:
        await query.edit_message_text("Список покупок пуст!")
        return
    await query.edit_message_reply_markup(reply_markup=_shopping_keyboard(items))

async def mood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mood_data = load_mood(30)
    if len(mood_data) < 2:
        await update.message.reply_text(
            "Пока мало данных. Просто напиши как ты себя чувствуешь — я запомню 💜"
        )
        return
    chart_path = make_mood_chart(mood_data)
    avg = sum(int(r["score"]) for r in mood_data) / len(mood_data)
    caption = f"💜 Твоё настроение за 30 дней\nСредняя оценка: {avg:.1f}/5"
    with open(chart_path, "rb") as img:
        await update.message.reply_photo(photo=img, caption=caption)
    os.remove(chart_path)

async def dates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dates = load_dates()
    if not dates:
        await update.message.reply_text(
            "Важных дат пока нет.\n\nДобавить: /adddate Годовщина 14 02\n(название, день, месяц)"
        )
        return
    months = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    lines = ["📅 *Важные даты:*\n"]
    for d in sorted(dates, key=lambda x: (x["month"], x["day"])):
        lines.append(f"• {d['name']} — {d['day']} {months[d['month']]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def adddate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Формат: /adddate Название день месяц\nПример: /adddate Годовщина 14 2"
        )
        return
    try:
        day = int(args[-2])
        month = int(args[-1])
    except ValueError:
        await update.message.reply_text("День и месяц должны быть числами.")
        return
    name = " ".join(args[:-2])
    dates = load_dates()
    dates.append({"name": name, "month": month, "day": day})
    save_dates(dates)
    await update.message.reply_text(f"✅ Добавила: {name} — {day}.{month:02d}")


# --- Core message processing ---

async def process_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, user: str):
    chat_id = update.effective_chat.id
    result = classify_message(text)
    intent = result.get("intent", "general")

    if intent == "expense":
        amount = result.get("amount", 0)
        category = result.get("category", "Другое")
        description = result.get("description", text)
        save_expense(user, amount, category, description)
        await update.message.reply_text(
            f"✅ Записала!\n💸 {description} — {amount:.0f}₽\n📂 {category}"
        )

    elif intent == "reminder":
        now = datetime.now()
        reminder_text = result.get("text", text)
        if result.get("is_relative"):
            delay_min = int(result.get("delay_minutes", 30))
            run_at = now + timedelta(minutes=delay_min)
        else:
            time_str = result.get("time_str", "")
            try:
                h, m = map(int, time_str.split(":"))
                run_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if run_at <= now:
                    run_at += timedelta(days=1)
            except Exception:
                await send_voice(update, "Не смогла разобрать время. Напиши например: напомни в 18:30 позвонить маме")
                return
        delay_sec = (run_at - now).total_seconds()
        context.job_queue.run_once(
            job_reminder,
            when=delay_sec,
            data={"chat_id": chat_id, "text": reminder_text},
        )
        await update.message.reply_text(
            f"⏰ Напомню в {run_at.strftime('%H:%M')}: {reminder_text}"
        )

    elif intent == "weather":
        city = result.get("city") or CITY
        if result.get("period") == "tomorrow":
            weather_text = get_weather_forecast(city)
        else:
            weather_text = get_weather(city)
        if weather_text:
            await send_voice(update, weather_text)
        else:
            await send_voice(update, "Не могу получить погоду. Нужен ключ OPENWEATHER_API_KEY.")

    elif intent == "shopping_add":
        items = result.get("items", [])
        if items:
            add_shopping_items(items)
            await update.message.reply_text(f"🛒 Добавила: {', '.join(items)}")
        else:
            await update.message.reply_text("Не поняла что добавить. Попробуй: «добавь молоко и хлеб»")

    elif intent == "recipe":
        await update.message.chat.send_action("record_voice")
        answer = await ask_recipe(text)
        await send_voice(update, answer)

    elif intent == "mood":
        score = int(result.get("score", 3))
        note = result.get("note", text)
        save_mood(score, note)
        responses = {
            1: "Слышу тебя, Даша. Всё будет хорошо, я рядом 💜",
            2: "Понимаю, бывают тяжёлые дни. Если нужно — расскажи мне всё 💜",
            3: "Средненько, но это нормально. Надеюсь день станет лучше! 🌸",
            4: "Рада слышать! Хорошее настроение — это здорово 😊",
            5: "Замечательно! Так держать, Дашечка! 🌟",
        }
        await update.message.reply_text(responses.get(score, responses[3]))

    else:
        await update.message.chat.send_action("record_voice")
        answer = await ask_ai(text)
        await send_voice(update, answer)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    user = update.effective_user.first_name or "Даша"
    await process_input(update, context, update.message.text, user)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    await update.message.chat.send_action("record_voice")
    user = update.effective_user.first_name or "Даша"

    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        tmp_path = f.name
    await voice_file.download_to_drive(tmp_path)
    with open(tmp_path, "rb") as audio:
        transcription = groq_client.audio.transcriptions.create(
            file=("voice.ogg", audio),
            model="whisper-large-v3",
        )
    os.remove(tmp_path)

    await process_input(update, context, transcription.text, user)


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("analysis", analysis))
    app.add_handler(CommandHandler("shopping", shopping_cmd))
    app.add_handler(CommandHandler("mood", mood_cmd))
    app.add_handler(CommandHandler("dates", dates_cmd))
    app.add_handler(CommandHandler("adddate", adddate_cmd))
    app.add_handler(CallbackQueryHandler(shopping_callback, pattern="^shop_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    app.job_queue.run_repeating(job_compliment, interval=3600, first=10)
    app.job_queue.run_daily(job_morning_briefing, time=dt_time(8, 0))
    app.job_queue.run_daily(job_mood_check, time=dt_time(21, 0))

    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
