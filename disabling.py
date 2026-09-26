"""Disabling - turn off individual commands per chat, Rose-style."""

import html
import json
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes, MessageHandler, filters

import db

log = logging.getLogger("unkilbonker.disabling")


def disableable_commands() -> set:
    """Commands that can be disabled (the bot's public command set)."""
    return {
        "approve", "unapprove", "approved", "unapproveall", "approval",
        "promote", "fullpromote", "demote", "adminlist", "admincache",
        "ban", "dban", "sban", "tban", "stban", "unban", "kick", "dkick",
        "skick", "kickme", "mute", "dmute", "smute", "tmute", "stmute",
        "unmute", "purge", "del", "pin", "unpin", "warn", "unwarn",
        "warnings", "resetwarns", "warnlimit", "setwarnlimit", "setwarnmode",
        "flood", "setflood", "setfloodtimer", "floodmode", "setfloodmode",
        "clearflood", "lock", "unlock", "locks", "locktypes", "allowlist",
        "rmallowlist", "addblocklist", "rmblocklist", "blocklist",
        "blocklistmode", "blocklistreason", "setblocklistreason",
        "resetblocklistreason", "blocklistdelete", "unblocklistall",
        "cleanservice", "keepservice", "nocleanservice", "cleanservicetypes",
        "captcha", "captchamode", "captchatime", "setcaptchatext",
        "captchafile", "antiraid", "raidtime", "raidactiontime",
        "autoantiraid", "setraidthreshold", "welcome", "setwelcome",
        "resetwelcome", "goodbye", "setgoodbye", "resetgoodbye",
        "cleanwelcome", "clearcleft", "save", "get", "notes", "clear",
        "clearallnotes", "filter", "filters", "stop", "stopall",
        "stopallfilters", "afk", "id", "info", "ping", "newfed", "delfed",
        "fedinfo", "joinfed", "joined", "leavefed", "fedchats", "fban",
        "unfban", "fbanlist", "fedstat", "fpromote", "fedpromote", "fdemote",
        "feddemote", "fedadmins", "fedsubscribe", "subfed", "unsubfed",
        "unfedsubscribe", "myfeds", "chatfed", "renamefed", "fedtransfer",
        "fedexport", "fedimport", "setfedlog", "unsetfedlog", "fedreason",
        "quietfed", "feddemoteme", "gban", "ungban", "gbanlist", "invite",
        "users", "settitle", "setdesc", "setgpic", "report", "reports",
        "connect", "disconnect", "reconnect", "connection", "export",
        "import", "q", "mmf", "memefi", "tagall", "all", "utagall", "uall",
        "utag", "cancel", "stoptag", "adsremover", "bonk", "fbonk", "bonki",
        "kang", "packs", "getsticker",
    }


def _disabled_set(chat_id: int) -> set:
    raw = db.get_setting(chat_id, "disabled_cmds")
    try:
        return set(json.loads(raw)) if raw else set()
    except (ValueError, TypeError):
        return set()


async def disable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from moderation import admin_gate
    if not await admin_gate(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    disabled = _disabled_set(chat_id)
    known = disableable_commands()
    if not args:
        await update.effective_message.reply_text(
            "Usage: /disable <command|all>\nSee /disableable for the list.")
        return
    for arg in args:
        name = arg.lstrip("/").lower()
        if name == "all":
            disabled = set(known)
        elif name in known:
            disabled.add(name)
        else:
            await update.effective_message.reply_text(
                f"Not a disableable command: {name}")
            return
    db.set_setting(chat_id, "disabled_cmds", json.dumps(sorted(disabled)))
    await update.effective_message.reply_text(
        f"🚫 Disabled {len(disabled)} command(s). See /disabled.")


async def enable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from moderation import admin_gate
    if not await admin_gate(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    disabled = _disabled_set(chat_id)
    if not args:
        await update.effective_message.reply_text("Usage: /enable <command|all>")
        return
    for arg in args:
        name = arg.lstrip("/").lower()
        if name == "all":
            disabled.clear()
        else:
            disabled.discard(name)
    db.set_setting(chat_id, "disabled_cmds", json.dumps(sorted(disabled)))
    await update.effective_message.reply_text(
        f"✅ {len(disabled)} command(s) still disabled.")


async def disableable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmds = sorted(disableable_commands())
    body = ", ".join(f"<code>{c}</code>" for c in cmds)
    await update.effective_message.reply_text(
        f"⚙️ <b>Disableable commands</b> ({len(cmds)}):\n{body}",
        parse_mode=ParseMode.HTML)


async def disabled_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    disabled = sorted(_disabled_set(update.effective_chat.id))
    if not disabled:
        await update.effective_message.reply_text("No disabled commands.")
        return
    await update.effective_message.reply_text(
        "🚫 <b>Disabled</b>: " + ", ".join(f"<code>{c}</code>" for c in disabled),
        parse_mode=ParseMode.HTML)


async def disabledel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from moderation import admin_gate
    if not await admin_gate(update, context):
        return
    args = context.args or []
    if args and args[0].lower() in ("on", "off", "yes", "no"):
        on = "1" if args[0].lower() in ("on", "yes") else "0"
        db.set_setting(update.effective_chat.id, "disabledel", on)
        await update.effective_message.reply_text(
            f"🚫 Disabled-command deletion is now {args[0].upper()}.")
        return
    await update.effective_message.reply_text("Usage: /disabledel on|off")


async def disableadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from moderation import admin_gate
    if not await admin_gate(update, context):
        return
    args = context.args or []
    if args and args[0].lower() in ("on", "off", "yes", "no"):
        on = "1" if args[0].lower() in ("on", "yes") else "0"
        db.set_setting(update.effective_chat.id, "disableadmin", on)
        await update.effective_message.reply_text(
            f"⚙️ Disabled commands now apply to admins too: {args[0].upper()}.")
        return
    await update.effective_message.reply_text("Usage: /disableadmin on|off")


async def enforce_disabled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registered FIRST in group 0, so it runs before any command handler."""
    msg = update.effective_message
    if msg is None or update.effective_chat.type == "private":
        return
    text = msg.text or ""
    if not text.startswith("/") or "@" in text.split()[0]:
        # skip commands addressed to other bots (e.g. /cmd@OtherBot)
        pass
    cmd = text.split()[0].lstrip("/").split("@")[0].lower() if text else ""
    if not cmd or cmd not in disableable_commands():
        return
    disabled = _disabled_set(update.effective_chat.id)
    if cmd not in disabled:
        return
    from moderation import user_is_admin
    user = update.effective_user
    if not user:
        return
    if await user_is_admin(update, context) and \
            db.get_setting(update.effective_chat.id, "disableadmin", "0") != "1":
        return  # admins bypass unless disableadmin is on
    if db.get_setting(update.effective_chat.id, "disabledel", "0") == "1":
        try:
            await msg.delete()
        except TelegramError:
            pass
    raise ApplicationHandlerStop
