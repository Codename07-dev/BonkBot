"""Federations (Rose-style feds) + owner-level global bans.

Fed concept: you create a federation, your groups join it, and any /fban
bans the user in EVERY group of the federation at once.
"""

import html
import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import db
from moderation import admin_gate, get_target_user, parse_default_emojis, sudo_ids, user_is_admin

log = logging.getLogger("unkilbonker.feds")

# throttle repeated fed-ban announcements per (chat, user)
_last_announced: dict = {}


def is_owner(context, user_id: int) -> bool:
    return user_id == context.bot_data.get("owner_id") or user_id in sudo_ids(context)


# ----------------------------------------------------------------- create

async def newfed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    name = " ".join(context.args or []).strip()
    if not name:
        await update.effective_message.reply_text(
            "Usage: /newfed <name>\nThen add groups with /joinfed <fed_id>.")
        return
    fed_id = db.create_fed(user.id, name)
    await update.effective_message.reply_text(
        f"✅ Federation <b>{html.escape(name)}</b> created!\n\n"
        f"🆔 Fed ID: <code>{fed_id}</code>\n\n"
        "Now go to each group you want in this fed (as admin) and run:\n"
        f"<code>/joinfed {fed_id}</code>\n\n"
        "Fed owners can use /fban anywhere in the fed.",
        parse_mode=ParseMode.HTML)


async def delfed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    args = context.args or []
    if fed is None and args:
        fed = db.get_fed(args[0])
    if fed is None:
        await update.effective_message.reply_text(
            "Use this in a group that's in the fed, or: /delfed <fed_id>")
        return
    if fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text("Only the fed owner can delete it.")
        return
    db.delete_fed(fed["fed_id"])
    await update.effective_message.reply_text(
        f"🗑 Federation <b>{html.escape(fed['name'])}</b> deleted.",
        parse_mode=ParseMode.HTML)


async def fedinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    args = context.args or []
    if fed is None and args:
        fed = db.get_fed(args[0])
    if fed is None:
        await update.effective_message.reply_text(
            "Usage (in a fed group): /fedinfo\nOr: /fedinfo <fed_id>")
        return
    chats = db.fed_chats_list(fed["fed_id"])
    fbans = db.list_fbans(fed["fed_id"])
    await update.effective_message.reply_text(
        f"🌐 <b>{html.escape(fed['name'])}</b>\n"
        f"ID: <code>{fed['fed_id']}</code>\n"
        f"Groups: {len(chats)}\n"
        f"Fed-bans: {len(fbans)}\n"
        f"Admins: /fedadmins",
        parse_mode=ParseMode.HTML)


# ----------------------------------------------------------------- join

async def joinfed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text("Run this in the group, not here.")
        return
    args = context.args or []
    if not args:
        # No ID given: join YOUR OWN fed (you created it with /newfed)
        owned = [f for f, n, role in db.my_feds(update.effective_user.id)
                 if role == "owner"]
        if not owned:
            await update.effective_message.reply_text(
                "You don't own a fed yet. Create one first: /newfed <name>")
            return
        if len(owned) > 1:
            await update.effective_message.reply_text(
                "You own several feds - give the ID: /joinfed <fed_id>")
            return
        fed = db.get_fed(owned[0])
    else:
        fed = db.get_fed(args[0])
    if not fed:
        await update.effective_message.reply_text("No fed with that ID.")
        return
    chat = update.effective_chat
    current = db.chat_fed(chat.id)
    if current:
        await update.effective_message.reply_text(
            f"This group is already in <b>{html.escape(current['name'])}</b>. "
            "Use /leavefed first.",
            parse_mode=ParseMode.HTML)
        return
    db.join_fed(fed["fed_id"], chat.id, chat.title or "?")
    await update.effective_message.reply_text(
        f"🌐 Joined federation <b>{html.escape(fed['name'])}</b>! "
        "Fed bans now apply here.",
        parse_mode=ParseMode.HTML)


