from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite

DB_PATH = Path("data/bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS recent_messages (
  chat_id INTEGER,
  message_id INTEGER,
  user_id INTEGER,
  text TEXT,
  ts INTEGER,
  PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS captcha_state (
  chat_id INTEGER,
  user_id INTEGER,
  expected INTEGER,
  trap_expected INTEGER,
  created_ts INTEGER,
  attempts INTEGER DEFAULT 0,
  PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT
);
CREATE INDEX IF NOT EXISTS idx_recent_chat_ts ON recent_messages(chat_id, ts);
CREATE TABLE IF NOT EXISTS violations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  category TEXT,
  reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_violations_user_chat_ts ON violations(chat_id, user_id, ts);
CREATE TABLE IF NOT EXISTS mute_state (
  chat_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  level INTEGER NOT NULL DEFAULT 0,
  until_ts INTEGER,
  PRIMARY KEY (chat_id, user_id)
);
"""

_db_init_lock = asyncio.Lock()
_db_initialized = False


async def init_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    async with _db_init_lock:
        if _db_initialized:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        _db_initialized = True


async def save_recent_message(chat_id: int, message_id: int, user_id: int, text: str | None) -> None:
    ts = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO recent_messages(chat_id,message_id,user_id,text,ts) VALUES(?,?,?,?,?)",
            (chat_id, message_id, user_id, (text or "")[:4000], ts),
        )
        await db.execute(
            """
          DELETE FROM recent_messages WHERE rowid NOT IN (
            SELECT rowid FROM recent_messages WHERE chat_id=? ORDER BY ts DESC, rowid DESC LIMIT 3
          ) AND chat_id=?
        """,
            (chat_id, chat_id),
        )
        await db.commit()


async def get_recent_messages(chat_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id,message_id,user_id,text,ts FROM recent_messages WHERE chat_id=? ORDER BY ts DESC LIMIT 3",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def set_captcha(chat_id: int, user_id: int, expected: int, trap: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO captcha_state(chat_id,user_id,expected,trap_expected,created_ts,attempts) VALUES(?,?,?,?,?,0)",
            (chat_id, user_id, expected, trap, int(time.time())),
        )
        await db.commit()


async def get_captcha(chat_id: int, user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM captcha_state WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None


async def del_captcha(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM captcha_state WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await db.commit()


async def kv_get(k: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (k,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None


async def kv_set(k: str, v: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))
        await db.commit()


# --- violations / mute escalation ---
async def add_violation(chat_id: int, user_id: int, category: str = "", reason: str = "") -> tuple[int, int, int]:
    ts = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO violations(chat_id,user_id,ts,category,reason) VALUES(?,?,?,?,?)",
            (chat_id, user_id, ts, category, reason),
        )
        await db.commit()
        async with db.execute(
            "SELECT COUNT(*) FROM violations WHERE chat_id=? AND user_id=? AND ts > ?", (chat_id, user_id, ts - 86400)
        ) as cur:
            c24 = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM violations WHERE chat_id=? AND user_id=? AND ts > ?",
            (chat_id, user_id, ts - 3 * 86400),
        ) as cur:
            c3d = (await cur.fetchone())[0]
        async with db.execute("SELECT level FROM mute_state WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            row = await cur.fetchone()
            level = row[0] if row else 0
    return c24, c3d, level


async def set_mute_level(chat_id: int, user_id: int, level: int, until_ts: int | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO mute_state(chat_id,user_id,level,until_ts) VALUES(?,?,?,?)",
            (chat_id, user_id, level, until_ts),
        )
        await db.commit()


async def get_mute_level(chat_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT level FROM mute_state WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0
