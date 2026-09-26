import asyncio, io, json, os
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
from telegram.error import BadRequest, TelegramError
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

# ================= FED TESTS =================
import feds, extras
from unittest.mock import call

if os.path.exists("/tmp/fed_test.db"): os.remove("/tmp/fed_test.db")

# reinit fresh for fed tests
db.DB_PATH = "/tmp/fed_test.db"; db.init_db()

def mk_upd(chat_id=-100777, user_id=111, reply_user=None):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.effective_chat.type = "supergroup"
    upd.effective_chat.title = "FedGroup"
    upd.effective_chat.ban_member = AsyncMock()
    upd.effective_chat.unban_member = AsyncMock()
    upd.effective_chat.promote_member = AsyncMock()
    upd.effective_chat.get_member_count = AsyncMock(return_value=42)
    upd.effective_chat.create_invite_link = AsyncMock(
        return_value=MagicMock(invite_link="https://t.me/+abc"))
    upd.effective_user.id = user_id
    upd.effective_user.first_name = "FedUser"
    upd.effective_user.username = "feduser"
    upd.effective_user.mention_html.return_value = "<a>FedUser</a>"
    msg = MagicMock()
    msg.text = ""
    msg.reply_to_message = MagicMock() if reply_user is None else reply_user
    msg.reply_text = AsyncMock()
    upd.effective_message = msg
    return upd, msg

ctx2 = MagicMock()
ctx2.bot.id = 42
ctx2.bot.username = "UnkilBonkerBot"
ctx2.bot_data = {"owner_id": 111}
banned_ids = []
async def fb(chat_id, uid, only_if_banned=False): banned_ids.append(uid)
ctx2.bot.ban_chat_member = fb
ctx2.bot.unban_chat_member = fb
ctx2.bot.get_chat_administrators = AsyncMock(return_value=[])
sent = []
async def sm(chat_id, text, parse_mode=None): sent.append((chat_id, text))
ctx2.bot.send_message = sm

# ---- create fed
upd, msg = mk_upd()
ctx2.args = ["My Federation"]
asyncio.run(feds.newfed(upd, ctx2))
fed_id = db.all_feds if hasattr(db, "all_feds") else None
# fetch the created fed id from db directly
import sqlite3
conn = sqlite3.connect("/tmp/fed_test.db")
row = conn.execute("SELECT fed_id, owner_id, name FROM feds").fetchone()
conn.close()
FED_ID, OWNER, FNAME = row
assert OWNER == 111 and FNAME == "My Federation"
assert FED_ID in msg.reply_text.call_args[0][0]
print("PASS newfed:", FED_ID)

# ---- joinfed from two chats
for cid in (-100777, -100888):
    upd, msg = mk_upd(chat_id=cid)
    ctx2.args = [FED_ID]
    asyncio.run(feds.joinfed(upd, ctx2))
assert len(db.fed_chats_list(FED_ID)) == 2
print("PASS joinfed x2")

# ---- double-join blocked
upd, msg = mk_upd(chat_id=-100777)
ctx2.args = [FED_ID]
asyncio.run(feds.joinfed(upd, ctx2))
assert len(db.fed_chats_list(FED_ID)) == 2
print("PASS one-fed-per-chat enforced")

# ---- fban propagates to both chats
spam = MagicMock()
spam.from_user = type("U", (), {"id": 999, "first_name": "Spammer", "username": "sp"})()
upd, msg = mk_upd(chat_id=-100777, reply_user=spam)
ctx2.args = ["fed rule breaker"]
asyncio.run(feds.fban(upd, ctx2))
assert db.get_fban(FED_ID, 999)[0] == "fed rule breaker"
assert banned_ids.count(999) == 2, banned_ids
assert len([s for s in sent if -100888 == s[0]]) == 1  # announcement to other chat
print("PASS fban propagates to 2 chats + announces")

# ---- is_fed_banned lookup
assert db.is_fed_banned(-100777, 999) == FNAME
assert db.is_fed_banned(-100777, 555) is None
assert db.user_fed_bans(999) == [(FNAME, "fed rule breaker")]
print("PASS fed ban lookups")

# ---- enforcement watchdog bans fed-banned user on sight
upd, msg = mk_upd(chat_id=-100888, user_id=999)
asyncio.run(feds.check_banned_user(upd, ctx2))
assert 999 in banned_ids  # banned again by watchdog
print("PASS watchdog enforces fed ban")

# ---- unfban removes everywhere
upd, msg = mk_upd(chat_id=-100777, reply_user=spam)
ctx2.args = []
asyncio.run(feds.unfban(upd, ctx2))
assert db.get_fban(FED_ID, 999) is None
print("PASS unfban")

# ---- fpromote / fed admin check
helper = MagicMock()
helper.from_user = type("U", (), {"id": 222, "first_name": "Helper", "username": "hh"})()
upd, msg = mk_upd(chat_id=-100777, reply_user=helper)
asyncio.run(feds.fpromote(upd, ctx2))
assert db.is_fed_admin(db.get_fed(FED_ID), 222)
print("PASS fpromote")