async def leavefed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    if not db.chat_fed(update.effective_chat.id):
        await update.effective_message.reply_text("This group isn't in a fed.")
        return
    db.leave_fed(update.effective_chat.id)
    await update.effective_message.reply_text("👋 Left the federation.")


# ------------------------------------------------------------- fed admins

async def _fed_admin_required(update, context, allow_chat_admin=False):
    """Resolve the current fed; return it if the caller may manage it."""
    fed = db.chat_fed(update.effective_chat.id)
    if not fed:
        await update.effective_message.reply_text(
            "This group isn't in a fed. Use /joinfed <fed_id> first.")
        return None
    user = update.effective_user
    if is_owner(context, user.id) or db.is_fed_admin(fed, user.id):
        return fed
    if allow_chat_admin and await user_is_admin(update, context):
        return fed
    await update.effective_message.reply_text(
        "You need to be a fed admin (/fpromote) to do that.")
    return None


async def fpromote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if fed and fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text(
            "Only the fed owner can promote fed admins.")
        return
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text("Reply to the user to promote.")
        return
    db.fed_add_admin(fed["fed_id"], target[0])
    await update.effective_message.reply_text(
        f"👑 {target[1]} is now a fed admin of <b>{html.escape(fed['name'])}</b>.",
        parse_mode=ParseMode.HTML)


async def fdemote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    if fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text(
            "Only the fed owner can demote fed admins.")
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text("Reply to the user to demote.")
        return
    db.fed_remove_admin(fed["fed_id"], target[0])
    await update.effective_message.reply_text(
        f"🔻 {target[1]} is no longer a fed admin.", parse_mode=ParseMode.HTML)


