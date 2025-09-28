#!/usr/bin/env python3
import os
import re
import asyncio
from typing import Final

import dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

from api_client import ask_api, extract_answer_text, extract_source_titles


dotenv.load_dotenv()


BOT_TOKEN_ENV_VARS: Final = [
    "TELEGRAM_BOT_TOKEN",
    "TG_BOT_TOKEN",
    "BOT_TOKEN",
]


def _split_message(text: str, *, limit: int = 3900) -> list[str]:
    # Splits text into chunks under Telegram limits, trying to break on paragraph boundaries
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = []
    current_len = 0
    for line in text.split("\n"):
        add_len = len(line) + (1 if current else 0)
        if current_len + add_len > limit:
            parts.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            if current:
                current.append(line)
                current_len += add_len
            else:
                current = [line]
                current_len = len(line)
    if current:
        parts.append("\n".join(current))
    return parts


def _escape_markdown_v1(text: str) -> str:
    # Minimal escaping for Telegram Markdown (v1): only _, *, [, ], (, ), `
    specials = set(["_", "*", "[", "]", "(", ")", "`"])
    result_chars = []
    for ch in text:
        if ch in specials:
            result_chars.append("\\" + ch)
        else:
            result_chars.append(ch)
    return "".join(result_chars)


def _escape_markdown_v2(text: str) -> str:
    # Telegram MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    specials = set(list("_[]()~`>#+-=|{}.!*"))
    out = []
    for ch in text:
        if ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


_CODE_SNIPPET_RE = re.compile(r"(```.*?```|`[^`]*`)", flags=re.DOTALL)


def _normalize_markdown_for_telegram(text: str) -> str:
    """Convert common Markdown constructs into Telegram-friendly Markdown (v1)."""

    def _normalize_segment(segment: str) -> str:
        # Normalize list markers that use '*' to '-' to avoid conflicts with bold
        segment = re.sub(r"(?m)^(\s*)\* +", r"\1- ", segment)
        # Convert double emphasis to single-star bold
        segment = re.sub(r"\*\*(.+?)\*\*", r"*\1*", segment)
        # Convert double underscores to single underscore emphasis
        segment = re.sub(r"__(.+?)__", r"_\1_", segment)
        return segment

    parts: list[str] = []
    last_end = 0
    for match in _CODE_SNIPPET_RE.finditer(text):
        prefix = text[last_end:match.start()]
        if prefix:
            parts.append(_normalize_segment(prefix))
        parts.append(match.group(0))
        last_end = match.end()
    remainder = text[last_end:]
    if remainder:
        parts.append(_normalize_segment(remainder))
    return "".join(parts)


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


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reset chat history for this chat
    context.chat_data["history"] = []
    await update.message.reply_text("Starting a new conversation. How can I help?")


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
        # Maintain per-chat history: list of {role, content}
        history = context.chat_data.get("history")
        if not isinstance(history, list):
            history = []

        history.append({"role": "user", "content": user_text})

        resp = await ask_api(user_text, chat_history=history)
        if isinstance(resp, dict) and resp.get("error"):
            # Show a friendly message without technical details
            await update.message.reply_text(
                "Sorry, I couldn't get an answer right now. Please try again later."
            )
            return

        answer = extract_answer_text(resp)
        if not answer:
            answer = "Sorry, I couldn't find an answer. Please try rephrasing your question."

        # Print raw answer to console for debugging/verification
        try:
            print("\n--- RAW ANSWER START ---\n" + str(answer) + "\n--- RAW ANSWER END ---\n")
        except Exception:
            pass

        # Append sources if available (with safe Markdown)
        titles = extract_source_titles(resp, max_titles=5)
        if titles:
            # Print sources to console for verification
            try:
                print("--- SOURCES START ---")
                for t in titles:
                    print(t)
                print("--- SOURCES END ---\n")
            except Exception:
                pass

            sources_block_lines = ["", "", "*Sources:*"]
            for title in titles:
                safe_title = _escape_markdown_v1(title)
                sources_block_lines.append(f"- {safe_title}")
            answer = answer + "\n".join(sources_block_lines)

        normalized_answer = _normalize_markdown_for_telegram(answer)

        # Telegram limits message length; split if too long
        # Send original Markdown (v1) to preserve formatting from API
        for chunk in _split_message(normalized_answer, limit=3900):
            try:
                await update.message.reply_text(
                    chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
            except BadRequest:
                await update.message.reply_text(chunk)

        # Save assistant reply to history (truncate long chats)
        history.append({"role": "assistant", "content": normalized_answer[:4000]})
        # Keep last N turns
        max_messages = 20
        if len(history) > max_messages:
            history = history[-max_messages:]
        context.chat_data["history"] = history
    except Exception as exc:
        # Generic friendly fallback
        await update.message.reply_text(
            "Sorry, something went wrong. Please try again in a moment."
        )
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
    app.add_handler(CommandHandler("new", new_chat))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()