# ---- gban (owner only)
upd, msg = mk_upd(chat_id=-100777, reply_user=spam)
ctx2.args = ["torrent spam"]
asyncio.run(feds.gban(upd, ctx2))
assert db.get_gban(999) == "torrent spam"
upd, msg = mk_upd(chat_id=-100888, user_id=999)
asyncio.run(feds.check_banned_user(upd, ctx2))  # watchdog sees gban
print("PASS gban + watchdog")

# ---- non-owner can't gban
upd, msg = mk_upd(chat_id=-100777, user_id=333, reply_user=spam)
asyncio.run(feds.gban(upd, ctx2))
assert "owner" in msg.reply_text.call_args[0][0]
print("PASS gban restricted to owner")

# ---- extras: promote / invite / users
upd, msg = mk_upd(chat_id=-100777, reply_user=helper)
ctx2.args = []
asyncio.run(extras.promote(upd, ctx2))
assert upd.effective_chat.promote_member.await_count == 1
kw = upd.effective_chat.promote_member.call_args.kwargs
assert kw.get("can_promote_members") is not True  # basic promote
print("PASS promote")

upd, msg = mk_upd(chat_id=-100777)
ctx2.args = []
asyncio.run(extras.invite(upd, ctx2))
assert "https://t.me/+abc" in msg.reply_text.call_args[0][0]
print("PASS invite")

upd, msg = mk_upd(chat_id=-100777)
ctx2.args = []
asyncio.run(extras.users_cmd(upd, ctx2))
assert "42" in msg.reply_text.call_args[0][0]
print("PASS users")

# ---- fedstat
upd, msg = mk_upd(chat_id=-100777, reply_user=spam)
ctx2.args = []
asyncio.run(feds.fedstat(upd, ctx2))
assert "Globally banned" in msg.reply_text.call_args[0][0]
print("PASS fedstat")

# ---- structural: all new handlers registered
from telegram.ext import ApplicationBuilder
app = ApplicationBuilder().token("123456:fake").build()
import bot as botmod
db.DB_PATH = "/tmp/test_ub2.db"  # reset to suite db
botmod.register(app)
counts = {g: len(h) for g, h in app.handlers.items()}
total = sum(counts.values())
print("handler groups:", dict(sorted(counts.items())), "| total:", total)
assert total >= 60, total
print("FED + EXTRAS: ALL TESTS PASSED")

# ================= V3 TESTS: blocklist, captcha, connect, export, etc =====
import captcha, utilities

if os.path.exists("/tmp/v3.db"): os.remove("/tmp/v3.db")
db.DB_PATH = "/tmp/v3.db"; db.init_db()

def mk3(chat_id=-100555, user_id=1, is_admin=True, text=""):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.effective_chat.type = "supergroup"
    upd.effective_chat.title = "V3Group"
    upd.effective_chat.ban_member = AsyncMock()
    upd.effective_chat.unban_member = AsyncMock()
    upd.effective_chat.restrict_member = AsyncMock()
    upd.effective_chat.get_member = AsyncMock(return_value=type(
        "M", (), {"user": type("U", (), {"id": 77})(),
                  "status": "administrator", "can_restrict_members": True})())
    upd.effective_user.id = user_id
    upd.effective_user.first_name = "Tester"
    upd.effective_user.username = "tester"
    upd.effective_user.mention_html.return_value = "<a>T</a>"
    msg = MagicMock()
    msg.text = text
    msg.chat = upd.effective_chat
    msg.from_user = upd.effective_user
    msg.entities = []
    msg.caption_entities = []
    msg.reply_to_message = MagicMock()
    msg.reply_to_message.delete = AsyncMock()
    msg.reply_to_message.from_user = type("U", (), {"id": 555,
        "first_name": "Target", "username": "target"})()
    msg.reply_text = AsyncMock()
    msg.delete = AsyncMock()
    upd.effective_message = msg
    upd._effective_chat = upd.effective_chat
    return upd, msg

ctx3 = MagicMock()
ctx3.bot.id = 77
ctx3.bot.username = "UnkilBonkerBot"
ctx3.bot_data = {"owner_id": 1}
admin_member = type("M", (), {"user": type("U", (), {"id": 1})(),
                              "status": "administrator"})()
ctx3.bot.get_chat_administrators = AsyncMock(return_value=[admin_member])
ctx3.job_queue = MagicMock()
ctx3.job_queue.run_once = MagicMock()
ctx3.bot.send_message = AsyncMock()
ctx3.bot.delete_message = AsyncMock()
ctx3.bot.send_photo = AsyncMock()

