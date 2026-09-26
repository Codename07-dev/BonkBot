"""Moderation: bans, mutes, warns, purges, pins - the Rose-style core."""

import asyncio
import logging
import re
import time

from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

import db

log = logging.getLogger("unkilbonker.mod")

DUR_RE = re.compile(r"^(\d+)([smhdw])$")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

MUTED = ChatPermissions.no_permissions()
UNMUTED = ChatPermissions.all_permissions()

# ----------------------------------------------------------------- helpers

def parse_duration(text: str):
    """'30s', '5m', '2h', '1d', '1w' -> seconds, or None."""
    m = DUR_RE.match(text.strip().lower())
    return int(m.group(1)) * UNIT_SECONDS[m.group(2)] if m else None


def fmt_duration(seconds: int) -> str:
    for label, unit in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % unit == 0 and seconds >= unit:
            return f"{seconds // unit}{label}"
    return f"{seconds}s"


def sudo_ids(context) -> set:
    raw = context.bot_data.get("sudo_ids", "")
    return {int(x) for x in re.split(r"[,\s]+", raw) if x.strip().isdigit()}


async def is_admin(context, chat_id: int, user_id: int) -> bool:
    """Admin membership check with a short-lived cache (avoids API spam)."""
    if user_id in sudo_ids(context) or user_id == context.bot_data.get("owner_id"):
        return True
    key = f"admins:{chat_id}"
    now = time.time()
    hit = context.bot_data.get(key)
    if hit and now < hit[0] and user_id in hit[1]:
        return True
    try:
        admins = {m.user.id
                  for m in await context.bot.get_chat_administrators(chat_id)}
    except TelegramError:
        return False
    context.bot_data[key] = (now + 180, admins)  # 3-minute cache
    return user_id in admins


async def user_is_admin(update: Update, context) -> bool:
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat:
        return False
    return await is_admin(context, chat.id, user.id)


async def get_target_user(update: Update) -> tuple | None:
    """(user_id, display_name) from reply, numeric id, or text mention."""
    msg = update.effective_message
    reply = msg.reply_to_message
    if reply and reply.from_user:
        u = reply.from_user
        return u.id, u.first_name
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "text_mention" and ent.user:
                return ent.user.id, ent.user.first_name
    for arg in (msg.text or "").split()[1:]:
        if arg.lstrip("-").isdigit():
            return int(arg), arg
    return None


async def check_bot_can_restrict(update: Update, context) -> bool:
    me = await update.effective_chat.get_member(context.bot.id)
    if not (me.can_restrict_members or me.status == "creator"):
        await update.effective_message.reply_text(
            "I need to be an admin with <b>Restrict/Ban users</b> rights "
            "to do that.", parse_mode=ParseMode.HTML)
        return False
    return True


async def protect_target(update: Update, context, target_id) -> bool:
    """Return True if the target may be actioned (not admin/creator/sudo)."""
    if target_id == context.bot.id:
        return False
    if target_id in sudo_ids(context) or target_id == context.bot_data.get("owner_id"):
        await update.effective_message.reply_text("I won't touch my owner.")
        return False
    if await is_admin(context, update.effective_chat.id, target_id):
        await update.effective_message.reply_text(
            "That user is an admin - I can't do that.")
        return False
    return True


def parse_default_emojis(args: list) -> tuple:
    """Split leading emoji args from the rest; returns (emojis, reason)."""
    emojis = []
    rest = []
    for a in args:
        if not emojis and not any(c.isascii() for c in a) and len(a) <= 8:
            emojis.append(a)
        else:
            rest.append(a)
    return emojis, " ".join(rest).strip()


