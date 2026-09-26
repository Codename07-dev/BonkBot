"""Group features: welcome, notes, filters, locks, antiflood, afk, info."""

import html
import json
import logging
import re
import random
import time
import unicodedata
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

def _urls_in(m):
    out = []
    for e in (m.entities or []) + (m.caption_entities or []):
        if e.type == "url":
            out.append((m.text or m.caption or "")[e.offset:e.offset + e.length])
        elif e.type == "text_link" and e.url:
            out.append(e.url)
    return out


def _has_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(c) in ("R", "AL")
               for c in (text or "")[:512])


INVITE_RE = re.compile(r"t\.me/(?:\+|joinchat/)", re.I)


LOCK_TYPES = {
    "msgs": lambda m, u: True,
    "text": lambda m, u: bool((m.text or m.caption or "").strip())
        and not (m.sticker or m.photo or m.video or m.document),
    "stickers": lambda m, u: bool(m.sticker),
    "gifs": lambda m, u: bool(m.animation),
    "photos": lambda m, u: bool(m.photo),
    "videos": lambda m, u: bool(m.video),
    "album": lambda m, u: bool(m.media_group_id),
    "audio": lambda m, u: bool(m.audio),
    "voice": lambda m, u: bool(m.voice),
    "video_notes": lambda m, u: bool(m.video_note),
    "documents": lambda m, u: bool(m.document),
    "polls": lambda m, u: bool(m.poll),
    "games": lambda m, u: bool(m.game),
    "bots": lambda m, u: bool(m.from_user and m.from_user.is_bot),
    "buttons": lambda m, u: bool(m.reply_markup),
    "commands": lambda m, u: any(e.type == "bot_command"
                                 for e in (m.entities or []) + (m.caption_entities or [])),
    "emails": lambda m, u: any(e.type == "email"
                               for e in (m.entities or []) + (m.caption_entities or [])),
    "phones": lambda m, u: any(e.type == "phone_number"
                               for e in (m.entities or []) + (m.caption_entities or [])),
    "inline": lambda m, u: bool(m.via_bot),
    "location": lambda m, u: bool(m.location or m.venue),
    "invitelinks": lambda m, u: any(INVITE_RE.search(url or "")
                                    for url in _urls_in(m)),
    "rtl": lambda m, u: _has_rtl(m.text or m.caption or ""),
    "urls": lambda m, u: any(not db.is_url_allowed(m.chat.id, url)
                             for url in _urls_in(m)),
    "forwards": lambda m, u: bool(getattr(m, "forward_origin", None)),
    "captions": lambda m, u: bool(m.caption),
    "edits": lambda m, u: bool(m.edit_date),
}

# backwards-compatible names
LOCK_TYPES["invite"] = LOCK_TYPES["invitelinks"]
LOCK_TYPES["bot"] = LOCK_TYPES["bots"]
LOCK_TYPES["command"] = LOCK_TYPES["commands"]
LOCK_TYPES["email"] = LOCK_TYPES["emails"]
LOCK_TYPES["phone"] = LOCK_TYPES["phones"]
LOCK_TYPES["videonote"] = LOCK_TYPES["video_notes"]


# ----------------------------------------------------------------- welcome

WELCOME_DEFAULT = (
    "Hey {mention}, welcome to <b>{chatname}</b>! 🎉\n"
    "You're member #{count} - read the pinned message and enjoy your stay."
)