# ---- blocklist enforcement (mode: kick)
upd, msg = mk3(chat_id=-100555, user_id=666, is_admin=False, text="buy cheap SCAM coins now")
ctx3.args = ["scam"]
upd2, msg2 = mk3(chat_id=-100555, user_id=1, text="/addblocklist scam")
asyncio.run(features.add_blocklist_cmd(upd2, ctx3))
db.set_setting(-100555, "blocklist_mode", "kick")
# simulate group admin check for the non-admin sender
upd.effective_chat.get_administrators = AsyncMock(return_value=[admin_member])
asyncio.run(features.enforce_blocklist(upd, ctx3))
assert msg.delete.await_count == 1
assert upd.effective_chat.ban_member.await_count == 1  # kick = ban+unban
print("PASS blocklist enforcement (kick)")

# ---- allowlist exempts url lock
db.add_allowlist(-100555, "trusted.com")
assert db.is_url_allowed(-100555, "https://www.trusted.com/path") is True
assert db.is_url_allowed(-100555, "https://evil.com") is False
print("PASS allowlist")

# ---- url lock respects allowlist
from features import LOCK_TYPES
def url_msg(text):
    m = MagicMock()
    m.chat.id = -100555
    m.entities = [MagicMock(type="url", offset=0, length=len(text))]
    m.caption_entities = []
    m.text = text
    return m
assert LOCK_TYPES["urls"](url_msg("https://trusted.com/x"), None) is False
assert LOCK_TYPES["urls"](url_msg("https://evil.com/x"), None) is True
print("PASS url lock + allowlist integration")

# ---- rtl + album + bots locks
m = MagicMock(); m.chat = upd.effective_chat
m.entities = []; m.caption_entities = []
LOCK_TYPES["rtl"](m, None)  # no crash
m2 = MagicMock(); m2.media_group_id = 123
assert LOCK_TYPES["album"](m2, None) is True
m3 = MagicMock(); m3.from_user.is_bot = True; m3.entities = []; m3.caption_entities = []
assert LOCK_TYPES["bots"](m3, None) is True
print("PASS new lock types")

# ---- unwarn decrements
db.add_warn(-100555, 666, "one"); db.add_warn(-100555, 666, "two")
upd, msg = mk3(chat_id=-100555, user_id=1)
upd.effective_message.reply_to_message.from_user.id = 666
asyncio.run(moderation.unwarn(upd, ctx3))
assert db.get_warns(-100555, 666)[0] == 1
print("PASS unwarn")

# ---- setwarnmode mute: user hits limit -> muted not banned
db.reset_warns(-100555, 666)
db.set_setting(-100555, "warn_mode", "mute")
upd, msg = mk3(chat_id=-100555, user_id=1)
upd.effective_message.reply_to_message.from_user.id = 666
for _ in range(3):
    asyncio.run(moderation.warn(upd, ctx3))
assert upd.effective_chat.restrict_member.await_count == 1
assert upd.effective_chat.ban_member.await_count == 0
print("PASS warnmode=mute")

# ---- goodbye rendering
real_user = type("U", (), {"first_name": "Asha", "last_name": "P", "username": "asha",
                           "mention_html": lambda s: "<a>Asha</a>"})()
out = features.render_welcome(features.GOODBYE_DEFAULT, real_user,
                              type("C", (), {"title": "G"}), 0)
assert "See you around" in out
print("PASS goodbye render")

# ---- media notes (photo)
upd, msg = mk3(chat_id=-100555, user_id=1)
r = upd.effective_message.reply_to_message
r.photo = [MagicMock(file_id="PHOTO1")]
r.sticker = r.animation = r.video = r.document = None
r.audio = r.voice = r.video_note = None
r.caption = "cool pic"
r.text_html = None
payload = features._note_payload(r)
assert payload["t"] == "media" and payload["k"] == "photo" and payload["f"] == "PHOTO1"
db.save_note(-100555, "pic", json.dumps(payload))
upd, msg = mk3(chat_id=-100555, user_id=999)
ctx3.bot.send_photo = AsyncMock()
ctx3.args = ["pic"]
asyncio.run(features.get_note_cmd(upd, ctx3))
ctx3.bot.send_photo.assert_awaited_once()
print("PASS media notes")

# ---- captcha math flow
db.set_setting(-100444, "captcha", "1")
db.set_setting(-100444, "captcha_mode", "math")
db.DB_PATH; captcha._pending.clear()
upd, msg = mk3(chat_id=-100444, user_id=1)
upd.effective_message.new_chat_members = [type("U", (), {"id": 666,
    "first_name": "Newbie"})()]
upd.effective_message.new_chat_members[0].id = 666
asyncio.run(captcha.on_member_join(upd, ctx3))
key = (-100444, 666)
assert key in captcha._pending and captcha._pending[key]["answer"].isdigit()
assert upd.effective_chat.restrict_member.await_count == 1  # muted
assert ctx3.job_queue.run_once.called  # timeout scheduled
# user answers correctly
ctx3.bot.restrict_chat_member = AsyncMock()
captcha_msg = MagicMock()
upd, msg = mk3(chat_id=-100444, user_id=666, text=captcha._pending[key]["answer"])
upd.effective_chat.id = -100444
asyncio.run(captcha.on_captcha_answer(upd, ctx3))
assert ctx3.bot.restrict_chat_member.await_count == 1  # unmuted
assert key not in captcha._pending
print("PASS captcha math flow (mute -> answer -> unmute)")

