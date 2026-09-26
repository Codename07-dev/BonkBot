# UnkilBonker 🦘

An all-in-one Telegram bot combining the core of Rose/Hypernova/Natalie-style
group management with Stickerkang-style sticker stealing, plus an optional AI
channel-post rewriter.

## Feature map

| Area | What it does |
|---|---|
| **Moderation** | `/ban` `/tban` `/unban` `/kick` `/mute` `/tmute` `/unmute` `/kickme` `/warn` `/warnings` `/resetwarns` `/warnlimit` `/purge` `/del` `/pin` `/unpin` |
| **Welcome** | `/setwelcome` (placeholders `{first}` `{mention}` `{username}` `{chatname}` `{count}`), `/welcome on\|off`, `/resetwelcome` |
| **Notes** | `/save` `/get` `/notes` `/clear` - notes also trigger via `#name` |
| **Filters** | `/filter` `/stop` `/stopall` `/filters` - auto-reply when a trigger word appears |
| **Locks** | `/lock` `/unlock` `/locks` - types: msgs, stickers, gifs, photos, videos, audio, voice, video_notes, documents, polls, games, urls, forwards, captions, edits |
| **Antiflood** | `/antiflood <limit\|off>` - removes flooders automatically |
| **Federations** | `/newfed` `/fedinfo` `/delfed` `/joinfed` `/leavefed` `/fedchats` - create a fed, connect groups, then `/fban` bans a user in **every** fed group at once. `/fbanlist` `/fpromote` `/fdemote` `/fedadmins` `/fedstat` |
| **Global bans** | `/gban` `/ungban` `/gbanlist` - bot owner bans a user across every group the bot knows. Fed/gbans are enforced automatically on join and on every message |
| **Admin tools** | `/promote` `/fullpromote` `/demote` `/invite` (invite link) `/users` (member count) `/settitle` `/setdesc` `/setgpic` |
| **Reports** | `/report` (reply) notifies admins in private; `/reports on\|off` toggles |
| **Warn system** | `/warn` `/unwarn` `/warnings` `/resetwarns` `/setwarnlimit` `/setwarnmode <ban/tban/mute/tmute/kick>` |
| **Anti-spam** | `/setflood` `/setfloodmode` `/flood` - 25+ lock types (`/locktypes`) incl. rtl, album, bots, buttons, invitelinks - `/allowlist` `/rmallowlist` URL exemptions - full blocklist: `/addblocklist` `/rmblocklist` `/blocklist` `/blocklistmode` `/blocklistreason` |
| **CAPTCHA gates** | `/captcha on\|off` `/captchamode <button/math/text>` `/captchatime 2m` `/setcaptchatext` `/captchafile` - mutes new members until verified, auto-kick on timeout |
| **Anti-raid** | `/antiraid on\|off` `/setraidthreshold <n>` - auto-enables when join rate exceeds the threshold |
| **Goodbyes** | `/setgoodbye` `/goodbye on\|off` `/resetgoodbye` `/cleanwelcome` `/clearcleft` |
| **Fed subscriptions** | `/fedsubscribe <fed_id>` - follow another fed's ban feed; fbans propagate automatically |
| **Remote admin** | `/connect` `/disconnect` `/connection` - manage a group from your DM with the bot |
| **Backups** | `/export` `/import` - JSON backup/restore of all chat settings (works around Render's ephemeral disk) |
| **Media notes** | `/save` while replying to any media stores it; `#name` resends the media |
| **Quotly** | `/q` (reply) - renders any message into a beautiful quote sticker with the author's avatar, colored accent and timestamp |
| **TagAll** | `/tagall` `/all` `/utagall` `/uall` `/utag` - mention every member (chunked, cancellable with `/cancel`); members are learned from chat activity |
| **MemeFi** | `/mmf Top ; Bottom` or `/mmf -c Center` (reply to a sticker) - classic meme text with auto-wrapping, returns a 512×512 sticker |
| **Ads Remover** | `/adsremover on\|off` - Kafka-style: deletes invite links, promos to other channels and cross-chat forwards from non-admins |
| **Bonk flavour** | `/bonk` = ban, `/fbonk` = fed-ban, `/bonki` = warn |
| **AFK** | `/afk <reason>` - replies to anyone who mentions an AFK user |
| **Info** | `/id` `/info` `/adminlist` `/ping` |
| **Stickers** | `/kang` (reply to a sticker/photo → your own pack), `/packs`, `/getsticker` |
| **AI rewriter** | optional - rewrites new posts in your channel with Gemini/OpenAI-compatible AI |

## Setup

### 1. Create the bot
Talk to **@BotFather** → `/newbot` → name it **UnkilBonker** → copy the token.

### 2. IMPORTANT: disable privacy mode (for group features)
@BotFather → `/setprivacy` → select your UnkilBonker bot → **Disable**.
Without this the bot can't see normal group messages, so filters, locks and
antiflood won't work. (Adding the bot as group admin also works.)

### 3. Add it to your group
Add as **admin** with at least *Delete messages*, *Restrict/Ban users* and
*Pin messages* rights. Run `/help` in the group to confirm it's alive.

### 4. Configure
Copy `.env.example` to `.env` and fill in:

- `TELEGRAM_BOT_TOKEN` (required)
- `OWNER_ID` - your user id (from @userinfobot) so nothing can be used on you
- Optional AI rewriter: `CHANNEL_ID`, `AI_API_KEY` (Gemini key from
  aistudio.google.com/apikey)

### 5. Run

```bash
pip install -r requirements.txt
python bot.py
```

Polling mode is used when `WEBHOOK_URL` is empty - perfect for local testing.

## Deploying on Render (free)

Same flow as any Python web service:

1. Push the files to GitHub (never push `.env`).
2. render.com → **New → Web Service** → connect the repo.
3. Build: `pip install -r requirements.txt`, Start: `python bot.py`,
   and set `PYTHON_VERSION=3.12.7` (already in `render.yaml`).
4. Environment variables: `TELEGRAM_BOT_TOKEN`, `OWNER_ID`,
   and `WEBHOOK_URL` = `https://your-service.onrender.com` (no trailing slash).
5. Deploy. Use a free UptimeRobot monitor pinging the service URL every
   10 minutes to stop the free tier from sleeping.

**Note on data:** warns, notes, filters, AFK etc. live in an SQLite file
(`unkilbonker.db`). Render's free disk is wiped on each deploy - settings
survive restarts/sleep-wakes, but are reset when you push a new version.
For permanent storage, attach a paid disk or self-host on a VPS.

## Command details

- `/tban 30m` / `/tmute 2h` - durations: s, m, h, d, w.
- `/warn` - reply to a user; at the limit (default 3, change with
  `/warnlimit`) they are banned automatically.
- `/purge` - reply to the first message to delete; everything from there to
  the bottom is purged.
- `/kang 🎉🎊` - reply to a sticker or photo; extra emoji arguments set the
  sticker's emojis. First kang creates your pack
  (`t.me/addstickers/unkilbonker_<id>_by_<bot>`), later kangs add to it.
- `/getsticker` - reply to a sticker to receive the raw file as a document.

## Extending

Note: the `assets/fonts/` folder must be uploaded too - the image renderers use it.

Everything is plain modules: `moderation.py` (bans/mutes/warns), `feds.py` (federations + global bans), `captcha.py` (join gates + anti-raid), `utilities.py` (connect/export/import), `quotly.py` (quote stickers), `tagall.py`, `memefi.py`, `extras.py` (promote/invite/group settings),
`features.py` (welcome/notes/filters/locks/afk), `stickers.py` (kang),
`rewriter.py` (AI), `db.py` (SQLite). Add a handler function and register it
in `bot.py`'s `register()` - that's the whole pattern.
