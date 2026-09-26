"""Extra admin tools: promote/demote, invite links, group info/settings, reports."""

import html
import logging

from telegram import ChatPermissions, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import db
from moderation import admin_gate, get_target_user, user_is_admin

log = logging.getLogger("unkilbonker.extras")


# ------------------------------------------------------------ promote

PROMOTE_BASIC = dict(
    can_delete_messages=True, can_restrict_members=True,
    can_invite_users=True, can_pin_messages=True)
PROMOTE_FULL = dict(PROMOTE_BASIC, can_promote_members=True, can_change_info=True)


async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _promote(update, context, PROMOTE_FULL if context.args and
                   context.args[0] == "full" else PROMOTE_BASIC, "Admin")


async def fullpromote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _promote(update, context, PROMOTE_FULL, "Full admin")


async def _promote(update, context, perms: dict, label: str) -> None:
    msg = update.effective_message
    if not await admin_gate(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to the user to promote.")
        return
    tid, name = target
    try:
        await update.effective_chat.promote_member(tid, **perms)
        await msg.reply_text(f"👑 {name} is now {label.lower()}.")
    except TelegramError as e:
        await msg.reply_text(
            f"Couldn't promote: {e}\n"
            "(I need to be admin with 'Add new admins' rights.)")


async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not await admin_gate(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await msg.reply_text("Reply to the user to demote.")
        return
    tid, name = target
    try:
        await update.effective_chat.promote_member(tid)  # no perms = demoted
        await msg.reply_text(f"🔻 {name} was demoted.")
    except TelegramError as e:
        await msg.reply_text(f"Couldn't demote: {e}")


# ------------------------------------------------------------ invite

async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    try:
        link = await update.effective_chat.create_invite_link()
        await update.effective_message.reply_text(
            f"🔗 Invite link:\n{link.invite_link}")
    except TelegramError as e:
        await update.effective_message.reply_text(
            f"Couldn't create a link: {e}\n"
            "(I need 'Invite users via link' admin right.)")


# ------------------------------------------------------------ group info

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        count = await update.effective_chat.get_member_count()
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't count: {e}")
        return
    await update.effective_message.reply_text(
        f"👥 <b>{html.escape(update.effective_chat.title or 'Chat')}</b>: "
        f"{count} member(s)", parse_mode=ParseMode.HTML)


async def set_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    title = " ".join(context.args or []).strip()
    if not title:
        await update.effective_message.reply_text("Usage: /settitle <new group title>")
        return
    try:
        await update.effective_chat.set_title(title[:128])
        await update.effective_message.reply_text("✅ Title updated.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't set title: {e}")


async def set_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    desc = " ".join(context.args or []).strip()
    if not desc:
        await update.effective_message.reply_text("Usage: /setdesc <new description>")
        return
    try:
        await update.effective_chat.set_description(desc[:255])
        await update.effective_message.reply_text("✅ Description updated.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't set description: {e}")


async def set_gpic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    source = update.effective_message.reply_to_message or update.effective_message
    photo = source.photo[-1] if source.photo else None
    if not photo:
        await update.effective_message.reply_text(
            "Reply to a photo with /setgpic to set the group picture.")
        return
    try:
        await update.effective_chat.set_photo(photo=photo.file_id)
        await update.effective_message.reply_text("✅ Group picture updated.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"Couldn't set picture: {e}")


# ------------------------------------------------------------ reports

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if db.get_setting(chat.id, "reports", "1") != "1":
        return
    target = msg.reply_to_message
    reason = " ".join(context.args or [])
    if not target:
        await msg.reply_text("Reply to the message you want to report.")
        return
    reporter = update.effective_user
    link = f"t.me/c/{str(chat.id).lstrip('-100')}/{target.message_id}" \
        if str(chat.id).startswith("-100") else ""
    notice = (f"⚠️ <b>Report</b> from {reporter.mention_html()}:\n"
              f"{html.escape(reason) if reason else '(no reason given)'}"
              + (f"\n{link}" if link else ""))
    sent = 0
    try:
        admins = await chat.get_administrators()
    except TelegramError:
        admins = []
    for m in admins:
        if m.user.is_bot or m.user.id == reporter.id:
            continue
        try:
            await context.bot.send_message(
                m.user.id, notice, parse_mode=ParseMode.HTML)
            sent += 1
        except TelegramError:
            pass  # admin hasn't started the bot - skip
    await msg.reply_text(
        f"🚨 Report sent to {sent} admin(s)." if sent
        else "🚨 Reported, but admins need to /start me in private to "
             "receive reports.")


async def reports_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    args = context.args or []
    if args and args[0].lower() in ("on", "off"):
        db.set_setting(update.effective_chat.id, "reports",
                       "1" if args[0] == "on" else "0")
        await update.effective_message.reply_text(
            f"🚨 Reports are now {args[0].upper()}.")
        return
    cur = db.get_setting(update.effective_chat.id, "reports", "1")
    await update.effective_message.reply_text(
        f"🚨 Reports are currently {'ON' if cur == '1' else 'OFF'}. "
        "Use /reports on|off.")
