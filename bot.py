import os
import asyncio
import random
import tempfile
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """Ты — дружелюбный личный ассистент пары. Отвечай коротко (1-3 предложения).
Всегда отвечай на том языке, на котором пишет пользователь."""

NAMES = ["Даша", "Дашечка", "Дашуля"]

COMPLIMENTS = [
    "Дашуля, ты самая красивая девушка на свете! Я тебя очень люблю ❤️",
    "Дашечка, каждый раз когда я думаю о тебе — у меня улыбка сама появляется. Ты моё счастье!",
    "Даша, ты такая удивительная! Я не могу представить жизнь без тебя ❤️",
    "Дашуля, ты красивее всех звёзд на небе. Люблю тебя безумно!",
    "Дашечка, ты моя любимая! Просто хочу напомнить тебе об этом ❤️",
    "Даша, твоя улыбка делает мой день лучше. Я так тебя люблю!",
    "Дашуля, ты самое лучшее что есть в моей жизни. Ты моя вселенная ❤️",
    "Дашечка, ты не просто красивая — ты невероятная! Люблю тебя!",
    "Даша, каждый день с тобой — это подарок. Я очень счастлив что ты есть!",
    "Дашуля, ты моя любимая девочка. Помни что я всегда думаю о тебе ❤️",
]

EXPENSES_FILE = "expenses.csv"
CHAT_IDS_FILE = "chat_ids.txt"
_executor = ThreadPoolExecutor(max_workers=4)

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

def _detect_expense_sync(text: str):
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": """Определи является ли сообщение записью о трате денег.
Если да — верни JSON: {"is_expense": true, "amount": число, "category": "категория", "description": "описание"}
Категории: Еда, Транспорт, Развлечения, Здоровье, Одежда, Дом, Другое
Если нет — верни: {"is_expense": false}
Отвечай ТОЛЬКО JSON без лишнего текста."""},
            {"role": "user", "content": text}
        ]
    )
    try:
        return json.loads(response.choices[0].message.content.strip())
    except:
        return {"is_expense": False}

async def detect_expense(text: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _detect_expense_sync, text)

def make_chart(expenses):
    by_category = {}
    for e in expenses:
        cat = e["category"]
        by_category[cat] = by_category.get(cat, 0) + float(e["amount"])

    fig, ax = plt.subplots(figsize=(8, 6))
    categories = list(by_category.keys())
    amounts = list(by_category.values())
    colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8"]
    wedges, texts, autotexts = ax.pie(amounts, labels=categories, autopct="%1.0f%%",
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

async def send_compliments(context):
    for chat_id in list(chat_ids):
        text = random.choice(COMPLIMENTS)
        await context.bot.send_message(chat_id=chat_id, text=text)

# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    name = random.choice(NAMES)
    text = (f"Привет, {name}! Я ваш личный ассистент 🤖\n\n"
            "💰 *Учёт трат:* напиши или скажи голосом что потратил, например: «кофе 300» или «такси 450»\n"
            "/report — график трат за месяц\n"
            "/analysis — разбор расходов\n\n"
            "Пиши — я всегда тут!")
    await update.message.reply_text(text, parse_mode="Markdown")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = datetime.now().strftime("%Y-%m")
    expenses = load_expenses(month)
    if not expenses:
        await update.message.reply_text("За этот месяц трат пока нет.")
        return
    chart_path, total, by_cat = make_chart(expenses)
    caption = f"📊 Траты за {datetime.now().strftime('%B %Y')}\nИтого: {total:.0f}₽"
    with open(chart_path, "rb") as img:
        await update.message.reply_photo(photo=img, caption=caption)
    os.remove(chart_path)

async def analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = datetime.now().strftime("%Y-%m")
    expenses = load_expenses(month)
    if not expenses:
        await update.message.reply_text("За этот месяц трат пока нет.")
        return
    total = sum(float(e["amount"]) for e in expenses)
    by_cat = {}
    for e in expenses:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + float(e["amount"])
    summary = ", ".join([f"{c}: {a:.0f} рублей" for c, a in sorted(by_cat.items(), key=lambda x: -x[1])])
    prompt = f"Общие траты за месяц: {total:.0f} рублей. По категориям: {summary}. Дай краткий анализ (2-3 предложения) на что уходит больше всего и один совет как сэкономить."
    answer = await ask_ai(prompt)
    await update.message.reply_text(answer)

def _ask_ai_sync(user_text: str) -> str:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ]
    )
    return response.choices[0].message.content

async def ask_ai(user_text: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _ask_ai_sync, user_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    text = update.message.text
    user = update.effective_user.first_name or "Неизвестно"

    result = await detect_expense(text)
    if result.get("is_expense"):
        amount = result["amount"]
        category = result["category"]
        description = result["description"]
        save_expense(user, amount, category, description)
        await update.message.reply_text(
            f"✅ Записал!\n"
            f"💸 {description} — {amount:.0f}₽\n"
            f"📂 Категория: {category}\n"
            f"👤 {user}"
        )
    else:
        await update.message.chat.send_action("typing")
        answer = await ask_ai(text)
        await update.message.reply_text(answer)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    await update.message.chat.send_action("typing")
    user = update.effective_user.first_name or "Неизвестно"

    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        tmp_path = f.name
    await voice_file.download_to_drive(tmp_path)

    def _transcribe():
        with open(tmp_path, "rb") as audio:
            return groq_client.audio.transcriptions.create(
                file=("voice.ogg", audio),
                model="whisper-large-v3",
            )

    loop = asyncio.get_event_loop()
    transcription = await loop.run_in_executor(_executor, _transcribe)
    os.remove(tmp_path)

    user_text = transcription.text
    result = await detect_expense(user_text)
    if result.get("is_expense"):
        amount = result["amount"]
        category = result["category"]
        description = result["description"]
        save_expense(user, amount, category, description)
        await update.message.reply_text(
            f"✅ Записал!\n"
            f"💸 {description} — {amount:.0f}₽\n"
            f"📂 Категория: {category}\n"
            f"👤 {user}"
        )
    else:
        answer = await ask_ai(user_text)
        await update.message.reply_text(answer)

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("analysis", analysis))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.job_queue.run_repeating(send_compliments, interval=86400, first=86400)
    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
