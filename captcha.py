"""CAPTCHA join gates and anti-raid protection (Rose-style)."""

import html
import logging
import random
import string
import time
from collections import defaultdict, deque

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackContext, CallbackQueryHandler, ContextTypes

import db
from moderation import admin_gate, MUTED, UNMUTED, parse_duration, user_is_admin

log = logging.getLogger("unkilbonker.captcha")

WORDS = ("bonk", "unicorn", "rocket", "pickle", "samosa", "chai", "gulab",
         "jamun", "koala", "wombat", "chikki", "laddu")
RAID_WINDOW = 60  # seconds

# {(chat_id, user_id): {"answer": str, "msg_id": int}}
_pending: dict = {}
# chat_id -> deque of join timestamps
_joins: dict = defaultdict(deque)


def _is_active(update) -> bool:
    return db.get_setting(update.effective_chat.id, "captcha", "0") == "1"


# ---------------------------------------------------------------- commands

async def captcha_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    if args and args[0].lower() in ("on", "yes", "off", "no"):
        on = "1" if args[0].lower() in ("on", "yes") else "0"
        db.set_setting(chat_id, "captcha", on)
        await update.effective_message.reply_text(
            f"🤖 CAPTCHA is now {'ON' if on == '1' else 'OFF'} - new members "
            "must verify before they can talk.")
        return
    cur = db.get_setting(chat_id, "captcha", "0")
    mode = db.get_setting(chat_id, "captcha_mode", "button")
    await update.effective_message.reply_text(
        f"🤖 CAPTCHA: {'ON' if cur == '1' else 'OFF'} (mode {mode})\n"
        "Usage: /captcha on|off")


async def captchamode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    args = context.args or []
    if not args or args[0].lower() not in ("button", "math", "text"):
        await update.effective_message.reply_text(
            "Usage: /captchamode <button/math/text>\n"
            "• button - tap a button\n• math - solve a sum\n"
            "• text - type the given word")
        return
    db.set_setting(update.effective_chat.id, "captcha_mode", args[0].lower())
    await update.effective_message.reply_text(f"✅ CAPTCHA mode: {args[0].lower()}")


async def captchatime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    from moderation import parse_duration
    args = context.args or []
    if not args or not parse_duration(args[0]):
        cur = db.get_setting(update.effective_chat.id, "captcha_time", 300)
        await update.effective_message.reply_text(
            f"Current CAPTCHA time limit: {cur}s\n"
            "Usage: /captchatime 2m")
        return
    db.set_setting(update.effective_chat.id, "captcha_time",
                   parse_duration(args[0]))
    await update.effective_message.reply_text(
        f"✅ CAPTCHA time limit: {args[0]} (kick after that).")


async def setcaptchatext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    text = " ".join(context.args or []).strip()
    if not text:
        cur = db.get_setting(update.effective_chat.id, "captcha_text",
                             "Tap the button to prove you're human.")
        await update.effective_message.reply_text(
            f"Current CAPTCHA text: {cur}\nUsage: /setcaptchatext <text>")
        return
    db.set_setting(update.effective_chat.id, "captcha_text", text)
    await update.effective_message.reply_text("✅ CAPTCHA text set.")


async def captchafile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    source = update.effective_message.reply_to_message
    if not source or not source.photo:
        await update.effective_message.reply_text(
            "Reply to a photo with /captchafile to use it in the CAPTCHA.")
        return
    db.set_setting(update.effective_chat.id, "captcha_file",
                   source.photo[-1].file_id)
    await update.effective_message.reply_text(
        "✅ CAPTCHA photo set (reply /captchafile to a new photo to change).")


# ------------------------------------------------------------ anti-raid

