"""SQLite storage for UnkilBonker (warns, notes, filters, settings, afk, packs)."""

import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("DB_PATH", "unkilbonker.db")
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER, key TEXT, value TEXT,
                PRIMARY KEY (chat_id, key));
            CREATE TABLE IF NOT EXISTS warns (
                chat_id INTEGER, user_id INTEGER,
                count INTEGER DEFAULT 0, reasons TEXT DEFAULT '[]',
                PRIMARY KEY (chat_id, user_id));
            CREATE TABLE IF NOT EXISTS notes (
                chat_id INTEGER, name TEXT, text TEXT,
                PRIMARY KEY (chat_id, name));
            CREATE TABLE IF NOT EXISTS filters (
                chat_id INTEGER, trigger TEXT, text TEXT,
                PRIMARY KEY (chat_id, trigger));
            CREATE TABLE IF NOT EXISTS afk (
                user_id INTEGER PRIMARY KEY, reason TEXT, since REAL);
            CREATE TABLE IF NOT EXISTS packs (
                user_id INTEGER PRIMARY KEY, name TEXT, title TEXT);
            CREATE TABLE IF NOT EXISTS packs2 (
                user_id INTEGER, fmt TEXT, name TEXT, title TEXT,
                PRIMARY KEY (user_id, fmt));
            CREATE TABLE IF NOT EXISTS feds (
                fed_id TEXT PRIMARY KEY, owner_id INTEGER, name TEXT);
            CREATE TABLE IF NOT EXISTS fed_admins (
                fed_id TEXT, user_id INTEGER,
                PRIMARY KEY (fed_id, user_id));
            CREATE TABLE IF NOT EXISTS fed_chats (
                chat_id INTEGER PRIMARY KEY, fed_id TEXT, title TEXT);
            CREATE TABLE IF NOT EXISTS fbans (
                fed_id TEXT, user_id INTEGER, reason TEXT, banner_id INTEGER,
                PRIMARY KEY (fed_id, user_id));
            CREATE TABLE IF NOT EXISTS gbans (
                user_id INTEGER PRIMARY KEY, reason TEXT, banner_id INTEGER);
            CREATE TABLE IF NOT EXISTS known_chats (
                chat_id INTEGER PRIMARY KEY, title TEXT, seen_at REAL);
            CREATE TABLE IF NOT EXISTS blocklist (
                chat_id INTEGER, word TEXT,
                PRIMARY KEY (chat_id, word));
            CREATE TABLE IF NOT EXISTS allowlist (
                chat_id INTEGER, domain TEXT,
                PRIMARY KEY (chat_id, domain));
            CREATE TABLE IF NOT EXISTS connections (
                user_id INTEGER PRIMARY KEY, chat_id INTEGER);
            CREATE TABLE IF NOT EXISTS fed_subs (
                child_fed TEXT, parent_fed TEXT,
                PRIMARY KEY (child_fed, parent_fed));
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id INTEGER, user_id INTEGER, name TEXT, username TEXT,
                PRIMARY KEY (chat_id, user_id));
            CREATE TABLE IF NOT EXISTS approved (
                chat_id INTEGER, user_id INTEGER, name TEXT,
                PRIMARY KEY (chat_id, user_id));
            CREATE TABLE IF NOT EXISTS conn_last (
                user_id INTEGER PRIMARY KEY, chat_id INTEGER);
            """
        )


# ---------------------------------------------------------------- settings

def get_setting(chat_id: int, key: str, default=None):
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE chat_id=? AND key=?",
            (chat_id, key)).fetchone()
    return row[0] if row else default


def set_setting(chat_id: int, key: str, value) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO settings VALUES (?,?,?) "
            "ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value",
            (chat_id, key, str(value)))


def get_locks(chat_id: int) -> set:
    raw = get_setting(chat_id, "locks")
    try:
        return set(json.loads(raw)) if raw else set()
    except (ValueError, TypeError):
        return set()


def set_locks(chat_id: int, locks: set) -> None:
    set_setting(chat_id, "locks", json.dumps(sorted(locks)))


# ---------------------------------------------------------------- warns

def add_warn(chat_id: int, user_id: int, reason: str = "") -> int:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT count, reasons FROM warns WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)).fetchone()
        count = (row[0] if row else 0) + 1
        reasons = json.loads(row[1]) if row and row[1] else []
        if reason:
            reasons = (reasons + [reason])[-10:]
        conn.execute(
            "INSERT INTO warns VALUES (?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count,"
            " reasons=excluded.reasons",
            (chat_id, user_id, count, json.dumps(reasons)))
    return count


def get_warns(chat_id: int, user_id: int) -> tuple[int, list]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT count, reasons FROM warns WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)).fetchone()
    return (row[0], json.loads(row[1] or "[]")) if row else (0, [])


def reset_warns(chat_id: int, user_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?",
                     (chat_id, user_id))


# ---------------------------------------------------------------- notes

def save_note(chat_id: int, name: str, text: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO notes VALUES (?,?,?) "
            "ON CONFLICT(chat_id,name) DO UPDATE SET text=excluded.text",
            (chat_id, name.lower(), text))


def get_note(chat_id: int, name: str):
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT text FROM notes WHERE chat_id=? AND name=?",
            (chat_id, name.lower())).fetchone()
    return row[0] if row else None


def del_note(chat_id: int, name: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM notes WHERE chat_id=? AND name=?",
                           (chat_id, name.lower()))
    return cur.rowcount > 0


def list_notes(chat_id: int) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT name FROM notes WHERE chat_id=? ORDER BY name",
            (chat_id,)).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------- filters

def save_filter(chat_id: int, trigger: str, text: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO filters VALUES (?,?,?) "
            "ON CONFLICT(chat_id,trigger) DO UPDATE SET text=excluded.text",
            (chat_id, trigger.lower(), text))


def del_filter(chat_id: int, trigger: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM filters WHERE chat_id=? AND trigger=?",
                           (chat_id, trigger.lower()))
    return cur.rowcount > 0


def get_filters(chat_id: int) -> dict:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT trigger, text FROM filters WHERE chat_id=?",
            (chat_id,)).fetchall()
    return dict(rows)


# ---------------------------------------------------------------- afk

def set_afk(user_id: int, reason: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO afk VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason,"
            " since=excluded.since",
            (user_id, reason, time.time()))


def get_afk(user_id: int):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT reason, since FROM afk WHERE user_id=?",
                           (user_id,)).fetchone()
    return (row[0], row[1]) if row else None


def clear_afk(user_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM afk WHERE user_id=?", (user_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------- packs

def save_pack(user_id: int, fmt: str, name: str, title: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO packs2 VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, fmt) DO UPDATE SET name=excluded.name,"
            " title=excluded.title",
            (user_id, fmt, name, title))
        conn.execute("INSERT INTO packs VALUES (?,?,?) ON CONFLICT(user_id) "
                    "DO UPDATE SET name=excluded.name, title=excluded.title",
                    (user_id, name, title))


def get_packs(user_id: int) -> list:
    """[(fmt, name, title)] for every pack style the user owns."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT fmt, name, title FROM packs2 WHERE user_id=?",
            (user_id,)).fetchall()
    return rows


