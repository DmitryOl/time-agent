import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

DB_PATH = Path("/app/data/agent.sqlite3")
TOKEN = os.environ["TG_BOT_TOKEN"]
OWNER_ID = int(os.environ.get("TG_ALLOWED_USER_ID", "0"))

KINDS = {
    "wish": "желание",
    "goal": "цель",
    "task": "задача",
}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


async def allowed(update: Update) -> bool:
    user = update.effective_user

    if user is not None and OWNER_ID != 0 and user.id == OWNER_ID:
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "Доступ закрыт. Отправьте /id, затем укажите полученное число "
            "в TG_ALLOWED_USER_ID."
        )

    return False


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.effective_message:
        await update.effective_message.reply_text(
            f"Ваш Telegram user ID: {update.effective_user.id}"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return

    await update.effective_message.reply_text(
        "Привет! Доступные команды:\n"
        "/wish текст — добавить желание\n"
        "/goal текст — добавить цель\n"
        "/task текст — добавить задачу\n"
        "/list — показать последние записи"
    )


async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return

    command = update.effective_message.text.split()[0].split("@")[0][1:]
    text = " ".join(context.args).strip()

    if not text:
        await update.effective_message.reply_text(
            f"Добавьте текст: /{command} мой текст"
        )
        return

    if len(text) > 2000:
        await update.effective_message.reply_text(
            "Пока принимаю записи длиной до 2000 символов."
        )
        return

    user_id = update.effective_user.id
    timestamp = now_utc()

    with connect() as db:
        cursor = db.execute(
            "INSERT INTO entries (user_id, kind, text, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, command, text, timestamp),
        )
        db.execute(
            "INSERT INTO action_log (user_id, action, details, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                user_id,
                "create_entry",
                f"{command}:{cursor.lastrowid}",
                timestamp,
            ),
        )

    await update.effective_message.reply_text(
        f"Сохранено: {KINDS[command]} №{cursor.lastrowid}."
    )


async def list_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return

    user_id = update.effective_user.id

    with connect() as db:
        rows = db.execute(
            "SELECT id, kind, text FROM entries "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user_id,),
        ).fetchall()
        db.execute(
            "INSERT INTO action_log (user_id, action, details, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, "list_entries", "last_10", now_utc()),
        )

    if not rows:
        await update.effective_message.reply_text("Записей пока нет.")
        return

    lines = [f"№{row[0]} · {KINDS[row[1]]}: {row[2]}" for row in rows]
    await update.effective_message.reply_text("\n\n".join(lines))


def main():
    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("start", start))

    for command in KINDS:
        app.add_handler(CommandHandler(command, add_entry))

    app.add_handler(CommandHandler("list", list_entries))
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()