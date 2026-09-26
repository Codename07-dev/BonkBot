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

import approvals
import db
import disabling
import extras
import features
import feds
import memefi
import moderation
import quotly
import captcha
import rewriter
import stickers
import tagall
import utilities

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("unkilbonker")

WELCOME = (
    "🦘 <b>UnkilBonker is alive!</b>\n\n"
    "An all-in-one bot:\n"
    "• 👮 <b>Moderation</b> - /ban /dban /sban /tban /mute /warn /purge ...\n"
    "• ✅ <b>Approvals</b> - /approve - trusted users skip all limits\n"
    "• 🔒 <b>Locks, antiflood & blocklists</b> - /lock, /antiflood, /blocklist\n"
    "• 🛡 <b>Captcha & anti-raid</b> - /captcha, /antiraid 3h\n"
    "• 🌐 <b>Federations</b> - /newfed, /joinfed, /fban, /fedexport\n"
    "• 🧹 <b>Clean service & disabling</b> - /cleanservice, /disable\n"
    "• 📝 <b>Notes, filters & greetings</b> - /save, /filter, /welcome\n"
    "• 🖼 <b>Quotly quotes</b> - reply with /q\n"
    "• 🦘 <b>Sticker kang</b> - /kang - and meme text /mmf\n"
    "• 🏷 <b>Tag all</b> - /tagall, /utagall\n"
    "• 🤖 <b>AI post rewriter</b> (if configured) for channel admins\n\n"
    "Send /help for the full command list."
)