def get_pack(user_id: int, fmt: str = "static"):
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT name, title FROM packs2 WHERE user_id=? AND fmt=?",
            (user_id, fmt)).fetchone()
    return (row[0], row[1]) if row else None


# ---------------------------------------------------------------- feds

def create_fed(owner_id: int, name: str) -> str:
    import secrets
    while True:
        fed_id = secrets.token_hex(4)
        with _lock, _connect() as conn:
            cur = conn.execute(
                "INSERT INTO feds VALUES (?,?,?)",
                (fed_id, owner_id, name))
        return fed_id  # token_hex collision chance ~1/4bn per try


def get_fed(fed_id: str):
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT owner_id, name FROM feds WHERE fed_id=?",
            (fed_id,)).fetchone()
    return {"fed_id": fed_id, "owner_id": row[0], "name": row[1]} if row else None


def delete_fed(fed_id: str) -> None:
    with _lock, _connect() as conn:
        for table in ("feds", "fed_admins", "fed_chats", "fbans"):
            conn.execute(f"DELETE FROM {table} WHERE fed_id=?", (fed_id,))


def fed_add_admin(fed_id: str, user_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO fed_admins VALUES (?,?)",
                     (fed_id, user_id))


def fed_remove_admin(fed_id: str, user_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM fed_admins WHERE fed_id=? AND user_id=?",
                     (fed_id, user_id))


def fed_admins_list(fed_id: str) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT user_id FROM fed_admins WHERE fed_id=?",
            (fed_id,)).fetchall()
    return [r[0] for r in rows]


def is_fed_admin(fed: dict, user_id: int) -> bool:
    return user_id == fed["owner_id"] or user_id in fed_admins_list(fed["fed_id"])


