import asyncio, io, os
from unittest.mock import AsyncMock, MagicMock
os.environ["DB_PATH"] = "/tmp/test_ub2.db"
if os.path.exists("/tmp/test_ub2.db"): os.remove("/tmp/test_ub2.db")
import db; db.init_db()
import features, moderation, stickers

ADMIN_ID = 555
ADMIN_MEMBER = type("M", (), {"user": type("U", (), {"id": ADMIN_ID})(),
                              "status": "administrator"})()
BOT_MEMBER = type("M", (), {"user": type("U", (), {"id": 999})(),
                            "status": "administrator", "can_restrict_members": True})()

def mk_update(chat_id=-100123, user_id=555, reply=None):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.effective_chat.type = "supergroup"
    upd.effective_chat.title = "TestGroup"
    upd.effective_chat.get_member_count = AsyncMock(return_value=100)
    upd.effective_chat.get_member = AsyncMock(return_value=BOT_MEMBER)
    upd.effective_chat.ban_member = AsyncMock()
    upd.effective_chat.unban_member = AsyncMock()
    upd.effective_chat.restrict_member = AsyncMock()
    upd.effective_user.id = user_id
    upd.effective_user.first_name = "Tester"
    upd.effective_user.username = "tester"
    upd.effective_user.is_bot = False
    upd.effective_user.mention_html.return_value = "<a>Tester</a>"
    msg = MagicMock()
    msg.text = ""
    msg.chat = upd.effective_chat
    msg.from_user = upd.effective_user
    msg.entities = []
    msg.caption_entities = []
    msg.reply_to_message = reply if reply is not None else MagicMock()
    msg.reply_text = AsyncMock()
    msg.reply_document = AsyncMock()
    msg.delete = AsyncMock()
    msg.pin = AsyncMock()
    upd.effective_message = msg
    return upd, msg

ctx = MagicMock()
ctx.bot.username = "UnkilBonkerBot"
ctx.bot.id = 999
ctx.bot.get_chat_administrators = AsyncMock(return_value=[ADMIN_MEMBER])
ctx.bot_data = {}
ctx.job_queue = MagicMock()
ctx.job_queue.run_once = MagicMock()

reply = MagicMock()
reply.from_user = type("U", (), {"id": 777, "first_name": "Spammer", "username": "spam"})()

# ---- warn flow
upd, msg = mk_update(reply=reply); ctx.args = ["being rude"]
asyncio.run(moderation.warn(upd, ctx))
assert "1/3" in msg.reply_text.call_args[0][0], msg.reply_text.call_args
ctx.args = []
asyncio.run(moderation.warn(upd, ctx)); asyncio.run(moderation.warn(upd, ctx))
assert upd.effective_chat.ban_member.await_count == 1
assert "banned" in msg.reply_text.call_args[0][0].lower()
assert db.get_warns(-100123, 777) == (0, [])
print("PASS warn")

# ---- tban
upd, msg = mk_update(reply=reply); ctx.args = ["30m"]
asyncio.run(moderation.tban(upd, ctx))
assert upd.effective_chat.ban_member.await_count == 1 and ctx.job_queue.run_once.called
assert "30m" in msg.reply_text.call_args[0][0]
print("PASS tban")

# ---- ban with reason
upd, msg = mk_update(reply=reply); ctx.args = ["toxic behaviour"]
asyncio.run(moderation.ban(upd, ctx))
assert "toxic behaviour" in msg.reply_text.call_args[0][0]
print("PASS ban")

# ---- mute
upd, msg = mk_update(reply=reply); ctx.args = []
asyncio.run(moderation.mute(upd, ctx))
assert upd.effective_chat.restrict_member.await_count == 1
print("PASS mute")

# ---- purge
ids = []
async def fake_del(cid, chunk): ids.extend(chunk)
ctx.bot.delete_messages = fake_del
upd, msg = mk_update()
msg.reply_to_message.message_id = 100
msg.message_id = 130
asyncio.run(moderation.purge(upd, ctx))
assert len(ids) == 31, len(ids)
print("PASS purge")

