#!/usr/bin/env python3
import os
import asyncio
from typing import Final

import dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

from api_client import ask_api, extract_answer_text


dotenv.load_dotenv()


BOT_TOKEN_ENV_VARS: Final = [
    "TELEGRAM_BOT_TOKEN",
    "TG_BOT_TOKEN",
    "BOT_TOKEN",
]


def _get_bot_token() -> str:
    for name in BOT_TOKEN_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError(
        "Bot token not found. Set TELEGRAM_BOT_TOKEN in .env or environment."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hey! I know a lot about using models and longevity research. Ask me anything and I'll be happy to help!"
    )


async def _typing_indicator(bot, chat_id: int) -> None:
    # Periodically sends ChatAction.TYPING until the task is cancelled
    while True:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(4)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    typing_task = asyncio.create_task(_typing_indicator(context.bot, chat_id))
    try:
        resp = await ask_api(user_text)
        answer = extract_answer_text(resp)
        if not answer:
            answer = "Failed to extract answer.answer from the API response."
        await update.message.reply_text(answer)
    except Exception as exc:
        await update.message.reply_text(f"Error while requesting the API: {exc}")
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    token = _get_bot_token()
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()