def join_fed(fed_id: str, chat_id: int, title: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO fed_chats VALUES (?,?,?)",
                     (chat_id, fed_id, title))


def leave_fed(chat_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM fed_chats WHERE chat_id=?", (chat_id,))


def chat_fed(chat_id: int):
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT fed_id, title FROM fed_chats WHERE chat_id=?",
            (chat_id,)).fetchone()
    return get_fed(row[0]) if row else None


def fed_chats_list(fed_id: str) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT chat_id, title FROM fed_chats WHERE fed_id=?",
            (fed_id,)).fetchall()
    return rows


def add_fban(fed_id: str, user_id: int, reason: str, banner_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO fbans VALUES (?,?,?,?) "
            "ON CONFLICT(fed_id,user_id) DO UPDATE SET reason=excluded.reason,"
            " banner_id=excluded.banner_id",
            (fed_id, user_id, reason, banner_id))


def remove_fban(fed_id: str, user_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM fbans WHERE fed_id=? AND user_id=?", (fed_id, user_id))
    return cur.rowcount > 0


def get_fban(fed_id: str, user_id: int):
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT reason, banner_id FROM fbans WHERE fed_id=? AND user_id=?",
            (fed_id, user_id)).fetchone()
    return (row[0], row[1]) if row else None


def list_fbans(fed_id: str) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, reason FROM fbans WHERE fed_id=? LIMIT 60",
            (fed_id,)).fetchall()
    return rows


def is_fed_banned(chat_id: int, user_id: int) -> str | None:
    """Return the fed name if user is fed-banned in this chat, else None.

    Also checks parent feds that this chat's fed subscribes to.
    """
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT f.name FROM fed_chats fc "
            "JOIN fbans b ON b.fed_id = fc.fed_id OR b.fed_id IN "
            "  (SELECT parent_fed FROM fed_subs WHERE child_fed = fc.fed_id) "
            "JOIN feds f ON f.fed_id = b.fed_id "
            "WHERE fc.chat_id=? AND b.user_id=?", (chat_id, user_id)).fetchone()
    return row[0] if row else None


def user_fed_bans(user_id: int) -> list:
    """All feds a user is banned from."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT f.name, b.reason FROM fbans b JOIN feds f "
            "ON f.fed_id = b.fed_id WHERE b.user_id=?", (user_id,)).fetchall()
    return rows


# ---------------------------------------------------------------- gbans

def add_gban(user_id: int, reason: str, banner_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO gbans VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason,"
            " banner_id=excluded.banner_id", (user_id, reason, banner_id))


def remove_gban(user_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM gbans WHERE user_id=?", (user_id,))
    return cur.rowcount > 0


def get_gban(user_id: int):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT reason FROM gbans WHERE user_id=?",
                           (user_id,)).fetchone()
    return row[0] if row else None


def list_gbans() -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, reason FROM gbans LIMIT 60").fetchall()
    return rows


# ------------------------------------------------------- known chats

def upsert_chat(chat_id: int, title: str) -> None:
    import time as _t
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO known_chats VALUES (?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,"
            " seen_at=excluded.seen_at", (chat_id, title, _t.time()))


def all_chats() -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT chat_id, title FROM known_chats").fetchall()
    return rows


# ------------------------------------------------------------ blocklist

def add_blocklist(chat_id: int, word: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO blocklist VALUES (?,?)",
                     (chat_id, word.lower()))


def del_blocklist(chat_id: int, word: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM blocklist WHERE chat_id=? AND word=?",
            (chat_id, word.lower()))
    return cur.rowcount > 0


def list_blocklist(chat_id: int) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT word FROM blocklist WHERE chat_id=?", (chat_id,)).fetchall()
    return [r[0] for r in rows]


def clear_blocklist(chat_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM blocklist WHERE chat_id=?", (chat_id,))


# ------------------------------------------------------------ allowlist

def add_allowlist(chat_id: int, domain: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO allowlist VALUES (?,?)",
                     (chat_id, domain.lower().strip("/")))


def del_allowlist(chat_id: int, domain: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM allowlist WHERE chat_id=? AND domain=?",
            (chat_id, domain.lower().strip("/")))
    return cur.rowcount > 0


def list_allowlist(chat_id: int) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT domain FROM allowlist WHERE chat_id=?", (chat_id,)).fetchall()
    return [r[0] for r in rows]


def is_url_allowed(chat_id: int, url: str) -> bool:
    """True if any whitelisted domain is a suffix of the url host."""
    dom = url.lower().split("//")[-1].split("/")[0].lstrip("www.")
    for allowed in list_allowlist(chat_id):
        if dom == allowed or dom.endswith("." + allowed):
            return True
    return False


# ------------------------------------------------------------ connections

def set_connection(user_id: int, chat_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO connections VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id",
            (user_id, chat_id))


def get_connection(user_id: int):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT chat_id FROM connections WHERE user_id=?",
                           (user_id,)).fetchone()
    return row[0] if row else None


def del_connection(user_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM connections WHERE user_id=?",
                           (user_id,))
    return cur.rowcount > 0


# ------------------------------------------------------------ fed subs

def fed_subscribe(child_fed: str, parent_fed: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO fed_subs VALUES (?,?)",
                     (child_fed, parent_fed))


def fed_unsubscribe(child_fed: str, parent_fed: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM fed_subs WHERE child_fed=? AND parent_fed=?",
            (child_fed, parent_fed))
    return cur.rowcount > 0


def fed_subscriptions(parent_fed: str) -> list:
    """Feds subscribed to this fed's ban feed."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT child_fed FROM fed_subs WHERE parent_fed=?",
            (parent_fed,)).fetchall()
    return [r[0] for r in rows]