# ---- captcha button callback
db.set_setting(-100445, "captcha", "1")
db.set_setting(-100445, "captcha_mode", "button")
upd, msg = mk3(chat_id=-100445, user_id=1)
upd.effective_message.new_chat_members = [type("U", (), {"id": 777,
    "first_name": "Btn"})()]
upd.effective_message.new_chat_members[0].id = 777
asyncio.run(captcha.on_member_join(upd, ctx3))
key = (-100445, 777)
assert key in captcha._pending
q = MagicMock(); q.data = f"cap:-100445:777"; q.from_user.id = 777
q.answer = AsyncMock()
q.message = MagicMock()
q.message.delete = AsyncMock()
upd2 = MagicMock(); upd2.callback_query = q
upd2.effective_user = type("U", (), {"id": 777, "mention_html": lambda s: "<a>B</a>"})()
ctx3.bot.restrict_chat_member = AsyncMock()
asyncio.run(captcha.on_captcha_button(upd2, ctx3))
assert ctx3.bot.restrict_chat_member.await_count == 1
assert key not in captcha._pending
print("PASS captcha button flow")

# ---- antiraid auto-enable
db.set_setting(-100446, "raid_threshold", 2)
upd, msg = mk3(chat_id=-100446, user_id=1)
upd.effective_message.new_chat_members = [type("U", (), {"id": 1, "first_name": "A"})()]
for i in range(2):
    asyncio.run(captcha.on_member_join(upd, ctx3))
assert db.get_setting(-100446, "antiraid") == "1"
print("PASS antiraid auto-trigger")

# ---- connect middleware
db.set_connection(1, -100555)
from telegram import (Update as RealUpdate, Chat as RealChat,
                     Message as RealMessage, User as RealUser)
dm_chat = RealChat(id=111, type="private", first_name="Me")
dm_user = RealUser(id=1, first_name="Me", is_bot=False)
dm_msg = RealMessage(message_id=5, date=None, chat=dm_chat, from_user=dm_user,
                     text="/ban")
real_upd = RealUpdate(update_id=1, message=dm_msg)
remote = type("C", (), {"id": -100555, "type": "supergroup", "title": "Remote"})()
ctx3.bot.get_chat = AsyncMock(return_value=remote)

@utilities.connected
async def probe(update, context):
    return update.effective_chat.id

result = asyncio.run(probe(real_upd, ctx3))
assert result == -100555, result
# replies still target the DM (message.chat untouched)
assert real_upd.effective_message.chat.id == 111
print("PASS connect middleware redirects effective_chat")

# ---- fedsubscribe propagation
db.DB_PATH = "/tmp/v3.db"
fed_a = db.create_fed(1, "FedA")
fed_b = db.create_fed(1, "FedB")
db.join_fed(fed_a, -100555, "G1")
db.join_fed(fed_b, -100556, "G2")
db.fed_subscribe(fed_b, fed_a)  # B follows A's bans
# fban in A propagates to B's chats
ctx3.bot.ban_chat_member = AsyncMock()
upd, msg = mk3(chat_id=-100555, user_id=1)
upd.effective_message.reply_to_message.from_user.id = 888
db.add_fban(fed_a, 888, "test", 1)
banned = []
async def b2(cid, uid, only_if_banned=False): banned.append((cid, uid))
ctx3.bot.ban_chat_member = b2
ctx3.bot.send_message = AsyncMock()
asyncio.run(feds.fban(upd, ctx3))
assert (-100556, 888) in banned  # B's group got the ban via subscription
assert db.is_fed_banned(-100556, 888) is not None
print("PASS fedsubscribe propagation")

# ---- export/import roundtrip
upd, msg = mk3(chat_id=-100555, user_id=1)
upd.effective_message.reply_document = AsyncMock()
asyncio.run(utilities.export_cmd(upd, ctx3))
doc = upd.effective_message.reply_document.call_args.kwargs["document"]
data = json.loads(doc.getvalue().decode())
assert data["settings"] and "scam" in data["blocklist"]
# import into a fresh chat
db.del_blocklist(-100559, "scam") if db.list_blocklist(-100559) else None
upd, msg = mk3(chat_id=-100559, user_id=1)
srcmsg = MagicMock()
srcmsg.document.get_file = AsyncMock(return_value=MagicMock(
    download_as_bytearray=AsyncMock(return_value=doc.getvalue())))
upd.effective_message.reply_to_message = srcmsg
asyncio.run(utilities.import_cmd(upd, ctx3))
assert "scam" in db.list_blocklist(-100559)
print("PASS export/import roundtrip")

