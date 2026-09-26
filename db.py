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

def save_pack(user_id: int, name: str, title: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO packs VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,"
            " title=excluded.title",
            (user_id, name, title))


def get_pack(user_id: int):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT name, title FROM packs WHERE user_id=?",
                           (user_id,)).fetchone()
    return (row[0], row[1]) if row else None