# ---- welcome render
user = type("U", (), {"first_name": "Asha", "last_name": "P", "username": "asha",
                      "mention_html": lambda s: "<a>Asha</a>"})()
out = features.render_welcome(features.WELCOME_DEFAULT, user,
                              type("C", (), {"title": "Bonki Group"})(), 100)
assert "Asha" in out and "Bonki Group" in out and "100" in out, out
print("PASS welcome render")

# ---- locks enforcement
db.set_locks(-100123, {"stickers"})
upd, msg = mk_update(user_id=777)
msg.sticker = True
stopped = False
try:
    asyncio.run(features.enforce_locks(upd, ctx))
except Exception:
    stopped = True
assert msg.delete.await_count == 1 and stopped
upd, msg = mk_update(user_id=ADMIN_ID)
msg.sticker = True
asyncio.run(features.enforce_locks(upd, ctx))
assert msg.delete.await_count == 0
db.set_locks(-100123, set())
print("PASS locks")

# ---- antiflood
db.set_setting(-100123, "antiflood", "3")
upd, msg = mk_update(user_id=777)
for _ in range(4):
    asyncio.run(features.check_flood(upd, ctx))
assert upd.effective_chat.ban_member.await_count == 1
db.set_setting(-100123, "antiflood", "0")
print("PASS antiflood")

# ---- afk
upd, msg = mk_update(reply=None); ctx.args = ["lunch"]
asyncio.run(features.afk_cmd(upd, ctx))
assert "lunch" in msg.reply_text.call_args[0][0]
upd2, msg2 = mk_update(user_id=888)
msg2.reply_to_message = MagicMock()
msg2.reply_to_message.from_user = upd.effective_user
asyncio.run(features.afk_watch(upd2, ctx))
assert "AFK" in msg2.reply_text.call_args[0][0]
upd3, msg3 = mk_update()
asyncio.run(features.afk_watch(upd3, ctx))
assert "Welcome back" in msg3.reply_text.call_args[0][0]
print("PASS afk")

# ---- kang
from PIL import Image
from telegram import InputSticker
from telegram.error import BadRequest
img = Image.new("RGB", (1024, 512), "red")
buf = io.BytesIO()
img.save(buf, "JPEG")
tgfile = MagicMock()
tgfile.download_as_bytearray = AsyncMock(return_value=buf.getvalue())
ctx.bot.get_file = AsyncMock(return_value=tgfile)
png = asyncio.run(stickers._photo_png("fakeid", ctx.bot))
assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 100
print("PASS photo->png")

st = MagicMock()
st.file_id = "FILE123"
st.is_video = False
st.is_animated = False
st.emoji = "🔥"
upd, msg = mk_update()
msg.reply_to_message.sticker = st
msg.reply_to_message.photo = None
ctx.bot.add_sticker_to_set = AsyncMock(side_effect=BadRequest("Sticker set not found"))
ctx.bot.create_new_sticker_set = AsyncMock(return_value=True)
ctx.args = []
asyncio.run(stickers.kang(upd, ctx))
kw = ctx.bot.create_new_sticker_set.call_args.kwargs
assert kw["user_id"] == 555 and kw["name"] == "unkilbonker_555_by_unkilbonkerbot"
ist = kw["stickers"][0]
assert isinstance(ist, InputSticker) and list(ist.emoji_list) == ["🔥"] and ist.format == "static"
assert db.get_pack(555)[0] == "unkilbonker_555_by_unkilbonkerbot"
print("PASS kang")

# ---- notes / filters via db-backed handlers
upd, msg = mk_update()
msg.text = "/save rules Be nice to everyone"
ctx.args = ["rules", "Be", "nice", "to", "everyone"]
upd.effective_message.reply_to_message = None
asyncio.run(features.save_note_cmd(upd, ctx))
assert db.get_note(-100123, "rules") is not None
print("PASS notes")

assert moderation.parse_duration("30s") == 30 and moderation.parse_duration("nope") is None
assert moderation.fmt_duration(7200) == "2h"
print("ALL TESTS PASSED")