async def fedsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Subscribe this group's fed to another fed's ban feed."""
    my_fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not my_fed:
        await update.effective_message.reply_text(
            "Use this in a group that's in a fed.")
        return
    user = update.effective_user
    if my_fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text(
            "Only the fed owner can manage fed subscriptions.")
        return
    args = context.args or []
    if not args:
        # show current subscriptions
        subs = db.fed_subscriptions(my_fed["fed_id"])
        if subs:
            await update.effective_message.reply_text(
                "This fed is subscribed to: " +
                ", ".join(f"<code>{s}</code>" for s in subs),
                parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text(
                "Usage: /fedsubscribe <fed_id>\nSubscribing copies that fed's "
                "bans into your fed's groups.")
        return
    parent = db.get_fed(args[0])
    if not parent:
        await update.effective_message.reply_text("No fed with that ID.")
        return
    if parent["fed_id"] == my_fed["fed_id"]:
        await update.effective_message.reply_text("A fed can't subscribe to itself.")
        return
    db.fed_subscribe(my_fed["fed_id"], parent["fed_id"])
    # pull existing parent bans into this fed's enforcement reach
    await update.effective_message.reply_text(
        f"🌐 <b>{html.escape(my_fed['name'])}</b> now follows the ban feed of "
        f"<b>{html.escape(parent['name'])}</b>.",
        parse_mode=ParseMode.HTML)


async def unfedsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    my_fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not my_fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    user = update.effective_user
    if my_fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text(
            "Only the fed owner can manage fed subscriptions.")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /unfedsubscribe <fed_id>")
        return
    if db.fed_unsubscribe(my_fed["fed_id"], args[0]):
        await update.effective_message.reply_text("🗑 Unsubscribed.")
    else:
        await update.effective_message.reply_text("No such subscription.")


async def fedadmins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    admins = db.fed_admins_list(fed["fed_id"])
    text = f"👑 Owner: <code>{fed['owner_id']}</code>"
    if admins:
        text += "\nFed admins: " + ", ".join(f"<code>{a}</code>" for a in admins)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ----------------------------------------------------------------- fbans

async def fban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = await _fed_admin_required(update, context)
    if not fed:
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text(
            "Reply to the user (or give their ID) to fed-ban.")
        return
    tid, name = target
    if tid == fed["owner_id"] or tid in db.fed_admins_list(fed["fed_id"]) \
            or tid == context.bot.id:
        await update.effective_message.reply_text("I won't fed-ban a fed admin.")
        return
    emojis, reason = parse_default_emojis(context.args or [])
    if not reason and db.get_setting(fed["fed_id"], "fed_reason", "0") == "1":
        await update.effective_message.reply_text(
            "This fed requires a reason: /fban <reply> <reason>")
        return
    db.add_fban(fed["fed_id"], tid, reason, update.effective_user.id)
    await fedlog(fed, context,
                 f"🚫 <b>fban</b>: {name} (<code>{tid}</code>) by "
                 f"{update.effective_user.first_name}"
                 + (f" - {html.escape(reason)}" if reason else ""))

    # also apply in feds subscribed to this one (ban-feed propagation)
    target_chats = list(db.fed_chats_list(fed["fed_id"]))
    for child_id in db.fed_subscriptions(fed["fed_id"]):
        db.add_fban(child_id, tid, reason, update.effective_user.id)
        target_chats += db.fed_chats_list(child_id)

    banned_in = 0
    for chat_id, title in target_chats:
        try:
            await context.bot.ban_chat_member(chat_id, tid)
            banned_in += 1
            if chat_id != update.effective_chat.id:
                try:
                    await context.bot.send_message(
                        chat_id,
                        f"🌐 <b>Fed-ban</b> in {html.escape(fed['name'])}:\n"
                        f"{name} (<code>{tid}</code>)"
                        + (f" - {html.escape(reason)}" if reason else ""),
                        parse_mode=ParseMode.HTML)
                except TelegramError:
                    pass
        except TelegramError as e:
            log.warning("fban failed in chat %s: %s", chat_id, e)

    await update.effective_message.reply_text(
        f"🌐 <b>Fed-banned</b> {name} (<code>{tid}</code>) in "
        f"<b>{html.escape(fed['name'])}</b>.\n"
        f"Applied in {banned_in}/{len(db.fed_chats_list(fed['fed_id']))} groups."
        + (f"\nReason: {html.escape(reason)}" if reason else ""),
        parse_mode=ParseMode.HTML)


async def unfban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = await _fed_admin_required(update, context)
    if not fed:
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text("Reply to the user to un-fed-ban.")
        return
    tid, name = target
    if not db.remove_fban(fed["fed_id"], tid):
        await update.effective_message.reply_text("That user isn't fed-banned.")
        return
    await fedlog(fed, context,
                 f"✅ <b>unfban</b>: {name} (<code>{tid}</code>) by "
                 f"{update.effective_user.first_name}")
    target_chats = list(db.fed_chats_list(fed["fed_id"]))
    for child_id in db.fed_subscriptions(fed["fed_id"]):
        db.remove_fban(child_id, tid)
        target_chats += db.fed_chats_list(child_id)
    unbanned = 0
    for chat_id, _title in target_chats:
        try:
            await context.bot.unban_chat_member(chat_id, tid, only_if_banned=True)
            unbanned += 1
        except TelegramError:
            pass
    await update.effective_message.reply_text(
        f"✅ Un-fed-banned {name} in {unbanned} group(s).")


async def fbanlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    rows = db.list_fbans(fed["fed_id"])
    if not rows:
        await update.effective_message.reply_text("No fed-bans in this fed.")
        return
    body = "\n".join(
        f"• <code>{uid}</code>{' - ' + html.escape(r) if r else ''}"
        for uid, r in rows[:40])
    await update.effective_message.reply_text(
        f"🌐 <b>Fed-bans</b> ({len(rows)}):\n{body}", parse_mode=ParseMode.HTML)


async def fedchats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    chats = db.fed_chats_list(fed["fed_id"])
    body = "\n".join(f"• {html.escape(t or str(c))}" for c, t in chats[:40])
    await update.effective_message.reply_text(
        f"🌐 <b>{html.escape(fed['name'])}</b> - {len(chats)} group(s):\n{body}",
        parse_mode=ParseMode.HTML)


async def fedstat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = await get_target_user(update) or (
        update.effective_user.id, update.effective_user.first_name)
    tid, name = target
    feds_banned = db.user_fed_bans(tid)
    gban = db.get_gban(tid)
    lines = [f"🔎 <b>{html.escape(name)}</b> (<code>{tid}</code>)"]
    if feds_banned:
        for fed_name, reason in feds_banned:
            lines.append(f"🌐 Fed-banned in <b>{html.escape(fed_name)}</b>"
                         + (f": {html.escape(reason)}" if reason else ""))
    else:
        lines.append("🌐 Not fed-banned anywhere.")
    if gban:
        lines.append(f"🔫 Globally banned: {html.escape(gban)}")
    await update.effective_message.reply_text("\n".join(lines),
                                             parse_mode=ParseMode.HTML)


# ------------------------------------------------------- enforcement

async def check_banned_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -3 watchdog: fed-ban and gban checks on every message."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if msg is None or user is None or chat.type == "private":
        return

    # Track chats the bot is active in (for /gban reach).
    now = time.time()
    last = _last_announced.get(f"seen:{chat.id}", 0)
    if now - last > 3600:
        _last_announced[f"seen:{chat.id}"] = now
        db.upsert_chat(chat.id, chat.title or "?")

    if await user_is_admin(update, context):
        return

    fed_name = db.is_fed_banned(chat.id, user.id)
    if fed_name:
        await _enforce_ban(context, chat, user,
                           f"🌐 {user.first_name} is fed-banned "
                           f"in <b>{html.escape(fed_name)}</b>.")
        return

    gban_reason = db.get_gban(user.id)
    if gban_reason:
        await _enforce_ban(context, chat, user,
                           f"🔫 {user.first_name} is globally banned"
                           + (f": {html.escape(gban_reason)}" if gban_reason else "."))


async def _enforce_ban(context, chat, user, announce: str) -> None:
    key = f"banned:{chat.id}:{user.id}"
    now = time.time()
    if now - _last_announced.get(key, 0) < 300:  # announce max every 5 min
        return
    _last_announced[key] = now
    try:
        await chat.ban_member(user.id)
        if db.get_setting(chat.id, "quietfed", "0") != "1":
            try:
                await context.bot.send_message(chat.id, announce,
                                               parse_mode=ParseMode.HTML)
            except TelegramError:
                pass
    except TelegramError as e:
        log.warning("enforce ban failed in %s: %s", chat.id, e)


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fed/gban check on join."""
    msg = update.effective_message
    chat = update.effective_chat
    for user in msg.new_chat_members or []:
        if user.id == context.bot.id:
            continue
        fed_name = db.is_fed_banned(chat.id, user.id)
        if fed_name:
            await _enforce_ban(
                context, chat, user,
                f"🌐 {user.first_name} is fed-banned "
                f"in <b>{html.escape(fed_name)}</b> - removed.")
            continue
        gban = db.get_gban(user.id)
        if gban:
            await _enforce_ban(
                context, chat, user,
                f"🔫 {user.first_name} is globally banned - removed.")