async def antiraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/antiraid [time|off] - tempban new joiners while active."""
    if not await admin_gate(update, context):
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    import time as _t
    if args and args[0].lower() not in ("on", "off", "no", "yes"):
        seconds = parse_duration(args[0])
        if not seconds:
            await update.effective_message.reply_text(
                "Usage: /antiraid [time] | /antiraid off\nExample: /antiraid 3h")
            return
        db.set_setting(chat_id, "antiraid", "1")
        db.set_setting(chat_id, "raid_until", str(_t.time() + seconds))
        await update.effective_message.reply_text(
            f"🛡 Anti-raid ON for {args[0]} - new joiners will be "
            "temporarily banned.")
        return
    if args and args[0].lower() in ("off", "no"):
        db.set_setting(chat_id, "antiraid", "0")
        db.set_setting(chat_id, "raid_until", 0)
        await update.effective_message.reply_text("🛡 Anti-raid is OFF.")
        return
    if args and args[0].lower() in ("on", "yes"):
        db.set_setting(chat_id, "antiraid", "1")
        await update.effective_message.reply_text(
            "🛡 Anti-raid is ON - new joiners will be temporarily banned.")
        return
    cur = db.get_setting(chat_id, "antiraid", "0")
    thr = db.get_setting(chat_id, "raid_threshold", 8)
    await update.effective_message.reply_text(
        f"🛡 Anti-raid: {'ACTIVE' if cur == '1' else 'off'} "
        f"(auto-triggers above {thr} joins/min)\n"
        "Usage: /antiraid [time] | /antiraid off")


async def setraidthreshold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    args = context.args or []
    if not args or not args[0].isdigit() or not (2 <= int(args[0]) <= 100):
        cur = db.get_setting(update.effective_chat.id, "raid_threshold", 8)
        await update.effective_message.reply_text(
            f"Current raid threshold: {cur} joins/minute\n"
            "Usage: /setraidthreshold <number>")
        return
    db.set_setting(update.effective_chat.id, "raid_threshold", int(args[0]))
    await update.effective_message.reply_text(
        f"✅ Raid threshold: {args[0]} joins/minute auto-enables anti-raid.")


# ------------------------------------------------------- join handling

async def on_member_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat

    # raid detection bookkeeping
    now = time.monotonic()
    stamps = _joins[chat.id]
    while stamps and now - stamps[0] > RAID_WINDOW:
        stamps.popleft()
    stamps.append(now)
    threshold = int(db.get_setting(chat.id, "raid_threshold", 8))
    if len(stamps) >= threshold and db.get_setting(chat.id, "antiraid", "0") != "1":
        db.set_setting(chat.id, "antiraid", "1")
        stamps.clear()
        try:
            await msg.reply_text(
                f"🛡 <b>Raid detected</b> ({threshold}+ joins/min) - anti-raid "
                "AUTO-ENABLED. New joiners will be kicked. "
                "Use /antiraid off to disable.",
                parse_mode=ParseMode.HTML)
        except TelegramError:
            pass

    import time as _t
    if db.get_setting(chat.id, "antiraid", "0") == "1":
        until = db.get_setting(chat.id, "raid_until", 0)
        try:
            if until and _t.time() > float(until):
                db.set_setting(chat.id, "antiraid", "0")
                db.set_setting(chat.id, "raid_until", 0)
            else:
                action = int(db.get_setting(chat.id, "raidactiontime", 3600))
                for user in (msg.new_chat_members or []):
                    if user.id != context.bot.id:
                        try:
                            await context.bot.ban_chat_member(
                                chat.id, user.id, until_date=_t.time() + action)
                        except TelegramError:
                            pass
                return
        except (TypeError, ValueError):
            pass

    for user in msg.new_chat_members or []:
        if user.id == context.bot.id:
            continue
        if db.get_setting(chat.id, "antiraid", "0") == "1":
            await _kick_quietly(chat, user.id)
            continue
        if db.get_setting(chat.id, "captcha", "0") == "1":
            await _start_captcha(update, context, user)


async def _kick_quietly(chat, user_id: int) -> None:
    try:
        await chat.ban_member(user_id)
        await chat.unban_member(user_id, only_if_banned=True)
    except TelegramError:
        pass


async def _start_captcha(update, context, user) -> None:
    chat = update.effective_chat
    chat_id, uid = chat.id, user.id
    mode = db.get_setting(chat_id, "captcha_mode", "button")
    custom = db.get_setting(chat_id, "captcha_text",
                            "Tap the button to prove you're human.")
    timeout = int(db.get_setting(chat_id, "captcha_time", 300))

    try:
        await chat.restrict_member(uid, MUTED)
    except TelegramError as e:
        log.warning("captcha mute failed: %s", e)
        return

    name = html.escape(user.first_name or "friend")
    answer = None
    keyboard = None
    prompt = {"button": "Tap the button below to verify.",
              "math": "Solve this to verify:",
              "text": "Type this word to verify:"}[mode]
    if mode == "math":
        a, b = random.randint(3, 12), random.randint(3, 12)
        answer = str(a + b)
        prompt = f"Solve this to verify: <b>{a} + {b} = ?</b>"
    elif mode == "text":
        answer = random.choice(WORDS)
        prompt = f"Type this word to verify: <b>{answer}</b>"
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ I'm human",
                                 callback_data=f"cap:{chat_id}:{uid}")
        ]])

    # optional custom photo
    file_id = db.get_setting(chat_id, "captcha_file")
    sent = None
    try:
        if file_id:
            await context.bot.send_photo(chat_id, file_id)
        sent = await update.effective_message.reply_text(
            f"🤖 <b>Welcome, {name}!</b>\n{html.escape(custom)}\n\n{prompt}\n"
            f"⏱️ You have <b>{timeout // 60} minute(s)</b>.",
            parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except TelegramError:
        return

    _pending[(chat_id, uid)] = {"answer": answer, "msg_id": sent.message_id}
    if context.job_queue:
        context.job_queue.run_once(
            captcha_timeout, timeout, data={"chat_id": chat_id, "user_id": uid,
                                            "name": user.first_name or "?"})


async def captcha_timeout(context: CallbackContext) -> None:
    d = context.job.data
    key = (d["chat_id"], d["user_id"])
    if key not in _pending:
        return
    _pending.pop(key, None)
    try:
        await context.bot.ban_chat_member(d["chat_id"], d["user_id"])
        await context.bot.unban_chat_member(d["chat_id"], d["user_id"],
                                            only_if_banned=True)
        await context.bot.send_message(
            d["chat_id"],
            f"🤖 {html.escape(d['name'])} didn't verify in time - kicked.",
            parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


async def on_captcha_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, chat_id, uid = query.data.split(":")
        chat_id, uid = int(chat_id), int(uid)
    except ValueError:
        return
    if query.from_user.id != uid:
        await query.answer("This button isn't for you!", show_alert=True)
        return
    key = (chat_id, uid)
    if key not in _pending:
        await query.answer("Already verified or expired.")
        return
    _pending.pop(key, None)
    await query.answer("✅ Verified!")
    await _finish_verification(update, context, chat_id, uid, query.message)


async def _finish_verification(update, context, chat_id, uid, captcha_msg) -> None:
    try:
        await context.bot.restrict_chat_member(chat_id, uid, UNMUTED)
    except TelegramError:
        pass
    try:
        await captcha_msg.delete()
    except TelegramError:
        pass
    name = update.effective_user.mention_html() if update.effective_user else "User"
    try:
        await context.bot.send_message(
            chat_id, f"✅ {name} is verified - welcome!",
            parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


async def on_captcha_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Math/text CAPTCHA: check typed answers in the group."""
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    key = (update.effective_chat.id, user.id)
    entry = _pending.get(key)
    if not entry or not entry.get("answer"):
        return
    if (msg.text or "").strip() != entry["answer"]:
        return  # wrong or unrelated message - keep waiting
    _pending.pop(key, None)
    await _finish_verification(update, context, key[0], key[1], msg)