# ----------------------------------------------------------------- commands

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    if not await check_bot_can_restrict(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID: /ban <id> [reason]")
        return
    tid, name = target
    if not await protect_target(update, context, tid):
        return
    emojis, reason = parse_default_emojis(context.args or [])
    try:
        await update.effective_chat.ban_member(tid)
        await msg.reply_text(
            f"🚫 <b>Banned</b> {name}." + (f"\nReason: {reason}" if reason else ""),
            parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't ban: {e}")


async def tban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    if not await check_bot_can_restrict(update, context):
        return
    args = context.args or []
    if not args or not parse_duration(args[0]):
        await msg.reply_text("Usage: /tban <time> [reply/id] - e.g. /tban 30m")
        return
    seconds = parse_duration(args.pop(0))
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID first.")
        return
    tid, name = target
    if not await protect_target(update, context, tid):
        return
    try:
        await update.effective_chat.ban_member(tid)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't ban: {e}")
        return
    if context.job_queue:
        context.job_queue.run_once(
            unban_job, seconds, data={"chat_id": update.effective_chat.id,
                                      "user_id": tid, "name": name})
    await msg.reply_text(
        f"⏳ <b>Banned</b> {name} for <b>{fmt_duration(seconds)}</b>.",
        parse_mode=ParseMode.HTML)


async def unban_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.job.data
    try:
        await context.bot.unban_member(d["chat_id"], d["user_id"],
                                        only_if_banned=True)
    except TelegramError as e:
        log.warning("unban_job failed: %s", e)


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to the user or give their ID.")
        return
    try:
        await update.effective_chat.unban_member(target[0], only_if_banned=True)
        await msg.reply_text(f"✅ Unbanned {target[1]}.")
    except TelegramError as e:
        await msg.reply_text(f"Couldn't unban: {e}")


async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    if not await check_bot_can_restrict(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID.")
        return
    tid, name = target
    if not await protect_target(update, context, tid):
        return
    try:
        await update.effective_chat.ban_member(tid)
        await update.effective_chat.unban_member(tid, only_if_banned=True)
        await msg.reply_text(f"👢 <b>Kicked</b> {name}.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't kick: {e}")


async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    if not await check_bot_can_restrict(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID.")
        return
    tid, name = target
    if not await protect_target(update, context, tid):
        return
    try:
        await update.effective_chat.restrict_member(tid, MUTED)
        await msg.reply_text(f"🔇 <b>Muted</b> {name}.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't mute: {e}")


async def tmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    if not await check_bot_can_restrict(update, context):
        return
    args = context.args or []
    if not args or not parse_duration(args[0]):
        await msg.reply_text("Usage: /tmute <time> - e.g. /tmute 10m")
        return
    seconds = parse_duration(args.pop(0))
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID first.")
        return
    tid, name = target
    if not await protect_target(update, context, tid):
        return
    try:
        await update.effective_chat.restrict_member(tid, MUTED)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't mute: {e}")
        return
    if context.job_queue:
        context.job_queue.run_once(
            unmute_job, seconds, data={"chat_id": update.effective_chat.id,
                                       "user_id": tid})
    await msg.reply_text(
        f"🔇 <b>Muted</b> {name} for <b>{fmt_duration(seconds)}</b>.",
        parse_mode=ParseMode.HTML)


async def unmute_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.job.data
    try:
        await context.bot.restrict_chat_member(d["chat_id"], d["user_id"],
                                               UNMUTED)
    except TelegramError as e:
        log.warning("unmute_job failed: %s", e)


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID.")
        return
    try:
        await update.effective_chat.restrict_member(target[0], UNMUTED)
        await msg.reply_text(f"🔊 Unmuted {target[1]}.")
    except TelegramError as e:
        await msg.reply_text(f"Couldn't unmute: {e}")


async def kickme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if user.id in sudo_ids(context):
        return
    if await is_admin(context, chat.id, user.id):
        await update.effective_message.reply_text("Admins can't use /kickme.")
        return
    try:
        await chat.ban_member(user.id)
        await chat.unban_member(user.id, only_if_banned=True)
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't: {e}")


# ------------------------------------------------------------------ warns

async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to someone or give their ID.")
        return
    tid, name = target
    if not await protect_target(update, context, tid):
        return
    emojis, reason = parse_default_emojis(context.args or [])
    chat_id = update.effective_chat.id
    count = db.add_warn(chat_id, tid, reason)
    limit = int(db.get_setting(chat_id, "warn_limit", 3))
    if count >= limit:
        db.reset_warns(chat_id, tid)
        try:
            await update.effective_chat.ban_member(tid)
            await msg.reply_text(
                f"⚠️ {name} hit {limit} warns - <b>banned</b>.",
                parse_mode=ParseMode.HTML)
        except TelegramError as e:
            await msg.reply_text(f"Warn limit reached but ban failed: {e}")
    else:
        await msg.reply_text(
            f"⚠️ {name} has <b>{count}/{limit}</b> warns."
            + (f"\nReason: {reason}" if reason else ""),
            parse_mode=ParseMode.HTML)


async def warnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = await get_target_user(update) or (update.effective_user.id,
                                               update.effective_user.first_name)
    count, reasons = db.get_warns(update.effective_chat.id, target[0])
    text = f"⚠️ {target[1]}: {count} warn(s)."
    if reasons:
        text += "\n" + "\n".join(f"• {r}" for r in reasons[-5:])
    await msg.reply_text(text)


async def resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text("Reply to someone or give their ID.")
        return
    db.reset_warns(update.effective_chat.id, target[0])
    await update.effective_message.reply_text(f"♻️ Cleared warns for {target[1]}.")


async def warnlimit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(
            f"Current warn limit: {db.get_setting(update.effective_chat.id, 'warn_limit', 3)}\n"
            "Change it: /warnlimit 5")
        return
    db.set_setting(update.effective_chat.id, "warn_limit", int(args[0]))
    await update.effective_message.reply_text(f"✅ Warn limit set to {args[0]}.")


# ------------------------------------------------------- purge / delete / pin

async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    if not msg.reply_to_message:
        await msg.reply_text("Reply to the message where the purge should start.")
        return
    start = msg.reply_to_message.message_id
    end = msg.message_id
    ids = list(range(start, end + 1))
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await context.bot.delete_messages(update.effective_chat.id, chunk)
            deleted += len(chunk)
        except BadRequest:
            pass  # some ids too old (>48h) - skip
    await msg.reply_text(f"🧹 Purged {deleted} messages.")


async def del_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text("Reply to the message to delete.")
        return
    try:
        await reply.delete()
        await msg.delete()
    except TelegramError:
        pass


async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await user_is_admin(update, context):
        return
    reply = msg.reply_to_message or msg
    silent = "loud" not in (context.args or [])
    try:
        await reply.pin(disable_notification=silent)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't pin: {e}")


async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    try:
        await update.effective_message.unpin()
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't unpin: {e}")