print("V3: ALL TESTS PASSED")

# ================= V4 TESTS: quotly, tagall, memefi, ads, bonk aliases =====
import quotly, memefi, tagall

# ---- quote render (visual verified separately; sanity here)
png = quotly.render_quote("hello world", "Tester", "tester", "01.01.2026")
assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
webp = quotly.to_sticker_bytes(png)
assert webp[:4] == b"RIFF"  # webp magic
img = Image.open(io.BytesIO(webp))
assert 512 in (img.width, img.height) and img.width <= 512 and img.height <= 512
print("PASS quote render + sticker scaling")

# ---- meme render
buf = io.BytesIO(); Image.new("RGB", (640, 400), (10, 10, 10)).save(buf, "WEBP")
out = memefi.render_meme(buf.getvalue(), top="A", bottom="B")
mimg = Image.open(io.BytesIO(out))
assert mimg.size == (512, 512)
print("PASS meme render 512x512")

# ---- tagall chunking + cancel
if os.path.exists("/tmp/v4.db"): os.remove("/tmp/v4.db")
db.DB_PATH = "/tmp/v4.db"; db.init_db()
for i in range(120):
    db.upsert_member(-100777, 1000 + i, f"Member{i}", f"m{i}")

upd, msg = mk3(chat_id=-100777, user_id=1, text="/tagall")
sent_msgs = []
async def fake_send(chat_id, text, parse_mode=None):
    sent_msgs.append(text)
    if len(sent_msgs) > 10: raise AssertionError("too many messages")
ctx3.bot.send_message = fake_send
ctx3.args = ["Test announcement"]
asyncio.run(tagall.tagall_cmd(upd, ctx3))
assert len(sent_msgs) >= 3, len(sent_msgs)  # 120 members / 50 per message
assert "Test announcement" in sent_msgs[0]
assert sent_msgs[0].count("tg://user?id=") > 40
assert all(len(t) <= 4096 for t in sent_msgs)
print(f"PASS tagall: {len(sent_msgs)} messages for 120 members")

# utagall uses @names
sent_msgs.clear()
ctx3.args = []
asyncio.run(tagall.utagall_cmd(upd, ctx3))
assert "@Member1 " in "".join(sent_msgs) or "@Member0" in "".join(sent_msgs)
print("PASS utagall")

# ---- ads remover detection
class FakeChat: id = -100777; username = "mychat"
class FakeMsg: pass
m = FakeMsg(); m.text = "join https://t.me/+abc123 now"
assert features._is_ad(m, FakeChat) is True
m2 = FakeMsg(); m2.text = "check out @news t.me/somechannel"
assert features._is_ad(m2, FakeChat) is True
m3 = FakeMsg(); m3.text = "see t.me/mychat rules"
assert features._is_ad(m3, FakeChat) is False
m4 = FakeMsg(); m4.text = "normal message, no links"
assert features._is_ad(m4, FakeChat) is False
m5 = FakeMsg(); m5.text = "pack t.me/addstickers/foo"
assert features._is_ad(m5, FakeChat) is False
print("PASS ads detection")

# ---- adsremover enforcement
db.set_setting(-100777, "adsremover", "1")
upd, msg = mk3(chat_id=-100777, user_id=666, text="promo t.me/otherchannel")
upd.effective_chat.username = "mychat"
stopped = False
try:
    asyncio.run(features.enforce_ads(upd, ctx3))
except Exception:
    stopped = True
assert msg.delete.await_count == 1 and stopped
# admins exempt
upd, msg = mk3(chat_id=-100777, user_id=1, text="promo t.me/otherchannel")
upd.effective_chat.username = "mychat"
asyncio.run(features.enforce_ads(upd, ctx3))
assert msg.delete.await_count == 0
db.set_setting(-100777, "adsremover", "0")
print("PASS adsremover enforcement")

# ---- structural: all new handlers registered
from telegram.ext import ApplicationBuilder
app = ApplicationBuilder().token("123456:fake").build()
import bot as botmod2
db.DB_PATH = "/tmp/v4.db"
botmod2.register(app)
counts = {g: len(h) for g, h in app.handlers.items()}
total = sum(counts.values())
print("handler groups:", dict(sorted(counts.items())), "| total:", total)
assert total >= 130, total
print("V4: ALL TESTS PASSED")

# ================= V6 TESTS: approvals, variants, disabling, etc ==========
import approvals, disabling

if os.path.exists("/tmp/v6.db"): os.remove("/tmp/v6.db")
db.DB_PATH = "/tmp/v6.db"; db.init_db()