# ------------------------------------------------------- global bans

async def gban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(context, update.effective_user.id):
        await update.effective_message.reply_text(
            "Only the bot owner can use global bans.")
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text(
            "Reply to the user (or give their ID) to globally ban.")
        return
    tid, name = target
    emojis, reason = parse_default_emojis(context.args or [])
    db.add_gban(tid, reason, update.effective_user.id)
    banned_in = 0
    for chat_id, _title in db.all_chats():
        try:
            await context.bot.ban_chat_member(chat_id, tid)
            banned_in += 1
        except TelegramError:
            pass
    await update.effective_message.reply_text(
        f"🔫 <b>Globally banned</b> {name} (<code>{tid}</code>) in "
        f"{banned_in} known group(s)."
        + (f"\nReason: {html.escape(reason)}" if reason else ""),
        parse_mode=ParseMode.HTML)


async def ungban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(context, update.effective_user.id):
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text("Reply to the user or give their ID.")
        return
    tid, name = target
    if not db.remove_gban(tid):
        await update.effective_message.reply_text("That user isn't globally banned.")
        return
    for chat_id, _title in db.all_chats():
        try:
            await context.bot.unban_chat_member(chat_id, tid, only_if_banned=True)
        except TelegramError:
            pass
    await update.effective_message.reply_text(f"✅ Removed global ban on {name}.")


