"""Group features: welcome, notes, filters, locks, antiflood, afk, info."""

import html
import logging
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes, MessageHandler, filters

import db
from moderation import user_is_admin

log = logging.getLogger("unkilbonker.features")

FLOOD_WINDOW = 10  # seconds
_flood = defaultdict(lambda: defaultdict(deque))  # chat -> user -> timestamps

LOCK_TYPES = {
    "msgs": lambda m, u: True,
    "stickers": lambda m, u: bool(m.sticker),
    "gifs": lambda m, u: bool(m.animation),
    "photos": lambda m, u: bool(m.photo),
    "videos": lambda m, u: bool(m.video),
    "audio": lambda m, u: bool(m.audio),
    "voice": lambda m, u: bool(m.voice),
    "video_notes": lambda m, u: bool(m.video_note),
    "documents": lambda m, u: bool(m.document),
    "polls": lambda m, u: bool(m.poll),
    "games": lambda m, u: bool(m.game),
    "urls": lambda m, u: any(
        e.type in ("url", "text_link")
        for e in (m.entities or []) + (m.caption_entities or [])),
    "forwards": lambda m, u: bool(getattr(m, "forward_origin", None)),
    "captions": lambda m, u: bool(m.caption),
    "edits": lambda m, u: bool(m.edit_date),
}


# ----------------------------------------------------------------- welcome

WELCOME_DEFAULT = (
    "Hey {mention}, welcome to <b>{chatname}</b>! 🎉\n"
    "You're member #{count} - read the pinned message and enjoy your stay."
)


def render_welcome(template: str, user, chat, count: int) -> str:
    mapping = {
        "{first}": html.escape(user.first_name or ""),
        "{last}": html.escape(user.last_name or ""),
        "{username}": f"@{user.username}" if user.username else user.first_name,
        "{mention}": user.mention_html(),
        "{chatname}": html.escape(chat.title or "the group"),
        "{count}": str(count),
    }
    text = template
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    msg = update.effective_message
    template = None
    if msg.reply_to_message:
        template = msg.reply_to_message.text_html or \
            msg.reply_to_message.caption_html
    elif context.args:
        raw = " ".join(context.args)
        template = html.escape(raw)
    if not template:
        await msg.reply_text("Usage: /setwelcome <text> (or reply to a message).\n"
                            "Placeholders: {first} {mention} {username} "
                            "{chatname} {count}")
        return
    db.set_setting(update.effective_chat.id, "welcome_text", template)
    db.set_setting(update.effective_chat.id, "welcome_on", "1")
    await msg.reply_text("✅ Welcome message set and enabled.")


async def welcome_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    chat_id = update.effective_chat.id
    args = context.args or []
    if args and args[0].lower() in ("on", "off"):
        db.set_setting(chat_id, "welcome_on", "1" if args[0] == "on" else "0")
        await update.effective_message.reply_text(
            f"Welcomes are now {'ON' if args[0] == 'on' else 'OFF'}.")
        return
    current = db.get_setting(chat_id, "welcome_text", WELCOME_DEFAULT)
    state = "ON" if db.get_setting(chat_id, "welcome_on", "1") == "1" else "OFF"
    await update.effective_message.reply_text(
        f"Welcome messages: {state}\n\n{current}", parse_mode=ParseMode.HTML)


async def reset_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    db.set_setting(update.effective_chat.id, "welcome_text", WELCOME_DEFAULT)
    await update.effective_message.reply_text("♻️ Welcome message reset to default.")


