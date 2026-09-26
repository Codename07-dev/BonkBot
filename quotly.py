"""Quotly-style: reply to any message with /q to get a quote sticker."""

import html
import io
import logging
import os
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import db  # noqa: F401  (kept so db init side effects run early)
from moderation import user_is_admin

log = logging.getLogger("unkilbonker.quotly")

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "fonts")
FONT_REG = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

CARD_BG = (24, 28, 38, 255)        # dark navy card
ACCENT = (88, 101, 242, 255)       # blurple


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    lines, line = [], ""
    for word in (text or "").split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


def _rounded_mask(size: tuple, radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius, fill=255)
    return m


def render_quote(text: str, author: str, date_str: str,
                 avatar: bytes | None = None, media: bytes | None = None,
                 accent: tuple = ACCENT) -> bytes:
    """Render a Quotly-style quote card and return PNG bytes."""
    S = 2  # supersample
    W = 900 * S
    PAD = 56 * S
    AVATAR = 120 * S
    inner = W - PAD * 2 - AVATAR - 40 * S

    scratch = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(scratch)

    name_font = _font(FONT_BOLD, 44 * S)
    text_font = _font(FONT_REG, 46 * S)
    date_font = _font(FONT_REG, 30 * S)

    lines = _wrap(d, text, text_font, inner if not media else inner)
    line_h = 62 * S
    text_h = len(lines) * line_h

    media_img = None
    media_h = 0
    if media:
        media_img = Image.open(io.BytesIO(media)).convert("RGB")
        mw = min(inner, W - PAD * 2)
        ratio = mw / media_img.width
        media_img = media_img.resize((mw, int(media_img.height * ratio)))
        media_h = media_img.height + 30 * S

    H = PAD + max(AVATAR, 60 * S) + 30 * S + text_h + media_h \
        + 70 * S + PAD  # avatar row + gap + text (+media) + date + bottom

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle([0, 0, W - 1, H - 1], 40 * S, fill=CARD_BG)
    # accent bar
    draw.rounded_rectangle([0, 0, 14 * S, H - 1], 7 * S, fill=accent)

    # avatar
    ax, ay = PAD, PAD + 10 * S
    if avatar:
        try:
            av = Image.open(io.BytesIO(avatar)).convert("RGB")
            mask = Image.new("L", av.size, 0)
            ImageDraw.Draw(mask).ellipse([0, 0, av.width - 1, av.height - 1],
                                         fill=255)
            av = av.resize((AVATAR, AVATAR)).convert("RGBA")
            mask = mask.resize((AVATAR, AVATAR))
            card.paste(av, (ax, ay), mask)
        except Exception:  # noqa: BLE001 - any avatar issue falls back
            _draw_initial(draw, ax, ay, AVATAR, author, accent)
    else:
        _draw_initial(draw, ax, ay, AVATAR, author, accent)

    # author name
    draw.text((ax + AVATAR + 40 * S, ay + 18 * S), author[:42],
              font=name_font, fill=(240, 242, 245, 255))

    # message text
    ty = PAD + max(AVATAR, 60 * S) + 30 * S
    for i, line in enumerate(lines[:28]):  # cap absurdly long messages
        draw.text((ax, ty + i * line_h), line, font=text_font,
                  fill=(220, 221, 225, 255))

    # optional embedded media (photo)
    if media_img is not None:
        my = ty + len(lines) * line_h + 20 * S
        mask = _rounded_mask(media_img.size, 24 * S)
        card.paste(media_img, (ax, my), mask)

    # date, bottom-right
    dw = draw.textlength(date_str, font=date_font)
    draw.text((W - PAD - dw, H - PAD - 26 * S), date_str,
              font=date_font, fill=(140, 145, 153, 255))

    # downscale for crispness
    card = card.resize((W // S, H // S), Image.LANCZOS)
    buf = io.BytesIO()
    card.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _draw_initial(draw, x, y, size, name, accent):
    draw.ellipse([x, y, x + size, y + size], fill=accent)
    initial = (name or "?").strip()[:1].upper() or "?"
    f = _font(FONT_BOLD, int(size * 0.42))
    w = draw.textlength(initial, font=f)
    bbox = draw.textbbox((0, 0), initial, font=f)
    draw.text((x + (size - w) / 2, y + (size - (bbox[3] - bbox[1])) / 2
              - bbox[1]), initial, font=f, fill=(255, 255, 255, 255))


def to_sticker_bytes(png: bytes) -> bytes:
    """Scale so the longer side is exactly 512px, encode as WEBP."""
    img = Image.open(io.BytesIO(png))
    if img.width >= img.height:
        nw = 512
        nh = max(1, round(img.height * 512 / img.width))
    else:
        nh = 512
        nw = max(1, round(img.width * 512 / img.height))
    img = img.resize((nw, nh), Image.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=92)
    return buf.getvalue()


async def quotly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to any message with /q to turn it into a quote sticker."""
    msg = update.effective_message
    source = msg.reply_to_message
    if not source or not source.from_user:
        await msg.reply_text("Reply to a message with /q to quote it. 🖼")
        return

    user = source.from_user
    text = source.text or source.caption or ""
    if not text and not source.photo:
        await msg.reply_text("I can quote text messages and photos. 🖼")
        return

    # color seed from the user id so everyone gets a consistent accent
    seeds = [ACCENT, (235, 69, 158, 255), (66, 181, 130, 255),
             (250, 166, 26, 255), (32, 177, 229, 255)]
    accent = seeds[user.id % len(seeds)]

    try:
        status = await msg.reply_text("🎨 Rendering your quote...")

        avatar = None
        try:
            photos = await context.bot.get_user_profile_photos(user.id,
                                                               limit=1)
            # NOTE: photos[0][0] is broken in PTB (raises
            # "attribute name must be string, not 'int'") - use .photos
            if photos.total_count > 0 and photos.photos:
                f = await photos.photos[0][0].get_file()
                avatar = bytes(await f.download_as_bytearray())
        except TelegramError:
            pass

        media = None
        if source.photo:
            f = await source.photo[-1].get_file()
            media = bytes(await f.download_as_bytearray())

        date_str = (source.date or
                    datetime.now(timezone.utc)).strftime("%d.%m.%Y %H:%M")
        png = render_quote(text, user.first_name or "?", date_str,
                           avatar=avatar, media=media, accent=accent)
        webp = to_sticker_bytes(png)

        await msg.reply_sticker(sticker=webp)
        try:
            await status.delete()
        except TelegramError:
            pass
    except TelegramError as e:
        await msg.reply_text(f"Couldn't render the quote: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("quote render failed")
        await msg.reply_text(f"Render error: {e}")
