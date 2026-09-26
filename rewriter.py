"""Optional: AI rewriter for channel posts (same engine as the standalone bot).

Active only when CHANNEL_ID and AI_API_KEY are set in the environment.
Rewrites every new post in that channel using any OpenAI-compatible API
(Gemini by default) and edits the post in place.
"""

import logging
import os
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

log = logging.getLogger("unkilbonker.rewriter")

MAX_CAPTION_LEN = 1024
MAX_TEXT_LEN = 4096

AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_BASE_URL = os.environ.get(
    "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
).strip()
AI_MODEL = os.environ.get("AI_MODEL", "gemini-2.5-flash").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
REWRITE_STYLE = os.environ.get(
    "REWRITE_STYLE",
    "engaging, professional, and easy to read, with tasteful emoji section "
    "headers and clean formatting",
)

SYSTEM_PROMPT = f"""You are an expert copywriter for a Telegram channel that \
shares apps, games, APKs and software.

You will receive the text of a channel post (it may be the caption of a \
photo/video/document post). Rewrite it so it is {REWRITE_STYLE}.

STRICT RULES:
1. Keep the meaning and every fact: app name, version, size, requirements, \
features, file details.
2. Preserve ALL links, URLs, @usernames, and hashtags EXACTLY as given.
3. If a download link or button text is present, keep it on its own line.
4. Use Telegram HTML formatting only: <b>, <i>, <u>, <s>, <code>, <a href>. \
No Markdown, no code fences.
5. Do NOT invent features, ratings, or details that are not in the original.
6. Reply with ONLY the rewritten post text. No preamble, no quotes.
7. Keep it within Telegram's limits: the rewritten post must not be longer \
than the original by more than 20%.
"""


def enabled() -> bool:
    return bool(CHANNEL_ID and AI_API_KEY)


def _client():
    from openai import OpenAI

    return OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


def strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.fullmatch(r"```(?:html|xml|text)?\s*(.*?)\s*```", text, re.DOTALL)
    return m.group(1).strip() if m else text


def rewrite_with_ai(original: str, max_len: int) -> str:
    response = _client().chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Rewrite this Telegram channel post (output must be under "
                f"{max_len} characters):\n\n{original}")},
        ],
        temperature=0.6,
        max_tokens=1600,
    )
    result = strip_code_fence(response.choices[0].message.content or "")
    if not result:
        raise ValueError("AI returned an empty rewrite")
    return result


def strip_all_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text)
    return re.sub(r"<[^>]+>", "", text)


def _from_allowed_chat(chat) -> bool:
    if not CHANNEL_ID:
        return False
    cid = CHANNEL_ID.lstrip("-")
    if chat.username and chat.username.lower() == CHANNEL_ID.lstrip("@").lower():
        return True
    return str(chat.id).lstrip("-") == cid or str(chat.id) == CHANNEL_ID


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if msg is None or not _from_allowed_chat(msg.chat):
        return

    original = msg.caption if msg.caption else msg.text
    is_caption = bool(msg.caption)
    max_len = MAX_CAPTION_LEN if is_caption else MAX_TEXT_LEN

    if not original or not original.strip():
        return

    log.info("Rewriting post %s from '%s'", msg.message_id,
             msg.chat.title or msg.chat.id)

    import asyncio

    try:
        rewritten = await asyncio.get_running_loop().run_in_executor(
            None, lambda: rewrite_with_ai(original, max_len))
    except Exception as exc:  # noqa: BLE001
        log.error("AI rewrite failed, leaving post untouched: %s", exc)
        return

    if len(rewritten) > max_len:
        rewritten = rewritten[: max_len - 3].rstrip() + "…"

    try:
        if is_caption:
            await msg.edit_caption(caption=rewritten, parse_mode=ParseMode.HTML)
        else:
            await msg.edit_text(rewritten, parse_mode=ParseMode.HTML)
        log.info("Post %s rewritten.", msg.message_id)
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        try:  # plain-text fallback
            plain = strip_all_html(rewritten)
            if is_caption:
                await msg.edit_caption(caption=plain)
            else:
                await msg.edit_text(plain)
        except TelegramError:
            log.error("Edit failed: %s", exc)
    except TelegramError as exc:
        log.error("Edit failed: %s", exc)