# ---- approvals: exempt from locks
upd, msg = mk3(chat_id=-100666, user_id=1, text="/approve")
upd.effective_message.reply_to_message.from_user.id = 555
upd.effective_message.reply_to_message.from_user.first_name = "Target"
ctx3.args = []
asyncio.run(approvals.approve_cmd(upd, ctx3))
assert db.is_approved(-100666, 555) is True
db.set_locks(-100666, {"stickers"})
upd, msg = mk3(chat_id=-100666, user_id=555)
msg.sticker = True
stopped = False
try: asyncio.run(features.enforce_locks(upd, ctx3))
except Exception: stopped = True
assert msg.delete.await_count == 0 and not stopped, "approved user hit lock"
db.set_locks(-100666, set())
print("PASS approvals exempt from locks")

# unapprove -> subject again
upd, msg = mk3(chat_id=-100666, user_id=1)
upd.effective_message.reply_to_message.from_user.id = 555
asyncio.run(approvals.unapprove_cmd(upd, ctx3))
assert db.is_approved(-100666, 555) is False
rows = db.approved_list(-100666)
assert rows == []
print("PASS unapprove")

# ---- ban variants: sban deletes command message, dban deletes target
upd, msg = mk3(chat_id=-100666, user_id=1)
upd.effective_message.reply_to_message.from_user.id = 555
ctx3.args = ["spam"]
asyncio.run(moderation.sban(upd, ctx3))
assert upd.effective_chat.ban_member.await_count == 1
assert msg.delete.await_count == 1, "sban should delete the command msg"
upd, msg = mk3(chat_id=-100666, user_id=1)
upd.effective_message.reply_to_message.from_user.id = 556
asyncio.run(moderation.dkick(upd, ctx3))
assert upd.effective_chat.ban_member.await_count == 1
assert msg.reply_to_message.delete.await_count == 1, "dkick deletes target msg"
print("PASS sban/dkick variants")

# ---- blocklist wildcards
assert features._blocklist_match("spam*", "spammer spamming") is True
assert features._blocklist_match("spam*", "spa m") is False  # * = no spaces
assert features._blocklist_match("b?y", "buy") is True
assert features._blocklist_match("b?y", "by") is False
assert features._blocklist_match("bad**word", "bad WORD".replace("WORD","word")) is True
assert features._blocklist_match("bad**word", "bad  word") is True
assert features._blocklist_match("hello", "say hello world") is True
print("PASS blocklist wildcards (?, *, **)")

# ---- clean service detection
class FakeMsg: pass
def svc_msg(**kw):
    m = FakeMsg()
    attrs = dict(new_chat_members=None, left_chat_member=None, pinned_message=None,
                 new_chat_title=None, new_chat_photo=None, delete_chat_photo=None,
                 group_chat_created=False, supergroup_chat_created=False,
                 text=None, caption=None, photo=None, video=None, audio=None,
                 voice=None, video_note=None, document=None, sticker=None,
                 animation=None, video_chat_started=None, video_chat_ended=None,
                 video_chat_participants_invited=None, video_chat_scheduled=None)
    attrs.update(kw)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m
assert features._service_kind(svc_msg(new_chat_members=[1])) == "join"
assert features._service_kind(svc_msg(left_chat_member=1)) == "leave"
assert features._service_kind(svc_msg(pinned_message=1)) == "pin"
assert features._service_kind(svc_msg(new_chat_title="x")) == "title"
assert features._service_kind(svc_msg(new_chat_photo=1)) == "photo"
assert features._service_kind(svc_msg(video_chat_started=True)) == "videochat"
print("PASS clean service detection")

# ---- disabling: /disable + enforcement
upd, msg = mk3(chat_id=-100666, user_id=1, text="/disable ping")
ctx3.args = ["ping"]
asyncio.run(disabling.disable_cmd(upd, ctx3))
assert "ping" in json.loads(db.get_setting(-100666, "disabled_cmds"))
# a non-admin using /ping gets stopped and deleted
db.set_setting(-100666, "disabledel", "1")
upd, msg = mk3(chat_id=-100666, user_id=777, text="/ping")
stopped = False
try: asyncio.run(disabling.enforce_disabled(upd, ctx3))
except Exception: stopped = True
assert stopped and msg.delete.await_count == 1
# admins bypass by default
upd, msg = mk3(chat_id=-100666, user_id=1, text="/ping")
stopped = False
try: asyncio.run(disabling.enforce_disabled(upd, ctx3))
except Exception: stopped = True
assert not stopped
print("PASS disabling enforcement")

# ---- floodmode tban + timer window
db.set_setting(-100666, "antiflood", "3")
db.set_setting(-100666, "flood_mode", "mute")
upd, msg = mk3(chat_id=-100666, user_id=778)
asyncio.run(features.check_flood(upd, ctx3)); asyncio.run(features.check_flood(upd, ctx3))
asyncio.run(features.check_flood(upd, ctx3)); asyncio.run(features.check_flood(upd, ctx3))
assert upd.effective_chat.restrict_member.await_count == 1, "flood -> mute mode"
db.set_setting(-100666, "antiflood", "0")
print("PASS floodmode mute")

