"""UnkilBonker - all-in-one Telegram bot: group management (Rose-style),
sticker kanging (Stickerkang-style) and an optional AI channel rewriter."""

import logging
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationBuilder, CommandHandler,
                          MessageHandler, filters)

import db
import features
import moderation
import rewriter
import stickers

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("unkilbonker")

WELCOME = (
    "🦘 <b>UnkilBonker is alive!</b>\n\n"
    "An all-in-one bot:\n"
    "• 👮 <b>Moderation</b> - /ban /tban /mute /warn /purge ...\n"
    "• 📝 <b>Notes & filters</b> - /save, /filter\n"
    "• 🔒 <b>Locks & antiflood</b> - /lock, /antiflood\n"
    "• 🦘 <b>Sticker kang</b> - /kang a sticker to make your own pack\n"
    "• 🤖 <b>AI post rewriter</b> (if configured) for channel admins\n\n"
    "Send /help for the full command list."
)

HELP = (
    "🦘 <b>UnkilBonker commands</b>\n\n"
    "<b>Moderation</b> (admins):\n"
    "/ban - /tban 30m - /unban - /kick\n"
    "/mute - /tmute 10m - /unmute\n"
    "/warn - /warnings - /resetwarns - /warnlimit\n"
    "/purge (reply) - /del (reply) - /pin - /unpin\n\n"
    "<b>Group setup</b>:\n"
    "/setwelcome <text> - /welcome on|off - /resetwelcome\n"
    "/save <name> <text> - /get <name> - /notes - /clear <name>\n"
    "• also triggers: #notename in chat\n"
    "/filter <trigger> <reply> - /stop <trigger> - /filters - /stopall\n"
    "/lock <type> - /unlock <type> - /locks\n"
    "/antiflood <limit|off>\n\n"
    "<b>Everyone</b>:\n"
    "/afk <reason> - /kickme - /id - /info - /adminlist - /ping\n\n"
    "<b>Stickers</b>:\n"
    "/kang [emoji] (reply to sticker/photo)\n"
    "/packs - /getsticker (reply to a sticker)"
)


async def start(update: Update, context) -> None:
    await update.effective_message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context) -> None:
    await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)


def on_error(update: object, context) -> None:
    log.error("Unhandled error:", exc_info=context.error)


async def post_init(app: Application) -> None:
    bot = await app.bot.get_me()
    app.bot_data["owner_id"] = int(os.environ["OWNER_ID"]) \
        if os.environ.get("OWNER_ID", "").strip().isdigit() else None
    app.bot_data["sudo_ids"] = os.environ.get("SUDO_IDS", "")
    log.info("Running as @%s (id %s)", bot.username, bot.id)
    if rewriter.enabled():
        log.info("AI channel rewriter active for %s (model %s)",
                 rewriter.CHANNEL_ID, rewriter.AI_MODEL)


def register(app: Application) -> None:
    admin = ~filters.ChatType.PRIVATE  # most commands need a group context

    app.add_handler(CommandHandler(["start", "help"], start), 1)
    app.add_handler(CommandHandler("help", help_cmd), 1)

    # ---- enforcement first (negative groups run before everything)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.check_flood), group=-2)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.enforce_locks), group=-1)

    # ---- moderation
    for cmd, fn in (
        ("ban", moderation.ban), ("tban", moderation.tban),
        ("unban", moderation.unban), ("kick", moderation.kick),
        ("mute", moderation.mute), ("tmute", moderation.tmute),
        ("unmute", moderation.unmute), ("kickme", moderation.kickme),
        ("warn", moderation.warn), ("warnings", moderation.warnings),
        ("resetwarns", moderation.resetwarns), ("warnlimit", moderation.warnlimit),
        ("purge", moderation.purge), ("del", moderation.del_msg),
        ("pin", moderation.pin), ("unpin", moderation.unpin),
    ):
        app.add_handler(CommandHandler(cmd, fn))

    # ---- group features
    for cmd, fn in (
        ("setwelcome", features.set_welcome),
        ("welcome", features.welcome_toggle),
        ("resetwelcome", features.reset_welcome),
        ("save", features.save_note_cmd),
        ("get", features.get_note_cmd),
        ("notes", features.notes_cmd),
        ("clear", features.clear_note_cmd),
        ("filter", features.add_filter_cmd),
        ("stop", features.stop_filter_cmd),
        ("stopall", features.stopall_filters_cmd),
        ("filters", features.filters_cmd),
        ("lock", features.lock_cmd),
        ("unlock", features.unlock_cmd),
        ("locks", features.locks_cmd),
        ("antiflood", features.antiflood_cmd),
        ("afk", features.afk_cmd),
        ("id", features.id_cmd),
        ("info", features.info_cmd),
        ("adminlist", features.adminlist_cmd),
        ("ping", features.ping_cmd),
    ):
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, features.greet_new_members))

    # ---- passive watchers (group 0, after commands)
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND
        & (filters.TEXT | filters.CAPTION),
        features.afk_watch))
    app.add_handler(MessageHandler(
        filters.Entity("hashtag") & (filters.TEXT | filters.CAPTION),
        features.note_hashtag))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND
        & (filters.TEXT | filters.CAPTION),
        features.run_filters))

    # ---- stickers (work in private and groups)
    for cmd, fn in (
        ("kang", stickers.kang),
        ("packs", stickers.packs_cmd),
        ("getsticker", stickers.getsticker_cmd),
    ):
        app.add_handler(CommandHandler(cmd, fn))

    # ---- optional AI channel rewriter
    if rewriter.enabled():
        app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST,
                                       rewriter.on_channel_post))


def main() -> None:
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        sys.exit("Set TELEGRAM_BOT_TOKEN (see .env.example).")

    db.init_db()

    app: Application = ApplicationBuilder().token(
        os.environ["TELEGRAM_BOT_TOKEN"]).post_init(post_init).build()
    register(app)
    app.add_error_handler(on_error)

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if webhook_url:
        log.info("Starting in WEBHOOK mode at %s", webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "8443")),
            url_path=os.environ["TELEGRAM_BOT_TOKEN"],
            webhook_url=f"{webhook_url.rstrip('/')}/{os.environ['TELEGRAM_BOT_TOKEN']}",
            secret_token=os.environ.get("WEBHOOK_SECRET") or None,
        )
    else:
        log.info("Starting in POLLING mode.")
        app.run_polling(allowed_updates=["message", "edited_message",
                                          "channel_post", "edited_channel_post"])


if __name__ == "__main__":
    main()
