"""Sticker kanging - the Stickerkang-style feature set."""

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


def pack_name(user_id: int, bot_username: str) -> str:
    return f"unkilbonker_{user_id}_by_{bot_username}".lower()[:64]


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
    if source.sticker:
        st = source.sticker
        fmt = _sticker_format(st)
        if not emojis or emojis == [DEFAULT_EMOJI]:
            emojis = list(st.emoji or DEFAULT_EMOJI)
        sticker_input = InputSticker(sticker=st.file_id, emoji_list=emojis,
                                     format=fmt)
    elif source.photo:
        try:
            png = await _photo_png(source.photo[-1].file_id, context.bot)
        except TelegramError as e:
            await msg.reply_text(f"Couldn't download that photo: {e}")
            return
        sticker_input = InputSticker(sticker=png, emoji_list=emojis,
                                     format="static")
    else:
        await msg.reply_text(
            "Reply to a sticker or a photo with /kang to steal it "
            "into your own pack. 🦘")
        return

    name = pack_name(user.id, bot_username)
    title = f"@{user.username} UnkilBonked" if user.username \
        else f"{user.first_name}'s pack"

    # Add to the existing pack, or create it on first kang.
    try:
        await context.bot.add_sticker_to_set(user.id, name, sticker_input)
        db.save_pack(user.id, name, title)
        await msg.reply_text(
            "🦘 <b>Kanged!</b> Check your pack: "
            f"<code>t.me/addstickers/{name}</code>",
            parse_mode=ParseMode.HTML)
    except BadRequest as e:
        err = str(e).lower()
        if "not found" in err or "invalid" in err:
            try:
                await context.bot.create_new_sticker_set(
                    user_id=user.id, name=name, title=title,
                    stickers=[sticker_input])
                db.save_pack(user.id, name, title)
                await msg.reply_text(
                    "🦘 <b>New pack created</b> and sticker added: "
                    f"<code>t.me/addstickers/{name}</code>",
                    parse_mode=ParseMode.HTML)
            except BadRequest as e2:
                if "occupied" in str(e2).lower():
                    # Pack exists but we don't have it cached - retry adding.
                    try:
                        await context.bot.add_sticker_to_set(user.id, name,
                                                            sticker_input)
                        db.save_pack(user.id, name, title)
                        await msg.reply_text(
                            f"🦘 Kanged! <code>t.me/addstickers/{name}</code>",
                            parse_mode=ParseMode.HTML)
                        return
                    except BadRequest:
                        pass
                await msg.reply_text(f"Couldn't kang: {e2}")
        else:
            await msg.reply_text(f"Couldn't kang: {e}")
    except TelegramError as e:
        await msg.reply_text(f"Couldn't kang: {e}")


async def packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.id != update.effective_user.id:  # reply support
        pass
    target = update.effective_message.reply_to_message.from_user \
        if update.effective_message.reply_to_message else user
    pack = db.get_pack(target.id)
    if not pack:
        await update.effective_message.reply_text(
            "No pack yet - send me a sticker with /kang to start one! 🦘")
        return
    name, title = pack
    count = "?"
    try:
        st_set = await context.bot.get_sticker_set(name)
        count = len(st_set.stickers)
    except TelegramError:
        pass
    await update.effective_message.reply_text(
        f"📦 <b>{title}</b>\nStickers: {count}\n"
        f"Link: t.me/addstickers/{name}",
        parse_mode=ParseMode.HTML)


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
