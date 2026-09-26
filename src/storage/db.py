from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import aiosqlite

_DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data/bot.db"
DB_PATH = _DEFAULT_DB_PATH  # совместимость: фактический путь берётся в _db()


def _db() -> str:
    # TG_DB_PATH позволяет тестам/деплою указать другой файл
    return os.environ.get("TG_DB_PATH") or str(_DEFAULT_DB_PATH)


SCHEMA = """
CREATE TABLE IF NOT EXISTS recent_messages (
  chat_id INTEGER,
  message_id INTEGER,
  user_id INTEGER,
  text TEXT,
  ts INTEGER,
  PRIMARY KEY (chat_id, message_id)
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
-- испытательный срок: когда юзер зашёл и сколько сообщений проверено
CREATE TABLE IF NOT EXISTS member_state (
  chat_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  joined_ts INTEGER NOT NULL,
  msg_count INTEGER NOT NULL DEFAULT 0,
  flagged INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, user_id)
);
-- кэш вердиктов по хэшу нормализованного текста (TTL-очистка при записи)
CREATE TABLE IF NOT EXISTS verdicts (
  h TEXT PRIMARY KEY,
  spam INTEGER NOT NULL,
  category TEXT,
  reason TEXT,
  ts INTEGER NOT NULL
);
-- кэш вердиктов по file_unique_id — дедуп репостов одной картинки/стикера
CREATE TABLE IF NOT EXISTS media_verdicts (
  file_unique_id TEXT PRIMARY KEY,
  spam INTEGER NOT NULL,
  category TEXT,
  reason TEXT,
  ts INTEGER NOT NULL
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
        Path(_db()).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(_db()) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        _db_initialized = True


async def save_recent_message(chat_id: int, message_id: int, user_id: int, text: str | None) -> None:
    ts = int(time.time())
    async with aiosqlite.connect(_db()) as db:
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
    async with aiosqlite.connect(_db()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id,message_id,user_id,text,ts FROM recent_messages WHERE chat_id=? ORDER BY ts DESC LIMIT 3",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# --- member_state: испытательный срок вместо капчи ---
async def record_join(chat_id: int, user_id: int, ts: int | None = None) -> bool:
    """Пишет факт входа юзера. True если запись новая (защита от двойных триггеров)."""
    async with aiosqlite.connect(_db()) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO member_state(chat_id,user_id,joined_ts,msg_count,flagged) VALUES(?,?,?,0,0)",
            (chat_id, user_id, ts or int(time.time())),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_member(chat_id: int, user_id: int) -> dict | None:
    async with aiosqlite.connect(_db()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM member_state WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None


async def bump_member_msgs(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(_db()) as db:
        await db.execute(
            "UPDATE member_state SET msg_count=msg_count+1 WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        await db.commit()


async def flag_member(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(_db()) as db:
        await db.execute(
            "UPDATE member_state SET flagged=1 WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await db.commit()


# --- кэш вердиктов ---
_VERDICT_TTL = 24 * 3600


async def verdict_get(h: str) -> dict | None:
    async with aiosqlite.connect(_db()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM verdicts WHERE h=? AND ts > ?", (h, int(time.time()) - _VERDICT_TTL)
        ) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None


async def verdict_set(h: str, spam: bool, category: str, reason: str) -> None:
    ts = int(time.time())
    async with aiosqlite.connect(_db()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO verdicts(h,spam,category,reason,ts) VALUES(?,?,?,?,?)",
            (h, int(spam), category, reason, ts),
        )
        await db.execute("DELETE FROM verdicts WHERE ts < ?", (ts - _VERDICT_TTL,))
        await db.commit()


async def media_verdict_get(file_unique_id: str) -> dict | None:
    async with aiosqlite.connect(_db()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM media_verdicts WHERE file_unique_id=? AND ts > ?",
            (file_unique_id, int(time.time()) - _VERDICT_TTL),
        ) as cur:
            r = await cur.fetchone()
            return dict(r) if r else None


async def media_verdict_set(file_unique_id: str, spam: bool, category: str, reason: str) -> None:
    ts = int(time.time())
    async with aiosqlite.connect(_db()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO media_verdicts(file_unique_id,spam,category,reason,ts) VALUES(?,?,?,?,?)",
            (file_unique_id, int(spam), category, reason, ts),
        )
        await db.execute("DELETE FROM media_verdicts WHERE ts < ?", (ts - _VERDICT_TTL,))
        await db.commit()


async def kv_get(k: str) -> str | None:
    async with aiosqlite.connect(_db()) as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (k,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None


async def kv_set(k: str, v: str) -> None:
    async with aiosqlite.connect(_db()) as db:
        await db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))
        await db.commit()


# --- violations / mute escalation ---
async def add_violation(chat_id: int, user_id: int, category: str = "", reason: str = "") -> tuple[int, int, int]:
    ts = int(time.time())
    async with aiosqlite.connect(_db()) as db:
        await db.execute(
            "INSERT INTO violations(chat_id,user_id,ts,category,reason) VALUES(?,?,?,?,?)",
            (chat_id, user_id, ts, category, reason),
        )
        # TTL-чистка: удаляем старше 7 дней, чтобы БД не раздувалась
        await db.execute("DELETE FROM violations WHERE ts < ?", (ts - 7 * 86400,))
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
    async with aiosqlite.connect(_db()) as db:
        await db.execute(
            "INSERT OR REPLACE INTO mute_state(chat_id,user_id,level,until_ts) VALUES(?,?,?,?)",
            (chat_id, user_id, level, until_ts),
        )
        await db.commit()


async def get_mute_level(chat_id: int, user_id: int) -> int:
    async with aiosqlite.connect(_db()) as db:
        async with db.execute("SELECT level FROM mute_state WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0
