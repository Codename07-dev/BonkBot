"""Quotly-style: reply to any message with /q to get a quote sticker.

Design (matches the classic Quotly look):
  dark rounded card -> circular avatar top-left -> bold name + muted
  @username -> optional "replying to" block with accent bar -> message text
  -> optional embedded photo -> small grey timestamp bottom-right.
"""

import io
import logging
import os
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

log = logging.getLogger("unkilbonker.quotly")

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "fonts")
FONT_REG = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

CARD_BG_TOP = (18, 20, 26)      # subtle vertical gradient
CARD_BG_BOTTOM = (30, 33, 42)
TEXT_MAIN = (238, 240, 243)
TEXT_MUTED = (124, 131, 141)
REPLY_ACCENT = (86, 156, 233)   # telegram blue
NAME_COLORS = [(86, 156, 233), (224, 99, 125), (98, 187, 122),
               (230, 170, 78), (167, 130, 224)]


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_width: int) -> list:
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


def _circle_avatar(avatar: bytes | None, size: int, name: str,
                   color: tuple) -> Image.Image:
    if avatar:
        try:
            av = Image.open(io.BytesIO(avatar)).convert("RGB")
            mask = Image.new("L", av.size, 0)
            ImageDraw.Draw(mask).ellipse([0, 0, av.width - 1, av.height - 1],
                                         fill=255)
            av = av.resize((size, size))
            out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            out.paste(av, (0, 0), mask.resize((size, size)))
            return out
        except Exception:  # noqa: BLE001
            pass
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).ellipse([0, 0, size - 1, size - 1], fill=color)
    initial = (name or "?").strip()[:1].upper() or "?"
    f = _font(FONT_BOLD, int(size * 0.42))
    d = ImageDraw.Draw(tile)
    w = d.textlength(initial, font=f)
    bbox = d.textbbox((0, 0), initial, font=f)
    d.text(((size - w) / 2, (size - (bbox[3] - bbox[1])) / 2 - bbox[1]),
           initial, font=f, fill=(255, 255, 255))
    return tile


def _rounded_mask(size: tuple, radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius, fill=255)
    return m


def render_quote(text: str, author: str, handle: str, date_str: str,
                 avatar: bytes | None = None, media: bytes | None = None,
                 reply_to: str = None, reply_snippet: str = None) -> bytes:
    """Render the Quotly-style card, return PNG bytes."""
    S = 2                       # supersampling
    W = 880 * S
    PAD = 54 * S
    AV = 108 * S
    GAP = 34 * S

    scratch = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(scratch)
    f_name = _font(FONT_BOLD, 42 * S)
    f_handle = _font(FONT_REG, 30 * S)
    f_text = _font(FONT_REG, 42 * S)
    f_small = _font(FONT_REG, 28 * S)
    f_date = _font(FONT_REG, 28 * S)

    name_color = NAME_COLORS[sum(author.encode()) % len(NAME_COLORS)]
    inner = W - PAD * 2

    # header block (avatar + name + handle)
    header_h = max(AV, 74 * S)

    # reply block
    reply_lines = []
    if reply_to:
        reply_lines = _wrap(d, (reply_snippet or "")[:80], f_small,
                            inner - 30 * S)[:2]

    # message text
    text_lines = _wrap(d, text, f_text, inner)[:24] if text else []
    line_h = 58 * S

    # media
    media_img = None
    media_h = 0
    if media:
        media_img = Image.open(io.BytesIO(media)).convert("RGB")
        mw = inner
        media_img = media_img.resize(
            (mw, max(1, int(media_img.height * mw / media_img.width))))
        media_h = media_img.height + 26 * S

    reply_h = (len(reply_lines) * 40 * S + 40 * S) if reply_lines else 0
    body_top = PAD + header_h + 30 * S
    text_h = len(text_lines) * line_h
    H = int(body_top + reply_h + text_h + media_h + 74 * S + PAD)

    # card + gradient
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / max(1, H - 1)
        grad.putpixel((0, y), tuple(
            int(a + (b - a) * t)
            for a, b in zip(CARD_BG_TOP, CARD_BG_BOTTOM)))
    card.paste(grad.resize((W, H)), (0, 0), _rounded_mask((W, H), 46 * S))
    draw = ImageDraw.Draw(card)

    # header
    card.paste(_circle_avatar(avatar, AV, author, name_color), (PAD, PAD))
    tx = PAD + AV + GAP
    draw.text((tx, PAD + 4 * S), author[:40], font=f_name,
              fill=tuple(name_color) + (255,))
    if handle:
        draw.text((tx, PAD + 56 * S), f"@{handle}"[:40], font=f_handle,
                  fill=TEXT_MUTED + (255,))

    # reply block
    y = body_top
    if reply_lines:
        draw.rounded_rectangle(
            [PAD, y, PAD + 7 * S, y + len(reply_lines) * 40 * S + 24 * S],
            4 * S, fill=REPLY_ACCENT + (255,))
        draw.text((PAD + 22 * S, y + 2 * S),
                  f"↩ {reply_to[:32]}", font=f_small,
                  fill=TEXT_MUTED + (255,))
        for i, ln in enumerate(reply_lines):
            draw.text((PAD + 22 * S, y + 36 * S + i * 40 * S), ln,
                      font=f_small, fill=TEXT_MUTED + (255,))
        y += reply_h

    # message text
    for i, ln in enumerate(text_lines):
        draw.text((PAD, y + i * line_h), ln, font=f_text,
                  fill=TEXT_MAIN + (255,))
    y += text_h

    # media
    if media_img is not None:
        card.paste(media_img, (PAD, y + 12 * S),
                   _rounded_mask(media_img.size, 22 * S))

    # timestamp bottom-right
    dw = draw.textlength(date_str, font=f_date)
    draw.text((W - PAD - dw, H - PAD - 30 * S), date_str, font=f_date,
              fill=TEXT_MUTED + (255,))

    card = card.resize((W // S, H // S), Image.LANCZOS)
    buf = io.BytesIO()
    card.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def to_sticker_bytes(png: bytes) -> bytes:
    """Scale so the longer side is exactly 512px, encode as WEBP."""
    img = Image.open(io.BytesIO(png))
    if img.width >= img.height:
        nw, nh = 512, max(1, round(img.height * 512 / img.width))
    else:
        nh, nw = 512, max(1, round(img.width * 512 / img.height))
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

        # reply context, like the classic Quotly card
        reply_to = reply_snippet = None
        r = source.reply_to_message
        if r and r.from_user:
            reply_to = r.from_user.first_name or "?"
            reply_snippet = (r.text or r.caption or "")[:80]

        date_str = (source.date or
                    datetime.now(timezone.utc)).strftime("%d.%m.%Y %H:%M")
        png = render_quote(text, user.first_name or "?",
                           user.username or "", date_str,
                           avatar=avatar, media=media,
                           reply_to=reply_to, reply_snippet=reply_snippet)
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