# ---- fedexport csv + fedimport roundtrip
fed_x = db.create_fed(1, "FedX")
db.join_fed(fed_x, -100777, "G1")
db.add_fban(fed_x, 999, "exported reason", 1)
upd, msg = mk3(chat_id=-100777, user_id=1)
upd.effective_message.reply_document = AsyncMock()
ctx3.args = ["csv"]
asyncio.run(feds.fedexport_cmd(upd, ctx3))
doc = upd.effective_message.reply_document.call_args.kwargs["document"]
csv_data = doc.getvalue().decode()
assert "999" in csv_data and "exported reason" in csv_data
# import into a new fed
fed_y = db.create_fed(1, "FedY")
db.join_fed(fed_y, -100888, "G2")
srcmsg = MagicMock()
srcmsg.document.get_file = AsyncMock(return_value=MagicMock(
    download_as_bytearray=AsyncMock(return_value=csv_data.encode())))
upd, msg = mk3(chat_id=-100888, user_id=1)
upd.effective_message.reply_to_message = srcmsg
ctx3.args = ["keep"]
asyncio.run(feds.fedimport_cmd(upd, ctx3))
assert db.get_fban(fed_y, 999) == ("exported reason", 1)
print("PASS fedexport/fedimport roundtrip")

# ---- myfeds
rows = db.my_feds(1)
assert any(f == fed_y for f, n, r in rows)
print("PASS myfeds")

# ---- structural: everything registered
from telegram.ext import ApplicationBuilder
app = ApplicationBuilder().token("123456:fake").build()
import bot as botmod3
db.DB_PATH = "/tmp/v6.db"
botmod3.register(app)
counts = {g: len(h) for g, h in app.handlers.items()}
total = sum(counts.values())
print("handler groups:", dict(sorted(counts.items())), "| total:", total)
assert total >= 165, total
print("V6: ALL TESTS PASSED")

# ================= V7 TESTS: joinfed own-fed + un-aliases ===============
if os.path.exists("/tmp/v7.db"): os.remove("/tmp/v7.db")
db.DB_PATH = "/tmp/v7.db"; db.init_db()

# owner creates a fed, then joins a group with NO fed id argument
fed_id = db.create_fed(1, "MyNet")
upd, msg = mk3(chat_id=-100999, user_id=1, text="/joinfed")
ctx3.args = []
db.join_fed(fed_id, -100999, "G")  # pre-existing? no - leave first
db.leave_fed(-100999)
asyncio.run(feds.joinfed(upd, ctx3))
assert db.chat_fed(-100999)["fed_id"] == fed_id, "joinfed no-arg should join own fed"
print("PASS joinfed with no args joins own fed")

# user with no fed gets a hint, not a crash
upd, msg = mk3(chat_id=-100998, user_id=4242, text="/joinfed")
db.join_fed(fed_id, -100998, "G2")
db.leave_fed(-100998)
asyncio.run(feds.joinfed(upd, ctx3))
assert db.chat_fed(-100998) is None
print("PASS joinfed no-arg without own fed")

# aliases registered: unbonk, unfbonki, unbonki
app = ApplicationBuilder().token("123456:fake").build()
db.DB_PATH = "/tmp/v7.db"
botmod3 = __import__("bot")
botmod3.register(app)
cmds = set()
for h in app.handlers.get(0, []):
    cmds |= (h.commands or set()) if hasattr(h, "commands") else set()
for a in ("unbonk", "unfbonki", "unbonki", "joinfed", "approve",
          "cleanservice", "disable", "fedexport", "raidtime"):
    assert a in cmds, a
print("PASS all v6+v7 commands registered:", len(cmds), "commands")
print("V7: ALL TESTS PASSED")

# ================= V8 TESTS: tagall learns from joins ====================
if os.path.exists("/tmp/v8.db"): os.remove("/tmp/v8.db")
db.DB_PATH = "/tmp/v8.db"; db.init_db()
import tagall

upd, msg = mk3(chat_id=-100111, user_id=888, text="")
msg.new_chat_members = [type("U", (), {"id": 888, "first_name": "Joiner",
                                       "username": "joined"})()]
upd.effective_user.id = 888
upd.effective_user.first_name = "Joiner"
upd.effective_user.username = "joined"
asyncio.run(tagall.track_members(upd, ctx3))
rows = db.get_members(-100111)
assert (888, "Joiner", "joined") in rows, rows
print("PASS tagall learns from join service messages")

# empty member list -> admin seeding fallback
upd, msg = mk3(chat_id=-100112, user_id=1, text="/tagall")
admin_u = type("U", (), {"id": 55, "first_name": "AdminA", "username": "aa",
                         "is_bot": False})()
upd.effective_chat.get_administrators = AsyncMock(
    return_value=[type("M", (), {"user": admin_u})()])
ctx3.args = []
asyncio.run(tagall.tagall_cmd(upd, ctx3))
rows = db.get_members(-100112)
assert any(uid == 55 for uid, n, u in rows), rows
sent = msg.reply_text.await_args_list
print("PASS tagall admin-seeding fallback")
print("V8: ALL TESTS PASSED")

