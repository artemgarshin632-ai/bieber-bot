import os
import asyncio
import random
import tempfile
import edge_tts
from dotenv import load_dotenv
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """Ты — суперфанат Джастина Бибера! Ты обожаешь его всем сердцем.
Ты знаешь все его песни, альбомы, факты из его жизни.
Отвечай с энтузиазмом, коротко (1-3 предложения).
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

FACTS = [
    "Джастин Бибер родился 1 марта 1994 года в Канаде!",
    "Его первый альбом My World вышел в 2009 году, когда ему было всего 15 лет!",
    "Его менеджер Скутер Браун нашёл его через YouTube в 2008 году.",
    "Песня Baby стала одним из самых просматриваемых видео на YouTube.",
    "Джастин женился на Хейли Болдуин в 2018 году.",
    "Он умеет играть на барабанах, гитаре, фортепиано и трубе.",
    "Альбом Justice вышел в 2021 году и посвящён социальной справедливости.",
    "Джастин дружит с Эдом Шираном, вместе записали Love Yourself и I Don't Care.",
]

SONGS = [
    "Baby — классика, с которой всё началось!",
    "Love Yourself — красивая и честная песня.",
    "Sorry — невозможно не танцевать!",
    "Peaches — летний хит из альбома Justice.",
    "Stay совместно с The Kid LAROI — абсолютный хит!",
    "Ghost — очень душевная баллада.",
    "Yummy — залипательный трек.",
    "What Do You Mean — настоящий шедевр!",
]

CHAT_IDS_FILE = "chat_ids.txt"

def load_chat_ids():
    ids = set()
    # из файла
    if os.path.exists(CHAT_IDS_FILE):
        with open(CHAT_IDS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.add(int(line))
    # из переменной окружения (для Railway)
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

VOICE = "ru-RU-DmitryNeural"

async def send_voice(update: Update, text: str):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(tmp_path)
    with open(tmp_path, "rb") as audio:
        await update.message.reply_voice(voice=audio)
    os.remove(tmp_path)

async def send_voice_to_chat(context, chat_id: int, text: str):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(tmp_path)
    with open(tmp_path, "rb") as audio:
        await context.bot.send_voice(chat_id=chat_id, voice=audio)
    os.remove(tmp_path)

async def send_compliments(context):
    for chat_id in list(chat_ids):
        text = random.choice(COMPLIMENTS)
        await send_voice_to_chat(context, chat_id, text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    name = random.choice(NAMES)
    text = f"Привет, {name}! Я твой личный фанат-бот Джастина Бибера! Пиши мне что угодно, или используй команды: /fact — узнать факт о Бибере, /song — что послушать прямо сейчас!"
    await send_voice(update, text)

async def fact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    text = "Знаешь ли ты, что... " + random.choice(FACTS)
    await send_voice(update, text)

async def song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    text = "Сейчас советую послушать: " + random.choice(SONGS)
    await send_voice(update, text)

async def ask_ai(user_text: str) -> str:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ]
    )
    return response.choices[0].message.content

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    await update.message.chat.send_action("record_voice")
    answer = await ask_ai(update.message.text)
    await send_voice(update, answer)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    save_chat_id(update.effective_chat.id)
    await update.message.chat.send_action("record_voice")

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

    user_text = transcription.text
    answer = await ask_ai(user_text)
    await send_voice(update, answer)

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    token = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fact", fact))
    app.add_handler(CommandHandler("song", song))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.job_queue.run_repeating(send_compliments, interval=3600, first=10)
    print("Бот запущен! Нажми Ctrl+C чтобы остановить.")
    app.run_polling()

if __name__ == "__main__":
    main()