async def gbanlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(context, update.effective_user.id):
        return
    rows = db.list_gbans()
    if not rows:
        await update.effective_message.reply_text("No global bans.")
        return
    body = "\n".join(
        f"• <code>{uid}</code>{' - ' + html.escape(r) if r else ''}"
        for uid, r in rows[:40])
    await update.effective_message.reply_text(
        f"🔫 <b>Global bans</b> ({len(rows)}):\n{body}", parse_mode=ParseMode.HTML)


# ------------------------------------------------------------ fed extras

async def renamefed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    user = update.effective_user
    if fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text("Only the fed owner can rename it.")
        return
    name = " ".join(context.args or []).strip()
    if not name:
        await update.effective_message.reply_text("Usage: /renamefed <new name>")
        return
    db.rename_fed(fed["fed_id"], name[:64])
    await update.effective_message.reply_text(
        f"✅ Fed renamed to <b>{html.escape(name[:64])}</b>.",
        parse_mode=ParseMode.HTML)


async def fedtransfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    user = update.effective_user
    if fed["owner_id"] != user.id and not is_owner(context, user.id):
        await update.effective_message.reply_text(
            "Only the fed owner can transfer it.")
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text(
            "Reply to the user who should own the fed.")
        return
    db.transfer_fed(fed["fed_id"], target[0])
    await update.effective_message.reply_text(
        f"👑 Federation transferred to {target[1]}.")