# -------------------------------------------------- warn helpers (v3)

def dec_warn(chat_id: int, user_id: int) -> int | None:
    """Remove one warn; returns new count or None if there were none."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)).fetchone()
        if not row or row[0] <= 0:
            return None
        conn.execute(
            "UPDATE warns SET count=? WHERE chat_id=? AND user_id=?",
            (row[0] - 1, chat_id, user_id))
    return row[0] - 1


def clear_all_notes(chat_id: int) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM notes WHERE chat_id=?", (chat_id,))
    return cur.rowcount


# ----------------------------------------------------------- chat members

def upsert_member(chat_id: int, user_id: int, name: str, username: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO chat_members VALUES (?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET name=excluded.name,"
            " username=excluded.username",
            (chat_id, user_id, name, username))


def del_member(chat_id: int, user_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM chat_members WHERE chat_id=? AND user_id=?",
                     (chat_id, user_id))


def get_members(chat_id: int) -> list:
    """[(user_id, name, username)] known members (bot's own id excluded)."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, name, username FROM chat_members WHERE chat_id=?",
            (chat_id,)).fetchall()
    return rows


# ---------------------------------------------------------------- approvals

def approve(chat_id: int, user_id: int, name: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO approved VALUES (?,?,?)",
                     (chat_id, user_id, name))


def unapprove(chat_id: int, user_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM approved WHERE chat_id=? AND user_id=?",
                           (chat_id, user_id))
    return cur.rowcount > 0


def unapprove_all(chat_id: int) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM approved WHERE chat_id=?", (chat_id,))
    return cur.rowcount


def is_approved(chat_id: int, user_id: int) -> bool:
    with _lock, _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM approved WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)).fetchone() is not None


def approved_list(chat_id: int) -> list:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, name FROM approved WHERE chat_id=?",
            (chat_id,)).fetchall()
    return rows


# ------------------------------------------------------- fed extras

def rename_fed(fed_id: str, name: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("UPDATE feds SET name=? WHERE fed_id=?", (name, fed_id))


def transfer_fed(fed_id: str, new_owner: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("UPDATE feds SET owner_id=? WHERE fed_id=?",
                     (new_owner, fed_id))


def my_feds(user_id: int) -> list:
    """Feds the user owns or administers: [(fed_id, name, role)]."""
    with _lock, _connect() as conn:
        owned = conn.execute(
            "SELECT fed_id, name FROM feds WHERE owner_id=?",
            (user_id,)).fetchall()
        admin_of = conn.execute(
            "SELECT f.fed_id, f.name FROM fed_admins a JOIN feds f "
            "ON f.fed_id = a.fed_id WHERE a.user_id=?", (user_id,)).fetchall()
    return ([(f, n, "owner") for f, n in owned]
            + [(f, n, "admin") for f, n in admin_of])


# ------------------------------------------------------- connections

def set_conn_last(user_id: int, chat_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO conn_last VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id",
            (user_id, chat_id))


def get_conn_last(user_id: int):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT chat_id FROM conn_last WHERE user_id=?",
                           (user_id,)).fetchone()
    return row[0] if row else None