def render_welcome(template: str, user, chat, count: int) -> str:
    mapping = {
        "{first}": html.escape(user.first_name or ""),
        "{firstname}": html.escape(user.first_name or ""),
        "{last}": html.escape(user.last_name or ""),
        "{lastname}": html.escape(user.last_name or ""),
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
    clean = db.get_setting(chat.id, "cleanwelcome", "0") == "1"
    store = context.bot_data.setdefault("welcome_msgs", {})
    for user in msg.new_chat_members:
        if user.id == context.bot.id:
            continue
        try:
            sent = await msg.reply_text(render_welcome(template, user, chat, count),
                                        parse_mode=ParseMode.HTML)
            if clean and chat.id in store:
                try:
                    await context.bot.delete_message(chat.id, store[chat.id])
                except TelegramError:
                    pass
            store[chat.id] = sent.message_id
            # for /clearcleft: remember which welcome belongs to whom
            if db.get_setting(chat.id, "clearcleft", "0") == "1":
                context.bot_data.setdefault("welcome_per_user", {})[
                    (chat.id, user.id)] = sent.message_id
        except TelegramError as e:
            log.warning("welcome failed: %s", e)


# ------------------------------------------------------------------- notes

async def save_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id
    name, payload = None, None
    if msg.reply_to_message:
        r = msg.reply_to_message
        if context.args:
            name = context.args[0]
            payload = json.dumps(_note_payload(r))
    elif len(context.args or []) >= 2:
        name = context.args[0]
        payload = json.dumps({"t": "text",
                              "c": html.escape(" ".join(context.args[1:]))})
    if not name or not payload or payload == "null":
        await msg.reply_text(
            "Usage:\n• /save <name> <text>\n• reply to any message (text or "
            "media) with /save <name>")
        return
    db.save_note(chat_id, name, payload)
    await msg.reply_text(f"📝 Note <code>#{name}</code> saved.", parse_mode=ParseMode.HTML)


async def get_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: /get <name>")
        return
    text = db.get_note(update.effective_chat.id, context.args[0])
    if text:
        await send_note(update, context, text)
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


async def clearallnotes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    # require the chat creator, per the Rose spec
    try:
        me = await update.effective_chat.get_member(update.effective_user.id)
        if me.status != "creator":
            await update.effective_message.reply_text(
                "Only the group creator can wipe all notes.")
            return
    except TelegramError:
        pass
    n = db.clear_all_notes(update.effective_chat.id)
    await update.effective_message.reply_text(f"🗑 Deleted {n} note(s).")


async def note_hashtag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    for ent in (msg.entities or []) + (msg.caption_entities or []):
        if ent.type == "hashtag":
            name = text[ent.offset + 1:ent.offset + ent.length]
            note = db.get_note(update.effective_chat.id, name)
            if note:
                try:
                    await send_note(update, context, note)
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


async def locktypes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    body = ", ".join(sorted(k for k in LOCK_TYPES if k not in
                            ("invite", "bot", "command", "email", "phone",
                             "videonote")))
    await update.effective_message.reply_text(
        f"🔒 <b>Lock types</b>:\n{body}", parse_mode=ParseMode.HTML)


async def allowlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if not args:
        current = db.list_allowlist(chat_id)
        body = ", ".join(f"<code>{d}</code>" for d in current) or "(empty)"
        await update.effective_message.reply_text(
            f"🔗 <b>URL allowlist</b>: {body}\n"
            "Usage: /allowlist example.com",
            parse_mode=ParseMode.HTML)
        return
    db.add_allowlist(chat_id, args[0])
    await update.effective_message.reply_text(
        f"✅ <code>{args[0]}</code> links are now allowed even with URL locks on.",
        parse_mode=ParseMode.HTML)


async def rmallowlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /rmallowlist example.com")
        return
    if db.del_allowlist(update.effective_chat.id, args[0]):
        await update.effective_message.reply_text("🗑 Removed from allowlist.")
    else:
        await update.effective_message.reply_text("That domain isn't allowlisted.")


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


async def setfloodmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if not args or args[0].lower() not in ("ban", "mute", "kick"):
        cur = db.get_setting(chat_id, "flood_mode", "kick")
        await update.effective_message.reply_text(
            f"Current flood mode: <b>{cur}</b>\n"
            "Usage: /setfloodmode <ban/mute/kick>", parse_mode=ParseMode.HTML)
        return
    db.set_setting(chat_id, "flood_mode", args[0].lower())
    await update.effective_message.reply_text(
        f"✅ Flood mode set to <b>{args[0].lower()}</b>.",
        parse_mode=ParseMode.HTML)


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
        mode = db.get_setting(chat.id, "flood_mode", "kick")
        try:
            if mode == "ban":
                await chat.ban_member(user.id)
                action = "banned"
            elif mode == "mute":
                from moderation import MUTED
                await chat.restrict_member(user.id, MUTED)
                action = "muted"
            else:  # kick
                await chat.ban_member(user.id)
                await chat.unban_member(user.id, only_if_banned=True)
                action = "kicked"
            await msg.reply_text(
                f"🌊 {user.mention_html()} was flooding - {action}.",
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


# ---------------------------------------------------------------- blocklist

async def add_blocklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    words = " ".join(context.args or []).split(",") if context.args else []
    words = [w.strip() for w in words if w.strip()]
    if not words:
        await update.effective_message.reply_text(
            "Usage: /addblocklist word1, word2")
        return
    for w in words:
        db.add_blocklist(update.effective_chat.id, w)
    await update.effective_message.reply_text(
        f"🚷 Added {len(words)} word(s) to the blocklist. "
        f"Mode: /blocklistmode")


async def rmblocklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    words = " ".join(context.args or []).split(",") if context.args else []
    removed = sum(1 for w in words if db.del_blocklist(update.effective_chat.id, w.strip()))
    await update.effective_message.reply_text(
        f"🗑 Removed {removed} word(s)." if removed else "None of those were blocked.")


async def blocklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    words = db.list_blocklist(update.effective_chat.id)
    if not words:
        await update.effective_message.reply_text(
            "No blocked words. Add some: /addblocklist spam")
        return
    mode = db.get_setting(update.effective_chat.id, "blocklist_mode", "warn")
    body = ", ".join(f"<code>{html.escape(w)}</code>" for w in words[:60])
    await update.effective_message.reply_text(
        f"🚷 <b>Blocklist</b> (mode: {mode}):\n{body}",
        parse_mode=ParseMode.HTML)


async def blocklistmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if not args or args[0].lower() not in ("ban", "mute", "kick", "warn", "delete"):
        cur = db.get_setting(chat_id, "blocklist_mode", "warn")
        await update.effective_message.reply_text(
            f"Current blocklist mode: <b>{cur}</b>\n"
            "Usage: /blocklistmode <ban/mute/kick/warn/delete>",
            parse_mode=ParseMode.HTML)
        return
    db.set_setting(chat_id, "blocklist_mode", args[0].lower())
    await update.effective_message.reply_text(
        f"✅ Blocklist mode set to <b>{args[0].lower()}</b>.",
        parse_mode=ParseMode.HTML)


async def blocklistreason_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    reason = " ".join(context.args or []).strip()
    if not reason:
        cur = db.get_setting(update.effective_chat.id, "blocklist_reason",
                              "Using blocked words")
        await update.effective_message.reply_text(
            f"Current blocklist reason: {cur}\n"
            "Usage: /blocklistreason <text>")
        return
    db.set_setting(update.effective_chat.id, "blocklist_reason", reason)
    await update.effective_message.reply_text("✅ Blocklist reason set.")


async def enforce_blocklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs in group -1 after lock enforcement. Admins are exempt."""
    msg = update.effective_message
    if msg is None or update.effective_chat.type == "private":
        return
    user = update.effective_user
    if not user or await user_is_admin(update, context):
        return
    text = (msg.text or msg.caption or "").lower()
    if not text:
        return
    words = db.list_blocklist(update.effective_chat.id)
    if not words:
        return
    hit = next((w for w in words if w in text), None)
    if not hit:
        return

    from moderation import MUTED
    chat = update.effective_chat
    mode = db.get_setting(chat.id, "blocklist_mode", "warn")
    reason = db.get_setting(chat.id, "blocklist_reason", "Using blocked words")
    try:
        await msg.delete()
    except TelegramError:
        pass
    try:
        if mode == "ban":
            await chat.ban_member(user.id)
        elif mode == "mute":
            await chat.restrict_member(user.id, MUTED)
        elif mode == "kick":
            await chat.ban_member(user.id)
            await chat.unban_member(user.id, only_if_banned=True)
        elif mode == "warn":
            from moderation import warn as warn_cmd
            # reuse the warn machinery via a lightweight direct increment
            import moderation
            count = db.add_warn(chat.id, user.id, f"blocklist: {hit}")
            limit = int(db.get_setting(chat.id, "warn_limit", 3))
            await msg.reply_text(
                f"🚷 {user.mention_html()} - {html.escape(reason)} "
                f"(word: <code>{html.escape(hit)}</code>). "
                f"Warn {count}/{limit}.",
                parse_mode=ParseMode.HTML)
            if count >= limit:
                db.reset_warns(chat.id, user.id)
                await moderation.apply_warn_mode(
                    update, context, user.id, user.first_name or "?", limit)
            return
        action = {"ban": "banned", "mute": "muted", "kick": "kicked",
                 "delete": "message deleted"}.get(mode, mode)
        await msg.reply_text(
            f"🚷 {user.mention_html()} - {html.escape(reason)} - {action}.",
            parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


# ------------------------------------------------------ cleanwelcome & co

async def cleanwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if args and args[0].lower() in ("on", "off"):
        db.set_setting(chat_id, "cleanwelcome",
                       "1" if args[0] == "on" else "0")
        await update.effective_message.reply_text(
            f"🧹 Cleanwelcome is now {args[0].upper()} "
            "(old welcome messages get deleted).")
    else:
        cur = db.get_setting(chat_id, "cleanwelcome", "0")
        await update.effective_message.reply_text(
            f"🧹 Cleanwelcome: {'ON' if cur == '1' else 'OFF'}. "
            "Usage: /cleanwelcome on|off")


async def clearcleft_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if args and args[0].lower() in ("on", "off"):
        db.set_setting(chat_id, "clearcleft",
                       "1" if args[0] == "on" else "0")
        await update.effective_message.reply_text(
            f"👋 Clear-on-leave is now {args[0].upper()} "
            "(a user's welcome is deleted when they leave).")
    else:
        cur = db.get_setting(chat_id, "clearcleft", "0")
        await update.effective_message.reply_text(
            f"👋 Clear-on-leave: {'ON' if cur == '1' else 'OFF'}. "
            "Usage: /clearcleft on|off")


# ----------------------------------------------------------------- goodbye

GOODBYE_DEFAULT = "👋 See you around, {mention}!"


async def set_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    msg = update.effective_message
    template = None
    if msg.reply_to_message:
        template = msg.reply_to_message.text_html or \
            msg.reply_to_message.caption_html
    elif context.args:
        template = html.escape(" ".join(context.args))
    if not template:
        await msg.reply_text("Usage: /setgoodbye <text>\n"
                            "Placeholders: {first} {mention} {username} {chatname}")
        return
    db.set_setting(update.effective_chat.id, "goodbye_text", template)
    await msg.reply_text("✅ Goodbye message set.")


async def goodbye_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    chat_id = update.effective_chat.id
    args = context.args or []
    if args and args[0].lower() in ("on", "off"):
        db.set_setting(chat_id, "goodbye_on", "1" if args[0] == "on" else "0")
        await update.effective_message.reply_text(
            f"Goodbyes are now {args[0].upper()}.")
        return
    current = db.get_setting(chat_id, "goodbye_text", GOODBYE_DEFAULT)
    state = "ON" if db.get_setting(chat_id, "goodbye_on", "0") == "1" else "OFF"
    await update.effective_message.reply_text(
        f"Goodbye messages: {state}\n\n{current}", parse_mode=ParseMode.HTML)


async def reset_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    db.set_setting(update.effective_chat.id, "goodbye_text", GOODBYE_DEFAULT)
    await update.effective_message.reply_text("♻️ Goodbye message reset to default.")


async def on_left_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    # /clearcleft: delete the leaver's welcome message
    if db.get_setting(chat.id, "clearcleft", "0") == "1":
        store = context.bot_data.get("welcome_per_user", {})
        wid = store.get((chat.id, msg.left_chat_member.id))
        if wid:
            try:
                await context.bot.delete_message(chat.id, wid)
            except TelegramError:
                pass
            store.pop((chat.id, msg.left_chat_member.id), None)
    if db.get_setting(chat.id, "goodbye_on", "0") != "1":
        return
    template = db.get_setting(chat.id, "goodbye_text", GOODBYE_DEFAULT)
    user = msg.left_chat_member
    try:
        await msg.reply_text(
            render_welcome(template, user, chat, 0),
            parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.warning("goodbye failed: %s", e)


# --------------------------------------------------------- media notes

MEDIA_KINDS = ("photo", "sticker", "animation", "video", "document",
               "audio", "voice", "video_note")


def _note_payload(reply) -> dict | None:
    """Build a note payload from a replied-to message."""
    for kind in MEDIA_KINDS:
        obj = getattr(reply, kind, None)
        if obj:
            media = obj[-1] if kind == "photo" else obj
            return {"t": "media", "k": kind, "f": media.file_id,
                    "c": reply.caption or ""}
    text = reply.text_html or reply.caption_html
    return {"t": "text", "c": text} if text else None


async def send_note(update: Update, context, payload: str) -> None:
    """Deliver a stored note (text or media)."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        data = {"t": "text", "c": payload}  # legacy plain-text note
    msg = update.effective_message
    if data.get("t") == "media":
        kind, file_id, caption = data["k"], data["f"], data.get("c") or None
        send = getattr(context.bot, f"send_{kind}")
        await send(update.effective_chat.id, file_id, caption=caption)
    else:
        await msg.reply_text(data.get("c", ""),
                             parse_mode=ParseMode.HTML)


# ------------------------------------------------------------- ads remover

TME_RE = re.compile(r"t\.me/(?!c/)([A-Za-z0-9_]+)", re.I)
INVITE_RE2 = re.compile(r"t\.me/(?:\+|joinchat/)", re.I)


def _is_ad(msg, chat) -> bool:
    """Invite links, links to OTHER chats/channels, or cross-chat forwards."""
    text = (msg.text or msg.caption or "")
    if INVITE_RE2.search(text):
        return True
    for m in TME_RE.finditer(text):
        target = m.group(1).lower()
        own = (chat.username or "").lower()
        if target != own and target not in ("addstickers", "addlist",
                                            "share", "setlanguage"):
            return True
    fwd = getattr(msg, "forward_origin", None)
    if fwd is not None:
        fwd_chat = getattr(fwd, "chat", None) or getattr(fwd, "sender_chat", None)
        if fwd_chat is not None and fwd_chat.id != chat.id:
            return True
    return False


async def adsremover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if args and args[0].lower() in ("on", "off", "yes", "no"):
        on = "1" if args[0].lower() in ("on", "yes") else "0"
        db.set_setting(chat_id, "adsremover", on)
        await update.effective_message.reply_text(
            ("🧹 Ads Remover is ON - invite links, promos to other channels "
             "and cross-chat forwards from non-admins get deleted.")
            if on == "1" else "🧹 Ads Remover is OFF.")
        return
    cur = db.get_setting(chat_id, "adsremover", "0")
    await update.effective_message.reply_text(
        f"🧹 Ads Remover: {'ON' if cur == '1' else 'OFF'}\n"
        "Usage: /adsremover on|off")


async def enforce_ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kafka-style ad deletion. Runs after lock enforcement (group -1)."""
    msg = update.effective_message
    if msg is None or update.effective_chat.type == "private":
        return
    if db.get_setting(update.effective_chat.id, "adsremover", "0") != "1":
        return
    user = update.effective_user
    if not user or await user_is_admin(update, context):
        return
    if _is_ad(msg, update.effective_chat):
        try:
            await msg.delete()
        except TelegramError:
            pass
        raise ApplicationHandlerStop