async def myfeds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.my_feds(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text(
            "You don't own or administer any feds.")
        return
    body = "\n".join(
        f"• <b>{html.escape(n)}</b> (<code>{f}</code>) - {role}"
        for f, n, role in rows[:30])
    await update.effective_message.reply_text(
        f"🌐 <b>Your feds</b>:\n{body}", parse_mode=ParseMode.HTML)


async def fedexport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export the current fed's ban list as CSV or JSON."""
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    if not (is_owner(context, update.effective_user.id)
            or db.is_fed_admin(fed, update.effective_user.id)):
        await update.effective_message.reply_text("Fed admins only.")
        return
    fmt = (context.args or ["csv"])[0].lower()
    import io as _io
    rows = db.list_fbans(fed["fed_id"])
    if fmt == "json":
        import json as _json
        payload = _json.dumps(
            {"fed": fed["name"], "bans": [{"user_id": u, "reason": r}
                                          for u, r in rows]}, indent=2)
        fname = f"{fed['fed_id']}_bans.json"
    else:
        payload = "user_id,reason\n" + "\n".join(
            f'{u},"{str(r).replace(chr(34), chr(39))}"' for u, r in rows)
        fname = f"{fed['fed_id']}_bans.csv"
    buf = _io.BytesIO(payload.encode())
    buf.name = fname
    await update.effective_message.reply_document(
        document=buf,
        caption=f"📦 {len(rows)} fed-ban(s) from {fed['name']}. "
                "Restore with /fedimport (reply to this file).")


async def fedimport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fedimport <overwrite|keep> - reply to a CSV/JSON backup."""
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    if not (is_owner(context, update.effective_user.id)
            or db.is_fed_admin(fed, update.effective_user.id)):
        await update.effective_message.reply_text("Fed admins only.")
        return
    mode = (context.args or ["keep"])[0].lower()
    if mode not in ("overwrite", "keep"):
        await update.effective_message.reply_text(
            "Usage: reply to the backup file with /fedimport <overwrite|keep>")
        return
    msg = update.effective_message
    source = msg.reply_to_message
    if not source or not source.document:
        await msg.reply_text("Reply to a backup .csv or .json file.")
        return
    try:
        tg_file = await source.document.get_file()
        raw = bytes(await tg_file.download_as_bytearray()).decode()
    except (TelegramError, UnicodeDecodeError) as e:
        await msg.reply_text(f"Couldn't read that file: {e}")
        return
    import json as _json
    entries = []
    try:
        if raw.lstrip().startswith("{"):
            data = _json.loads(raw)
            entries = [(b["user_id"], b.get("reason", "")) for b in data["bans"]]
        else:
            lines = [l for l in raw.splitlines()[1:] if l.strip()]
            for line in lines:
                uid, _, rest = line.partition(",")
                entries.append((int(uid.strip().strip('"')),
                                rest.strip().strip('"')))
    except (ValueError, KeyError, TypeError) as e:
        await msg.reply_text(f"Invalid backup format: {e}")
        return
    if mode == "overwrite":
        for uid, _r in db.list_fbans(fed["fed_id"]):
            db.remove_fban(fed["fed_id"], uid)
    for uid, reason in entries[:500]:
        db.add_fban(fed["fed_id"], uid, reason, update.effective_user.id)
    await msg.reply_text(
        f"✅ Imported {len(entries)} fed-ban(s) into {fed['name']}.")


async def setfedlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    if fed["owner_id"] != update.effective_user.id and \
            not is_owner(context, update.effective_user.id):
        await update.effective_message.reply_text("Fed owner only.")
        return
    db.set_setting(fed["fed_id"], "fed_log", update.effective_chat.id)
    await update.effective_message.reply_text(
        "📜 This chat is now the federation log - all fed events will be "
        "posted here.")


async def unsetfedlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    if fed["owner_id"] != update.effective_user.id and \
            not is_owner(context, update.effective_user.id):
        await update.effective_message.reply_text("Fed owner only.")
        return
    db.set_setting(fed["fed_id"], "fed_log", 0)
    await update.effective_message.reply_text("📜 Fed log unset.")


async def fedlog(fed: dict, context, text: str) -> None:
    """Send an event to the fed log channel if configured."""
    chat_id = db.get_setting(fed["fed_id"], "fed_log")
    if not chat_id:
        return
    try:
        await context.bot.send_message(
            int(chat_id), f"🌐 <b>{html.escape(fed['name'])}</b>\n{text}",
            parse_mode=ParseMode.HTML)
    except (TelegramError, ValueError, TypeError):
        pass


async def fedreason_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fed = db.chat_fed(update.effective_chat.id) \
        if update.effective_chat.type != "private" else None
    if not fed:
        await update.effective_message.reply_text("Use this in a fed group.")
        return
    if fed["owner_id"] != update.effective_user.id and \
            not is_owner(context, update.effective_user.id):
        await update.effective_message.reply_text("Fed owner only.")
        return
    args = context.args or []
    if args and args[0].lower() in ("on", "off", "yes", "no"):
        db.set_setting(fed["fed_id"], "fed_reason",
                      "1" if args[0].lower() in ("on", "yes") else "0")
        await update.effective_message.reply_text(
            f"Fbans now {'require' if args[0].lower() in ('on', 'yes') else 'do not require'} a reason.")
        return
    await update.effective_message.reply_text("Usage: /fedreason on|off")


async def quietfed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if args and args[0].lower() in ("on", "off", "yes", "no"):
        db.set_setting(chat_id, "quietfed",
                      "1" if args[0].lower() in ("on", "yes") else "0")
        await update.effective_message.reply_text(
            f"🤫 Fed-ban join notifications are now {args[0].upper()}.")
        return
    cur = db.get_setting(chat_id, "quietfed", "0")
    await update.effective_message.reply_text(
        f"🤫 Quiet fed: {'ON' if cur == '1' else 'OFF'}. "
        "Usage: /quietfed on|off")


async def feddemoteme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /feddemoteme <fed_id>")
        return
    fed = db.get_fed(args[0])
    if not fed:
        await update.effective_message.reply_text("No fed with that ID.")
        return
    db.fed_remove_admin(fed["fed_id"], update.effective_user.id)
    await update.effective_message.reply_text(
        f"🔻 You are no longer an admin of {fed['name']}.")