# ---------------------------------------------------------- registration

def register(app) -> None:
    from telegram.ext import MessageHandler, filters
    app.add_handler(CallbackQueryHandler(on_captcha_button, pattern=r"^cap:"))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
        on_captcha_answer), group=1)


async def raidtime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    from moderation import parse_duration, fmt_duration
    args = context.args or []
    if args and parse_duration(args[0]):
        db.set_setting(update.effective_chat.id, "raidtime",
                       parse_duration(args[0]))
        await update.effective_message.reply_text(
            f"✅ Anti-raid duration: {args[0]} when enabled with a time.")
    else:
        cur = db.get_setting(update.effective_chat.id, "raidtime", 21600)
        await update.effective_message.reply_text(
            "Anti-raid duration: " + fmt_duration(int(cur))
            + "\nUsage: /raidtime 6h")


async def raidactiontime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    from moderation import parse_duration, fmt_duration
    args = context.args or []
    if args and parse_duration(args[0]):
        db.set_setting(update.effective_chat.id, "raidactiontime",
                       parse_duration(args[0]))
        await update.effective_message.reply_text(
            f"✅ Joiner tempban time: {args[0]}.")
    else:
        cur = db.get_setting(update.effective_chat.id, "raidactiontime", 3600)
        await update.effective_message.reply_text(
            "Joiner tempban time: " + fmt_duration(int(cur))
            + "\nUsage: /raidactiontime 1h")


async def autoantiraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await admin_gate(update, context):
        return
    args = context.args or []
    if args and args[0].lower() in ("off", "no", "0"):
        db.set_setting(update.effective_chat.id, "raid_threshold", 0)
        await update.effective_message.reply_text("Auto anti-raid disabled.")
        return
    if args and args[0].isdigit() and int(args[0]) >= 2:
        db.set_setting(update.effective_chat.id, "raid_threshold", int(args[0]))
        await update.effective_message.reply_text(
            f"✅ Auto anti-raid: {args[0]} joins/minute.")
        return
    cur = db.get_setting(update.effective_chat.id, "raid_threshold", 8)
    await update.effective_message.reply_text(
        f"Auto anti-raid threshold: {cur} joins/min\n"
        "Usage: /autoantiraid <number|off>")
