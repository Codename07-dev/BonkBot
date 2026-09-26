"""Approvals - trusted users exempt from locks, blocklists and antiflood."""

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import db
from moderation import get_target_user, user_is_admin


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text(
            "Reply to a user or give their ID: /approve <id>")
        return
    tid, name = target
    db.approve(update.effective_chat.id, tid, name)
    await update.effective_message.reply_text(
        f"✅ <b>{html.escape(name)}</b> is now approved. Locks, blocklists and "
        "antiflood no longer apply to them.", parse_mode=ParseMode.HTML)


async def unapprove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    target = await get_target_user(update)
    if not target:
        await update.effective_message.reply_text("Reply to a user or give their ID.")
        return
    if db.unapprove(update.effective_chat.id, target[0]):
        await update.effective_message.reply_text(
            f"❌ {target[1]} unapproved - they're subject to locks, blocklists "
            "and antiflood again.")
    else:
        await update.effective_message.reply_text("That user isn't approved.")


async def approved_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.approved_list(update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text("No approved users in this chat.")
        return
    body = "\n".join(f"• {html.escape(n or '?')} (<code>{u}</code>)"
                     for u, n in rows[:60])
    await update.effective_message.reply_text(
        f"✅ <b>Approved users</b> ({len(rows)}):\n{body}", parse_mode=ParseMode.HTML)


async def unapproveall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await user_is_admin(update, context):
        return
    n = db.unapprove_all(update.effective_chat.id)
    await update.effective_message.reply_text(
        f"🗑 Removed all {n} approval(s). This cannot be undone.")


async def approval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = await get_target_user(update) or (
        update.effective_user.id, update.effective_user.first_name)
    ok = db.is_approved(update.effective_chat.id, target[0])
    await update.effective_message.reply_text(
        f"✅ {target[1]} is approved - locks, blocklists and antiflood don't "
        "apply to them." if ok else
        f"❌ {target[1]} is not approved in this chat.")
