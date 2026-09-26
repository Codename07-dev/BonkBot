"""Connections (remote group management from DM) and export/import backups."""

import functools
import html
import io
import json
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import db

log = logging.getLogger("unkilbonker.util")


# ---------------------------------------------------------------- connect

async def connect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if update.effective_chat.type == "private":
        args = context.args or []
        if not args:  # Rose-style: list feds you can connect to
            cur = db.get_connection(user.id)
            await update.effective_message.reply_text(
                ("🔗 Currently connected to chat "
                 f"<code>{cur}</code>. Use /connect <chat_id> to switch, "
                 "/disconnect to end." if cur else
                 "Usage: /connect <chat_id> - or run /connect inside a group "
                 "first and I'll remember it."))
            return
    if update.effective_chat.type != "private":
        # remember this chat, then tell the user to switch to DM
        prev = db.get_connection(user.id)
        if prev and prev != update.effective_chat.id:
            db.set_conn_last(user.id, prev)
        db.set_connection(user.id, update.effective_chat.id)
        await update.effective_message.reply_text(
            f"🔗 This chat connected! Now open a private chat with me and use "
            f"group commands there.\n(To switch to another chat later: "
            f"/connect <chat_id> here in DM)")
        return
    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        current = db.get_connection(user.id)
        await update.effective_message.reply_text(
            "Usage (in DM): /connect <chat_id>\n"
            "Tip: run /connect inside the group itself first - I'll remember it.")
        return
    chat_id = int(args[0])
    try:
        chat = await context.bot.get_chat(chat_id)
    except TelegramError as e:
        await update.effective_message.reply_text(
            f"Couldn't find that chat: {e}")
        return
    # user must be an admin of the target chat
    try:
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.effective_message.reply_text(
                "You're not an admin there.")
            return
    except TelegramError:
        await update.effective_message.reply_text(
            "I can't check that chat - is the bot in it?")
        return
    prev = db.get_connection(user.id)
    if prev and prev != chat_id:
        db.set_conn_last(user.id, prev)
    db.set_connection(user.id, chat_id)
    await update.effective_message.reply_text(
        f"🔗 Connected to <b>{html.escape(chat.title or str(chat_id))}</b>.\n"
        "Group commands you run here now apply to that group.\n"
        "/disconnect when done.", parse_mode=ParseMode.HTML)


async def disconnect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if db.del_connection(update.effective_user.id):
        await update.effective_message.reply_text(
            "🔌 Disconnected. Use /reconnect to restore it.")
    else:
        await update.effective_message.reply_text("You weren't connected.")


async def reconnect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    last = db.get_conn_last(update.effective_user.id)
    if not last:
        await update.effective_message.reply_text(
            "No previous connection to restore.")
        return
    try:
        chat = await context.bot.get_chat(last)
        title = html.escape(chat.title or str(last))
    except TelegramError:
        title = str(last)
    db.set_connection(update.effective_user.id, last)
    await update.effective_message.reply_text(
        f"🔗 Reconnected to <b>{title}</b>.", parse_mode=ParseMode.HTML)


async def connection_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = db.get_connection(update.effective_user.id)
    if not cid:
        await update.effective_message.reply_text("No active connection.")
        return
    try:
        chat = await context.bot.get_chat(cid)
        title = chat.title or str(cid)
    except TelegramError:
        title = str(cid)
    await update.effective_message.reply_text(
        f"🔗 Connected to: <b>{html.escape(title)}</b> "
        f"(<code>{cid}</code>)", parse_mode=ParseMode.HTML)


def connected(fn):
    """Decorator: in a private chat with an active connection, redirect
    update.effective_chat to the connected group. Replies still go to the DM."""
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is not None and chat.type == "private" and update.effective_user:
            cid = db.get_connection(update.effective_user.id)
            if cid:
                try:
                    group = await context.bot.get_chat(cid)
                    update._effective_chat = group
                except TelegramError:
                    pass
        return await fn(update, context)
    return wrapper


# ----------------------------------------------------------- export/import

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_or_connected(update, context):
        return
    chat_id = update.effective_chat.id
    data = {
        "chat_id": chat_id,
        "settings": {},
        "notes": {},
        "filters": {},
        "blocklist": db.list_blocklist(chat_id),
        "allowlist": db.list_allowlist(chat_id),
        "locks": sorted(db.get_locks(chat_id)),
    }
    with db._connect() as conn:
        for key, value in conn.execute(
                "SELECT key, value FROM settings WHERE chat_id=?", (chat_id,)):
            data["settings"][key] = value
        for name, text in conn.execute(
                "SELECT name, text FROM notes WHERE chat_id=?", (chat_id,)):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {"t": "text", "c": text}
            data["notes"][name] = payload
        for trig, text in conn.execute(
                "SELECT trigger, text FROM filters WHERE chat_id=?", (chat_id,)):
            data["filters"][trig] = text

    buf = io.BytesIO(json.dumps(data, indent=2).encode())
    buf.name = f"unkilbonker_backup_{str(chat_id).replace('-', '_')}.json"
    await update.effective_message.reply_document(
        document=buf,
        caption="📦 Group configuration backup. Restore with /import "
                "(reply to this file).")


async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_or_connected(update, context):
        return
    msg = update.effective_message
    source = msg.reply_to_message
    if not source or not source.document:
        await msg.reply_text("Reply to a backup .json file with /import.")
        return
    try:
        tg_file = await source.document.get_file()
        raw = await tg_file.download_as_bytearray()
        data = json.loads(bytes(raw).decode())
    except (ValueError, TelegramError, UnicodeDecodeError) as e:
        await msg.reply_text(f"That doesn't look like a valid backup: {e}")
        return
    chat_id = update.effective_chat.id
    restored = 0
    for key, value in data.get("settings", {}).items():
        db.set_setting(chat_id, key, value)
        restored += 1
    for name, payload in data.get("notes", {}).items():
        db.save_note(chat_id, name,
                     payload if isinstance(payload, str)
                     else json.dumps(payload))
        restored += 1
    for trig, text in data.get("filters", {}).items():
        db.save_filter(chat_id, trig, text)
        restored += 1
    for w in data.get("blocklist", []):
        db.add_blocklist(chat_id, w)
        restored += 1
    for d in data.get("allowlist", []):
        db.add_allowlist(chat_id, d)
        restored += 1
    if data.get("locks"):
        db.set_locks(chat_id, set(data["locks"]))
        restored += len(data["locks"])
    await msg.reply_text(
        f"✅ Imported {restored} item(s) into this chat.")


async def _admin_or_connected(update: Update, context) -> bool:
    from moderation import user_is_admin
    if await user_is_admin(update, context):
        return True
    await update.effective_message.reply_text(
        "Admins only.")
    return False