HELP = (
    "🦘 <b>UnkilBonker commands</b>\n\n"
    "<b>Moderation</b> (admins):\n"
    "/ban - /tban 30m - /unban - /kick - /kickme\n"
    "/dban /sban /stban - ban + delete or silent variants\n"
    "/dmute /smute /stmute /dkick /skick - same for mute & kick\n"
    "/mute - /tmute 10m - /unmute\n"
    "/warn - /unwarn - /warnings - /resetwarns - /setwarnlimit\n"
    "/setwarnmode <ban/tban/mute/tmute/kick> [time]\n"
    "/purge (reply) - /del (reply) - /pin - /unpin\n"
    "/promote - /fullpromote - /demote - /adminlist - /admincache\n\n"
    "<b>Approvals</b>:\n"
    "/approve (reply) - /unapprove - /approved - /unapproveall\n"
    "/approval - approved users skip locks, blocklists & antiflood\n\n"
    "<b>Anti-spam</b>:\n"
    "/setflood <n> - /setfloodtimer <n> <dur> - /flood\n"
    "/floodmode <ban/mute/kick/tban/tmute> [time] - /clearflood\n"
    "/lock <type> - /unlock <type> - /locks - /locktypes\n"
    "/allowlist <domain> - /rmallowlist <domain>\n"
    "/addblocklist - /rmblocklist - /unblocklistall - /blocklist\n"
    "/blocklistmode <nothing/ban/mute/kick/tban/tmute/warn>\n"
    "/blocklistdelete on|off - /setblocklistreason (wildcards ? * **)\n\n"
    "<b>Captcha & raid</b>:\n"
    "/captcha on|off - /captchamode <button/math/text>\n"
    "/captchatime 2m - /setcaptchatext - /captchafile\n"
    "/antiraid [3h] - /raidtime - /raidactiontime\n"
    "/autoantiraid <n|off> - /setraidthreshold <n>\n\n"
    "<b>Clean & disabling</b>:\n"
    "/cleanservice <join/leave/pin/title/photo/videochat/other/all>\n"
    "/keepservice - /nocleanservice - /cleanservicetypes\n"
    "/disable <cmd> - /enable - /disableable - /disabled\n"
    "/disabledel on|off - /disableadmin on|off\n\n"
    "<b>Federations</b>:\n"
    "/newfed <name> - /renamefed - /fedtransfer - /delfed\n"
    "/joinfed - no ID needed, joins YOUR fed - /leavefed - /fedchats\n"
    "/fban (reply) - /unfban - /fbanlist - /fedstat - /fedreason\n"
    "/fpromote - /fdemote - /feddemoteme - /fedadmins - /myfeds\n"
    "/subfed <fed_id> - /unsubfed - /chatfed - /quietfed\n"
    "/fedexport csv|json - /fedimport (reply)\n"
    "/setfedlog - /unsetfedlog - fed event log channel\n\n"
    "<b>Global bans</b> (bot owner): /gban - /ungban - /gbanlist\n\n"
    "<b>Group setup</b>:\n"
    "/setwelcome - /welcome on|off - /resetwelcome\n"
    "/setgoodbye - /goodbye on|off - /resetgoodbye\n"
    "/cleanwelcome on|off - /clearcleft on|off\n"
    "/save <name> <text|reply media> - /get - /notes - /clear\n"
    "• also triggers: #notename in chat - /clearallnotes\n"
    "/filter <trigger> <reply> - /stop - /filters - /stopall\n\n"
    "<b>Group tools</b>:\n"
    "/invite - /users - /settitle - /setdesc - /setgpic\n"
    "/report (reply) - /reports on|off\n\n"
    "<b>Remote & backups</b>:\n"
    "/connect (in group), then DM me commands\n"
    "/disconnect - /connection - /export - /import\n\n"
    "<b>Everyone</b>:\n"
    "/afk <reason> - /kickme - /id - /info - /adminlist - /ping\n\n"
    "<b>Stickers & fun</b>:\n"
    "/kang [emoji] - /packs - /getsticker\n"
    "/q (reply) - turn any message into a quote sticker\n"
    "/mmf Top ; Bottom - /mmf -c Center - meme text on stickers\n"
    "/tagall [msg] - /utagall - /cancel to stop\n"
    "/adsremover on|off - auto-delete ads & promos\n"
    "/bonk - /fbonk - /bonki (ban/fed-ban/warn)"
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
    C = utilities.connected  # /connect support: group commands work from DM

    # ---- disabled-command enforcement FIRST in group 0 (before commands)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.COMMAND,
                                   disabling.enforce_disabled), group=0)

    app.add_handler(CommandHandler(["start", "help"], start), 1)
    app.add_handler(CommandHandler("help", help_cmd), 1)

    # ---- enforcement first (negative groups run before everything)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   feds.check_banned_user), group=-3)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.check_flood), group=-2)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.enforce_locks), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.enforce_blocklist), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.enforce_ads), group=-1)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS,
                                   feds.on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS,
                                   captcha.on_member_join))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER,
                                   features.on_left_members))

    # ---- moderation
    for cmd, fn in (
        ("ban", moderation.ban), ("tban", moderation.tban),
        ("unban", moderation.unban), ("kick", moderation.kick),
        ("mute", moderation.mute), ("tmute", moderation.tmute),
        ("unmute", moderation.unmute), ("kickme", moderation.kickme),
        ("warn", moderation.warn), ("warnings", moderation.warnings),
        ("unwarn", moderation.unwarn), ("resetwarns", moderation.resetwarns),
        ("warnlimit", moderation.setwarnlimit),
        ("setwarnlimit", moderation.setwarnlimit),
        ("setwarnmode", moderation.setwarnmode),
        ("purge", moderation.purge), ("del", moderation.del_msg),
        ("pin", moderation.pin), ("unpin", moderation.unpin),
        # action variants (Rose-style)
        ("dban", moderation.dban), ("sban", moderation.sban),
        ("stban", moderation.stban), ("dmute", moderation.dmute),
        ("smute", moderation.smute), ("stmute", moderation.stmute),
        ("dkick", moderation.dkick), ("skick", moderation.skick),
        # admin module extras
        ("admincache", moderation.admincache_cmd),
        ("adminerror", moderation.adminerror_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- group features
    for cmd, fn in (
        ("setwelcome", features.set_welcome),
        ("welcome", features.welcome_toggle),
        ("resetwelcome", features.reset_welcome),
        ("save", features.save_note_cmd),
        ("get", features.get_note_cmd),
        ("notes", features.notes_cmd),
        ("clear", features.clear_note_cmd),
        ("clearallnotes", features.clearallnotes_cmd),
        ("filter", features.add_filter_cmd),
        ("stop", features.stop_filter_cmd),
        ("stopall", features.stopall_filters_cmd),
        ("stopallfilters", features.stopall_filters_cmd),
        ("filters", features.filters_cmd),
        ("lock", features.lock_cmd),
        ("unlock", features.unlock_cmd),
        ("locks", features.locks_cmd),
        ("locktypes", features.locktypes_cmd),
        ("allowlist", features.allowlist_cmd),
        ("rmallowlist", features.rmallowlist_cmd),
        ("antiflood", features.antiflood_cmd),
        ("setflood", features.antiflood_cmd),
        ("flood", features.antiflood_cmd),
        ("setfloodmode", features.floodmode_cmd),
        ("floodmode", features.floodmode_cmd),
        ("setfloodtimer", features.setfloodtimer_cmd),
        ("clearflood", features.clearflood_cmd),
        ("addblocklist", features.add_blocklist_cmd),
        ("rmblocklist", features.rmblocklist_cmd),
        ("unblocklistall", features.unblocklistall_cmd),
        ("blocklist", features.blocklist_cmd),
        ("blocklistmode", features.blocklistmode_cmd),
        ("blocklistreason", features.blocklistreason_cmd),
        ("setblocklistreason", features.setblocklistreason_cmd),
        ("resetblocklistreason", features.resetblocklistreason_cmd),
        ("blocklistdelete", features.blocklistdelete_cmd),
        ("cleanservice", features.clean_service_cmd),
        ("keepservice", features.keep_service_cmd),
        ("nocleanservice", features.keep_service_cmd),
        ("cleanservicetypes", features.cleanservicetypes_cmd),
        ("setwelcome", features.set_welcome),
        ("goodbye", features.goodbye_toggle),
        ("setgoodbye", features.set_goodbye),
        ("resetgoodbye", features.reset_goodbye),
        ("cleanwelcome", features.cleanwelcome_cmd),
        ("clearcleft", features.clearcleft_cmd),
        ("afk", features.afk_cmd),
        ("id", features.id_cmd),
        ("info", features.info_cmd),
        ("adminlist", features.adminlist_cmd),
        ("admins", features.adminlist_cmd),
        ("ping", features.ping_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

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

    # ---- federations
    for cmd, fn in (
        ("newfed", feds.newfed), ("delfed", feds.delfed),
        ("fedinfo", feds.fedinfo), ("joinfed", feds.joinfed),
        ("joined", feds.joinfed),
        ("leavefed", feds.leavefed), ("fpromote", feds.fpromote),
        ("fdemote", feds.fdemote), ("fedadmins", feds.fedadmins),
        ("fban", feds.fban), ("unfban", feds.unfban),
        ("fbanlist", feds.fbanlist), ("fedchats", feds.fedchats),
        ("fedstat", feds.fedstat), ("gban", feds.gban),
        ("ungban", feds.ungban), ("gbanlist", feds.gbanlist),
        ("fedsubscribe", feds.fedsubscribe),
        ("subfed", feds.fedsubscribe),
        ("unfedsubscribe", feds.unfedsubscribe),
        ("unsubfed", feds.unfedsubscribe),
        ("fedpromote", feds.fpromote), ("feddemote", feds.fdemote),
        ("renamefed", feds.renamefed_cmd),
        ("fedtransfer", feds.fedtransfer_cmd),
        ("myfeds", feds.myfeds_cmd), ("chatfed", feds.fedinfo),
        ("fedexport", feds.fedexport_cmd), ("fedimport", feds.fedimport_cmd),
        ("setfedlog", feds.setfedlog_cmd), ("unsetfedlog", feds.unsetfedlog_cmd),
        ("fedreason", feds.fedreason_cmd), ("quietfed", feds.quietfed_cmd),
        ("feddemoteme", feds.feddemoteme_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- captcha & anti-raid
    for cmd, fn in (
        ("captcha", captcha.captcha_cmd),
        ("captchamode", captcha.captchamode_cmd),
        ("captchatime", captcha.captchatime_cmd),
        ("setcaptchatext", captcha.setcaptchatext_cmd),
        ("captchafile", captcha.captchafile_cmd),
        ("antiraid", captcha.antiraid_cmd),
        ("setraidthreshold", captcha.setraidthreshold_cmd),
        ("autoantiraid", captcha.autoantiraid_cmd),
        ("raidtime", captcha.raidtime_cmd),
        ("raidactiontime", captcha.raidactiontime_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))
    captcha.register(app)

    # ---- extra admin tools
    for cmd, fn in (
        ("promote", extras.promote), ("fullpromote", extras.fullpromote),
        ("demote", extras.demote), ("invite", extras.invite),
        ("users", extras.users_cmd), ("settitle", extras.set_title),
        ("setdesc", extras.set_desc), ("setgpic", extras.set_gpic),
        ("report", extras.report_cmd), ("reports", extras.reports_toggle),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- connections & backups
    for cmd, fn in (
        ("connect", utilities.connect_cmd),
        ("disconnect", utilities.disconnect_cmd),
        ("reconnect", utilities.reconnect_cmd),
        ("connection", utilities.connection_cmd),
        ("export", utilities.export_cmd),
        ("import", utilities.import_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- approvals
    for cmd, fn in (
        ("approve", approvals.approve_cmd),
        ("unapprove", approvals.unapprove_cmd),
        ("approved", approvals.approved_cmd),
        ("unapproveall", approvals.unapproveall_cmd),
        ("approval", approvals.approval_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- disabling module
    for cmd, fn in (
        ("disable", disabling.disable_cmd), ("enable", disabling.enable_cmd),
        ("disableable", disabling.disableable_cmd),
        ("disabled", disabling.disabled_cmd),
        ("disabledel", disabling.disabledel_cmd),
        ("disableadmin", disabling.disableadmin_cmd),
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- clean service enforcement (runs alongside welcome handlers)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS,
                                   features.enforce_clean_service), group=1)

    # ---- bonk aliases (UnkilBonker flavour)
    for cmd, fn in (
        ("bonk", moderation.ban),          # /bonk   == /ban
        ("unbonk", moderation.unban),      # /unbonk == /unban
        ("fbonk", feds.fban),               # /fbonk  == /fban
        ("unfbonki", feds.unfban),          # /unfbonki == /unfban
        ("bonki", moderation.warn),         # /bonki  == /warn
        ("unbonki", moderation.unwarn),     # /unbonki == /unwarn (one warn)
    ):
        app.add_handler(CommandHandler(cmd, C(fn)))

    # ---- quotly / memefi / tagall
    for cmd, fn in (
        ("q", quotly.quotly_cmd),
        ("mmf", memefi.memefi_cmd), ("memefi", memefi.memefi_cmd),
        ("tagall", tagall.tagall_cmd), ("all", tagall.tagall_cmd),
        ("utagall", tagall.utagall_cmd), ("uall", tagall.utagall_cmd),
        ("utag", tagall.utagall_cmd),
        ("cancel", tagall.tagall_stop), ("stoptag", tagall.tagall_stop),
    ):
        app.add_handler(CommandHandler(cmd, fn))
    tagall.register(app)

    # ---- ads remover
    app.add_handler(CommandHandler("adsremover", C(features.adsremover_cmd)))

    # ---- member tracking for tagall
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER,
                                   tagall.on_member_leave), group=1)

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