async def greet_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if db.get_setting(chat.id, "welcome_on", "1") != "1":
        return
    template = db.get_setting(chat.id, "welcome_text", WELCOME_DEFAULT)
    try:
        count = await chat.get_member_count()
    except TelegramError:
        count = 0
    for user in msg.new_chat_members:
        if user.id == context.bot.id:
            continue
        try:
            await msg.reply_text(render_welcome(template, user, chat, count),
                                 parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning("welcome failed: %s", e)


# ------------------------------------------------------------------- notes

async def save_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id
    name, text = None, None
    if msg.reply_to_message:
        r = msg.reply_to_message
        if context.args:
            name = context.args[0]
            text = r.text_html or r.caption_html
    elif len(context.args or []) >= 2:
        name = context.args[0]
        text = html.escape(" ".join(context.args[1:]))
    if not name or not text:
        await msg.reply_text(
            "Usage:\n• /save <name> <text>\n• reply to a message with /save <name>")
        return
    db.save_note(chat_id, name, text)
    await msg.reply_text(f"📝 Note <code>#{name}</code> saved.", parse_mode=ParseMode.HTML)


async def get_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: /get <name>")
        return
    text = db.get_note(update.effective_chat.id, context.args[0])
    if text:
        await msg.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await msg.reply_text("No such note.")


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    names = db.list_notes(update.effective_chat.id)
    if not names:
        await update.effective_message.reply_text("No notes in this chat.")
        return
    body = "\n".join(f"• <code>#{n}</code>" for n in names)
    await update.effective_message.reply_text(
        f"📝 <b>Notes</b> ({len(names)}):\n{body}", parse_mode=ParseMode.HTML)


async def clear_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /clear <name>")
        return
    ok = db.del_note(update.effective_chat.id, context.args[0])
    await update.effective_message.reply_text("🗑 Note deleted." if ok else "No such note.")


async def note_hashtag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    for ent in (msg.entities or []) + (msg.caption_entities or []):
        if ent.type == "hashtag":
            name = text[ent.offset + 1:ent.offset + ent.length]
            note = db.get_note(update.effective_chat.id, name)
            if note:
                try:
                    await msg.reply_text(note, parse_mode=ParseMode.HTML)
                except TelegramError:
                    pass
                return


# ----------------------------------------------------------------- filters

async def add_filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id
    trigger, text = None, None
    if msg.reply_to_message and context.args:
        trigger = context.args[0]
        r = msg.reply_to_message
        text = r.text_html or r.caption_html
    elif len(context.args or []) >= 2:
        trigger = context.args[0]
        text = html.escape(" ".join(context.args[1:]))
    if not trigger or not text:
        await msg.reply_text(
            "Usage:\n• /filter <trigger> <reply text>\n"
            "• reply to a message with /filter <trigger>")
        return
    db.save_filter(chat_id, trigger, text)
    await msg.reply_text(f"🔍 Filter <code>{trigger}</code> saved.",
                         parse_mode=ParseMode.HTML)


async def stop_filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /stop <trigger>")
        return
    ok = db.del_filter(update.effective_chat.id, context.args[0])
    await update.effective_message.reply_text(
        "🗑 Filter removed." if ok else "No such filter.")


async def stopall_filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    for trigger in db.get_filters(update.effective_chat.id):
        db.del_filter(update.effective_chat.id, trigger)
    await update.effective_message.reply_text("🗑 All filters removed.")


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    triggers = db.get_filters(update.effective_chat.id)
    if not triggers:
        await update.effective_message.reply_text("No filters in this chat.")
        return
    body = "\n".join(f"• <code>{t}</code>" for t in triggers)
    await update.effective_message.reply_text(
        f"🔍 <b>Filters</b> ({len(triggers)}):\n{body}", parse_mode=ParseMode.HTML)


async def run_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    if not text:
        return
    for trigger, reply in db.get_filters(update.effective_chat.id).items():
        if trigger.lower() in text.lower():
            try:
                await msg.reply_text(reply, parse_mode=ParseMode.HTML)
            except TelegramError:
                pass
            return


# ------------------------------------------------------------------- locks

async def lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    if not args or args[0] not in LOCK_TYPES:
        await update.effective_message.reply_text(
            "Lock types: " + ", ".join(sorted(LOCK_TYPES)) + "\nUsage: /lock <type>")
        return
    locks = db.get_locks(update.effective_chat.id)
    locks.add(args[0])
    db.set_locks(update.effective_chat.id, locks)
    await update.effective_message.reply_text(f"🔒 Locked <code>{args[0]}</code>.",
                                             parse_mode=ParseMode.HTML)


async def unlock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    if not args or args[0] not in LOCK_TYPES:
        await update.effective_message.reply_text(
            "Lock types: " + ", ".join(sorted(LOCK_TYPES)) + "\nUsage: /unlock <type>")
        return
    locks = db.get_locks(update.effective_chat.id)
    locks.discard(args[0])
    db.set_locks(update.effective_chat.id, locks)
    await update.effective_message.reply_text(f"🔓 Unlocked <code>{args[0]}</code>.",
                                             parse_mode=ParseMode.HTML)


async def locks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    locks = db.get_locks(update.effective_chat.id)
    if not locks:
        await update.effective_message.reply_text("Nothing is locked. Use /lock <type>.")
        return
    body = ", ".join(f"<code>{l}</code>" for l in sorted(locks))
    await update.effective_message.reply_text(f"🔒 <b>Locked</b>: {body}",
                                             parse_mode=ParseMode.HTML)


async def enforce_locks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs in group -1 (before everything else). Admins are exempt."""
    msg = update.effective_message
    if msg is None or update.effective_chat.type == "private":
        return
    if await user_is_admin(update, context):
        return
    locks = db.get_locks(update.effective_chat.id)
    if not locks:
        return
    for lock in locks:
        check = LOCK_TYPES.get(lock)
        if check and check(msg, update):
            try:
                await msg.delete()
            except TelegramError:
                pass
            raise ApplicationHandlerStop


# --------------------------------------------------------------- antiflood

async def antiflood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if not args:
        cur = db.get_setting(chat_id, "antiflood", "0")
        await update.effective_message.reply_text(
            f"Antiflood limit: {cur} (0 = off)\nUsage: /antiflood <limit>")
        return
    if args[0].lower() in ("off", "0"):
        db.set_setting(chat_id, "antiflood", "0")
        await update.effective_message.reply_text("🌊 Antiflood disabled.")
        return
    if not args[0].isdigit():
        await update.effective_message.reply_text("Usage: /antiflood <limit>")
        return
    db.set_setting(chat_id, "antiflood", args[0])
    await update.effective_message.reply_text(
        f"🌊 Antiflood enabled - more than {args[0]} messages in "
        f"{FLOOD_WINDOW}s gets the flooder banned.")


async def check_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None or update.effective_chat.type == "private":
        return
    limit = db.get_setting(update.effective_chat.id, "antiflood", "0")
    if not limit or limit == "0":
        return
    limit = int(limit)
    user, chat = update.effective_user, update.effective_chat
    if not user or await user_is_admin(update, context):
        return
    now = time.monotonic()
    stamps = _flood[chat.id][user.id]
    while stamps and now - stamps[0] > FLOOD_WINDOW:
        stamps.popleft()
    stamps.append(now)
    if len(stamps) > limit:
        _flood[chat.id][user.id].clear()
        try:
            await chat.ban_member(user.id)
            await chat.unban_member(user.id, only_if_banned=True)
            await msg.reply_text(
                f"🌊 {user.mention_html()} was flooding - removed.",
                parse_mode=ParseMode.HTML)
        except TelegramError:
            pass


# --------------------------------------------------------------------- afk

async def afk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    reason = " ".join(context.args or []) or "no reason given"
    db.set_afk(user.id, reason)
    await update.effective_message.reply_text(
        f"💤 {user.first_name} is now AFK: {reason}")


async def afk_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not user:
        return

    # A returning user's activity clears their AFK.
    if db.get_afk(user.id):
        db.clear_afk(user.id)
        await msg.reply_text("👋 Welcome back! Your AFK was cleared.")
        return

    # Replying to an AFK user tells the sender.
    reply = msg.reply_to_message
    if reply and reply.from_user:
        info = db.get_afk(reply.from_user.id)
        if info:
            await msg.reply_text(
                f"💤 <b>{reply.from_user.first_name}</b> is AFK: "
                f"{html.escape(info[0] or 'no reason')}",
                parse_mode=ParseMode.HTML)


# -------------------------------------------------------------------- info

async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    text = f"🆔 Chat: <code>{chat.id}</code>\nYou: <code>{user.id}</code>"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        text += f"\nReply target: <code>{msg.reply_to_message.from_user.id}</code>"
    await msg.reply_text(text, parse_mode=ParseMode.HTML)


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target = msg.reply_to_message.from_user
    else:
        target = update.effective_user
    lines = [
        f"👤 <b>{html.escape(target.first_name or '?')}</b>",
        f"ID: <code>{target.id}</code>",
        f"Username: @{target.username}" if target.username else None,
        f"{'🤖 Bot' if target.is_bot else '👤 Human'}",
    ]
    warns_n, _ = db.get_warns(update.effective_chat.id, target.id)
    lines.append(f"Warns here: {warns_n}")
    await msg.reply_text("\n".join(l for l in lines if l), parse_mode=ParseMode.HTML)


async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        admins = await update.effective_chat.get_administrators()
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't list admins: {e}")
        return
    lines = ["👑 <b>Admins</b>"]
    for m in sorted(admins, key=lambda x: (x.status != "creator", x.user.first_name or "")):
        name = m.user.first_name or "?"
        tag = " (creator)" if m.status == "creator" else ""
        lines.append(f"• {m.user.mention_html()}{tag}" if m.user.username
                     else f"• {html.escape(name)}{tag}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    t0 = time.perf_counter()
    await update.effective_message.reply_text("🏓")
    log.info("ping handled in %.0f ms", (time.perf_counter() - t0) * 1000)
