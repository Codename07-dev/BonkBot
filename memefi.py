"""MemeFi - add classic meme text (top/center/bottom) to any sticker."""

import html
import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

log = logging.getLogger("unkilbonker.memefi")

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")


def _font(size: int):
    try:
        return ImageFont.truetype(FONT_BOLD, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_width: int) -> list:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


def _draw_block(draw, lines, font, fill_y, img_w):
    """Draw centered lines above/below the given y boundary."""
    line_h = font.size * 1.15
    total = len(lines) * line_h
    y = fill_y - total if fill_y < 0 else fill_y
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        x = (img_w - w) / 2
        ly = y + i * line_h
        draw.text((x + 3, ly + 3), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, ly), line, font=font, fill=(255, 255, 255, 255))


def render_meme(image_bytes: bytes, top: str = "", center: str = "",
                bottom: str = "") -> bytes:
    """Return a 512x512 WEBP sticker with classic meme text bands."""
    W = 512
    font_size = max(26, W // 12)
    scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    f = _font(font_size)

    top_lines = _wrap(scratch, top.upper(), f, W - 40)[:3]
    bottom_lines = _wrap(scratch, bottom.upper(), f, W - 40)[:3]
    center_lines = _wrap(scratch, center.upper(), f, W - 40)[:6]
    band = font_size * 1.15

    top_h = int(len(top_lines) * band + 12) if top else 0
    bottom_h = int(len(bottom_lines) * band + 12) if bottom else 0
    content_h = W - top_h - bottom_h

    canvas = Image.new("RGBA", (W, W), (0, 0, 0, 255))
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    # scale the base image into the content area, keep aspect
    ratio = min(W / img.width, content_h / img.height)
    img = img.resize((max(1, round(img.width * ratio)),
                      max(1, round(img.height * ratio))), Image.LANCZOS)
    canvas.paste(img, ((W - img.width) // 2,
                       top_h + (content_h - img.height) // 2), img)
    draw = ImageDraw.Draw(canvas)

    if top:
        _draw_block(draw, top_lines, f, 4, W)
    if bottom:
        _draw_block(draw, bottom_lines, f, W - 4 - len(bottom_lines) * band, W)
    if center:
        _draw_block(draw, center_lines, f,
                    top_h + (content_h - len(center_lines) * band) / 2, W)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "WEBP", quality=92)
    return buf.getvalue()


def _parse_args(args: list) -> tuple:
    """Return (top, center, bottom) from /mmf arguments."""
    top = center = bottom = ""
    rest = list(args)
    if rest and rest[0] in ("-c", "-center"):
        rest.pop(0)
        center = " ".join(rest)
        return "", center, ""
    text = " ".join(rest)
    if ";" in text:
        parts = text.split(";")
        top = parts[0].strip()
        bottom = parts[1].strip() if len(parts) > 1 else ""
        if len(parts) > 2 and parts[2].strip():
            center = parts[2].strip()
    elif text:
        top = text.strip()
    return top, center, bottom


async def memefi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    source = msg.reply_to_message
    if not source or not (source.sticker or source.photo):
        await msg.reply_text(
            "Reply to a sticker or photo with /mmf Your Text\n\n"
            "• /mmf Top Text\n• /mmf Top ; Bottom\n• /mmf -c Center Text")
        return

    top, center, bottom = _parse_args(context.args or [])
    if not (top or center or bottom):
        await msg.reply_text("Give me some text! e.g. /mmf Bonk ; Ultra")
        return

    try:
        status = await msg.reply_text("🎨 Adding your text...")
        if source.sticker:
            f = await source.sticker.get_file()
        else:
            f = await source.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        out = render_meme(data, top, center, bottom)

        if source.sticker and not source.sticker.is_animated \
                and not source.sticker.is_video:
            await msg.reply_sticker(sticker=out)
        else:
            await msg.reply_photo(photo=out, caption=None)
        try:
            await status.delete()
        except TelegramError:
            pass
    except Exception as e:  # noqa: BLE001
        log.exception("memefi render failed")
        await msg.reply_text(f"Couldn't render: {e}")