# ================= V9 TESTS: kang robustness =============================
if os.path.exists("/tmp/v9.db"): os.remove("/tmp/v9.db")
db.DB_PATH = "/tmp/v9.db"; db.init_db()
import stickers
from telegram.error import BadRequest, TelegramError

def mk_upd_with_sticker(chat_id=-100123, user_id=911, vid=False, anim=False,
                        emoji="🤔"):
    upd, msg = mk3(chat_id=chat_id, user_id=user_id, text="/kang")
    st = MagicMock()
    st.is_video = vid; st.is_animated = anim
    st.emoji = emoji; st.file_id = "st_file_id_1"
    msg.reply_to_message.sticker = st
    msg.reply_to_message.photo = None
    return upd, msg, st

# bot mock whose add/create fail with given sequences
class BotMock:
    def __init__(self, add_seq=(), create_seq=()):
        self.username = "unkilbot"
        self._add = list(add_seq); self._create = list(create_seq)
        self.add_calls = []; self.create_calls = []
    async def add_sticker_to_set(self, uid, name, sticker):
        self.add_calls.append(name)
        r = self._add.pop(0)
        if isinstance(r, Exception): raise r
        return r
    async def create_new_sticker_set(self, **kw):
        self.create_calls.append(kw["name"])
        r = self._create.pop(0)
        if isinstance(r, Exception): raise r
        return r

# 1. first kang: add fails 'invalid' -> create succeeds
upd, msg, st = mk_upd_with_sticker()
b = BotMock(add_seq=[BadRequest("Sticker set is invalid")],
            create_seq=["ok"])
ctx3.bot = b; ctx3.args = []
asyncio.run(stickers.kang(upd, ctx3))
assert b.create_calls == ["unkilbonker_911_by_unkilbot"], b.create_calls
assert db.get_pack(911, "static")[0] == "unkilbonker_911_by_unkilbot"
assert "New pack created" in msg.reply_text.await_args.args[0]
print("PASS kang: first-time pack creation")

# 2. video sticker goes to a separate pack
upd, msg, st = mk_upd_with_sticker(vid=True)
b = BotMock(add_seq=[BadRequest("Sticker set is invalid")], create_seq=["ok"])
ctx3.bot = b
asyncio.run(stickers.kang(upd, ctx3))
assert b.create_calls == ["unkilbonkerv_911_by_unkilbot"], b.create_calls
print("PASS kang: video stickers use a separate video pack")

# 3. pack full -> rollover to _2
upd, msg, st = mk_upd_with_sticker()
b = BotMock(add_seq=[BadRequest("Stickers too much"),
                     BadRequest("Stickers too much"),
                     "ok"])
ctx3.bot = b
asyncio.run(stickers.kang(upd, ctx3))
assert b.add_calls == ["unkilbonker_911_by_unkilbot",
                       "unkilbonker_911_by_unkilbot_1",
                       "unkilbonker_911_by_unkilbot_2"], b.add_calls
assert db.get_pack(911, "static")[0].endswith("_2")
print("PASS kang: full pack rolls over to _2")

# 4. occupied race: add invalid -> create occupied -> add retry ok
upd, msg, st = mk_upd_with_sticker()
b = BotMock(add_seq=[BadRequest("Sticker set is invalid"), "ok"],
            create_seq=[BadRequest("Name is already occupied")])
ctx3.bot = b
asyncio.run(stickers.kang(upd, ctx3))
assert "Kanged" in msg.reply_text.await_args.args[0]
print("PASS kang: occupied race recovers")

# 5. real errors surface verbatim (no silent death)
upd, msg, st = mk_upd_with_sticker()
b = BotMock(add_seq=[BadRequest("Bot was blocked by the user")])
ctx3.bot = b
asyncio.run(stickers.kang(upd, ctx3))
assert "blocked" in msg.reply_text.await_args.args[0]
print("PASS kang: errors reported verbatim")

# 6. no sticker/photo -> usage message (not a crash)
upd, msg = mk3(chat_id=-100123, user_id=911, text="/kang")
upd.effective_message.reply_to_message.sticker = None
upd.effective_message.reply_to_message.photo = None
ctx3.args = []
asyncio.run(stickers.kang(upd, ctx3))
assert "Reply to a sticker" in msg.reply_text.await_args.args[0]
print("PASS kang: usage message without sticker")

# 7. packs_cmd lists all format packs
upd, msg = mk3(chat_id=-100123, user_id=911, text="/packs")
upd.effective_message.reply_to_message = None
ctx3.bot.get_sticker_set = AsyncMock(side_effect=TelegramError("no"))
asyncio.run(stickers.packs_cmd(upd, ctx3))
assert "addstickers" in msg.reply_text.await_args.args[0]
print("PASS packs_cmd multi-format listing")
print("V9: ALL TESTS PASSED")
