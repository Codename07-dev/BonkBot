"""TagAll - mention every known member of the group (Kafka-style)."""

import asyncio
import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, MessageHandler, filters

import db
from moderation import user_is_admin

log = logging.getLogger("unkilbonker.tagall")

CHUNK_LIMIT = 4096          # telegram message limit
MENTIONS_PER_MSG = 50       # keep entity counts safe
DELAY_BETWEEN = 1.2         # seconds - avoid hitting flood limits

_seen: dict = {}            # (chat, user) -> (name, username) last written
_cancel_key = "tagall_cancel"


# ----------------------------------------------------------- tracking

async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remember everyone who speaks, so /tagall can reach them."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    name = (user.first_name or "")[:64]
    username = (user.username or "")[:64]
    prev = _seen.get((msg.chat.id, user.id))
    if prev == (name, username):
        return
    _seen[(msg.chat.id, user.id)] = (name, username)
    db.upsert_member(msg.chat.id, user.id, name, username)


# ----------------------------------------------------------- commands

async def _tag_all(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   with_names: bool) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        await msg.reply_text("Only admins can tag everyone.")
        return
    chat = update.effective_chat
    members = [(uid, name, uname) for uid, name, uname in db.get_members(chat.id)
               if uid != context.bot.id]
    if not members:
        await msg.reply_text(
            "I haven't seen any members yet - people need to talk first, "
            "or add me as admin so I can see them.")
        return

    context.bot_data[f"{_cancel_key}:{chat.id}"] = False
    custom = " ".join(context.args or [])
    header = (f"📢 <b>{html.escape(custom)}</b>\n\n" if custom
              else "📢 <b>Tagging everyone:</b>\n\n")

    # build chunks of mentions
    chunks, current, current_len, count = [], [header], len(header), 0
    for uid, name, uname in members:
        label = html.escape((name or uname or str(uid))[:32])
        if with_names:
            mention = f"@{label} "
        else:
            mention = f'<a href="tg://user?id={uid}">{label}</a> '
        if count >= MENTIONS_PER_MSG or current_len + len(mention) > CHUNK_LIMIT - 20:
            chunks.append(current)
            current, current_len, count = [], 0, 0
        current.append(mention)
        current_len += len(mention)
        count += 1
    if current:
        chunks.append(current)

    sent = 0
    for chunk in chunks:
        if context.bot_data.get(f"{_cancel_key}:{chat.id}"):
            await msg.reply_text(f"⏹ Tagging stopped at {sent}.")
            return
        try:
            await context.bot.send_message(
                chat.id, "".join(chunk), parse_mode=ParseMode.HTML)
            sent += 1
            if len(chunks) > 1:
                await asyncio.sleep(DELAY_BETWEEN)
        except TelegramError as e:
            log.warning("tagall send failed: %s", e)
            break
    await msg.reply_text(f"✅ Tagged {len(members)} member(s)."
                        if sent else "Couldn't send tags.")


async def tagall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _tag_all(update, context, with_names=False)


async def utagall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _tag_all(update, context, with_names=True)


async def tagall_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    context.bot_data[f"{_cancel_key}:{update.effective_chat.id}"] = True
    await update.effective_message.reply_text("⏹ Stopping the tag...")


def on_member_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg and msg.left_chat_member:
        db.del_member(msg.chat.id, msg.left_chat_member.id)
        _seen.pop((msg.chat.id, msg.left_chat_member.id), None)


def register(app) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, track_members))
