"""Sticker kanging - the Stickerkang-style feature set.

Three things make kang fail on real Telegram, all fixed here:
  1. A pack can only hold ONE format (static/video/animated) - each format
     gets its own pack name.
  2. A pack holds max 120 stickers - we roll over to pack_2, pack_3, ...
  3. 'Sticker set invalid' vs 'already occupied' races on first kang -
     both are handled with retry paths.
Errors are always reported to the user verbatim so problems are debuggable.
"""

import logging
from io import BytesIO

from PIL import Image
from telegram import InputSticker, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

import db

log = logging.getLogger("unkilbonker.stickers")

DEFAULT_EMOJI = "🤔"
MAX_PACKS = 10  # pack, pack_2, ... pack_10

FMT_SUFFIX = {"static": "", "video": "v", "animated": "a"}


def pack_name(user_id: int, bot_username: str, fmt: str = "static",
              index: int = 0) -> str:
    suffix = FMT_SUFFIX.get(fmt, "")
    base = f"unkilbonker{suffix}_{user_id}_by_{bot_username}"
    if index:
        base += f"_{index}"
    return base.lower()[:64]


async def _photo_png(file_id: str, bot) -> bytes:
    """Download a photo and convert it into a sticker-sized PNG."""
    tg_file = await bot.get_file(file_id)
    data = await tg_file.download_as_bytearray()
    img = Image.open(BytesIO(bytes(data))).convert("RGBA")

    # Scale so the longest side is 512px, pad to a square.
    w, h = img.size
    scale = 512 / max(w, h)
    img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                     Image.LANCZOS)
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.paste(img, ((512 - img.width) // 2, (512 - img.height) // 2))
    buf = BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()


def _sticker_format(sticker) -> str:
    if sticker.is_video:
        return "video"
    if sticker.is_animated:
        return "animated"
    return "static"


def _err_text(e: Exception) -> str:
    return str(e).lower()


async def _add_to(bot, user_id: int, name: str, sticker: InputSticker) -> str:
    """Try adding to a set. Returns 'added' | 'missing' | 'full' | error str."""
    try:
        await bot.add_sticker_to_set(user_id, name, sticker)
        return "added"
    except BadRequest as e:
        t = _err_text(e)
        if "too much" in t or "too many" in t or "full" in t:
            return "full"
        if "not found" in t or "invalid" in t:
            return "missing"
        return f"error:{e}"
    except TelegramError as e:
        return f"error:{e}"


async def _create(bot, user_id: int, name: str, title: str,
                  sticker: InputSticker) -> str:
    """Create a fresh set. Returns 'added' | 'occupied' | error str."""
    try:
        await bot.create_new_sticker_set(
            user_id=user_id, name=name, title=title, stickers=[sticker])
        return "added"
    except BadRequest as e:
        if "occupied" in _err_text(e):
            return "occupied"
        return f"error:{e}"
    except TelegramError as e:
        return f"error:{e}"


async def kang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kang [emojis] - reply to a sticker or photo to add it to your pack."""
    msg = update.effective_message
    user = update.effective_user
    if not user:
        return

    source = msg.reply_to_message or msg
    bot_username = context.bot.username

    emojis = [a for a in (context.args or [])
              if not any(c.isascii() for c in a) and len(a) <= 8]
    if not emojis:
        emojis = [DEFAULT_EMOJI]

    # Work out what we're kanging.
    fmt = "static"
    if source.sticker:
        st = source.sticker
        fmt = _sticker_format(st)
        if emojis == [DEFAULT_EMOJI]:
            base_emoji = [c for c in (st.emoji or "") if not c.isascii()]
            emojis = base_emoji or [DEFAULT_EMOJI]
        sticker_input = InputSticker(sticker=st.file_id, emoji_list=emojis,
                                     format=fmt)
    elif source.photo:
        try:
            png = await _photo_png(source.photo[-1].file_id, context.bot)
        except (TelegramError, Exception) as e:  # noqa: BLE001
            await msg.reply_text(f"Couldn't download that photo: {e}")
            return
        sticker_input = InputSticker(sticker=png, emoji_list=emojis,
                                     format="static")
    else:
        await msg.reply_text(
            "Reply to a sticker or a photo with /kang to steal it "
            "into your own pack. 🦘")
        return

    title = (f"@{user.username} UnkilBonked" if user.username
             else f"{user.first_name or 'My'}'s pack")

    # Walk through pack indices: existing packs first, then fresh ones.
    for i in range(MAX_PACKS):
        name = pack_name(user.id, bot_username, fmt, i)
        res = await _add_to(context.bot, user.id, name, sticker_input)
        if res == "added":
            db.save_pack(user.id, fmt, name, title)
            await msg.reply_text(
                f"🦘 <b>Kanged!</b> "
                f"<code>t.me/addstickers/{name}</code>",
                parse_mode=ParseMode.HTML)
            return
        if res == "missing":
            # Set doesn't exist - create it, then we're done.
            res2 = await _create(context.bot, user.id, name, title,
                                 sticker_input)
            if res2 == "added":
                db.save_pack(user.id, fmt, name, title)
                await msg.reply_text(
                    f"🦘 <b>New pack created!</b> "
                    f"<code>t.me/addstickers/{name}</code>",
                    parse_mode=ParseMode.HTML)
                return
            if res2 == "occupied":
                # Set exists on Telegram but add failed - retry once.
                res3 = await _add_to(context.bot, user.id, name, sticker_input)
                if res3 == "added":
                    db.save_pack(user.id, fmt, name, title)
                    await msg.reply_text(
                        f"🦘 <b>Kanged!</b> "
                        f"<code>t.me/addstickers/{name}</code>",
                        parse_mode=ParseMode.HTML)
                    return
                await msg.reply_text(
                    f"Couldn't kang to <code>{name}</code>: {res3}\n"
                    "Tip: open t.me/addstickers/" + name + " and make sure "
                    "the bot isn't blocked.", parse_mode=ParseMode.HTML)
                return
            await msg.reply_text(f"Couldn't create your pack: {res2}")
            return
        if res == "full":
            continue  # next index
        # any other error - report it verbatim so it's debuggable
        await msg.reply_text(
            f"Couldn't kang: {res.split(':', 1)[-1]}\n"
            f"(pack <code>{name}</code>)", parse_mode=ParseMode.HTML)
        return

    await msg.reply_text(
        f"All {MAX_PACKS} of your {'video ' if fmt == 'video' else ''}"
        "packs are full! Delete one at t.me/addstickers/... "
        "or use /getsticker to save it as a file.")


async def packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target = update.effective_message.reply_to_message.from_user \
        if update.effective_message.reply_to_message else user
    packs = db.get_packs(target.id)
    if not packs:
        await update.effective_message.reply_text(
            "No pack yet - reply to a sticker with /kang to start one! 🦘")
        return
    lines = []
    for fmt, name, title in packs:
        count = "?"
        try:
            st_set = await context.bot.get_sticker_set(name)
            count = len(st_set.stickers)
        except TelegramError:
            pass
        icon = {"static": "🖼", "video": "🎬", "animated": "✨"}.get(fmt, "📦")
        lines.append(f"{icon} <b>{title}</b> ({fmt}) - {count} stickers\n"
                     f"t.me/addstickers/{name}")
    await update.effective_message.reply_text(
        "🦘 <b>Packs:</b>\n\n" + "\n\n".join(lines), parse_mode=ParseMode.HTML)


async def getsticker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to a sticker to receive the raw file as a document."""
    msg = update.effective_message
    source = msg.reply_to_message or msg
    st = source.sticker
    if not st:
        await msg.reply_text("Reply to a sticker with /getsticker to get its file.")
        return
    ext = "webm" if st.is_video else ("tgs" if st.is_animated else "webp")
    try:
        tg_file = await st.get_file()
        data = await tg_file.download_as_bytearray()
        await msg.reply_document(
            document=bytes(data),
            filename=f"sticker.{ext}",
            caption=f"🦘 <code>{st.file_id}</code>",
            parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await msg.reply_text(f"Couldn't fetch that sticker: {e}")
