from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

# Единый детектор ссылок — согласован с src/ai/vega_client.py
URL_RE = re.compile(
    r"(https?://|t\.me/|telegram\.me/|www\.|\b[\w-]+\.(ru|com|net|io|me|xyz|online|shop|site|info|biz|pro|fun|top|click|link|store|app|su|by|ua|kz|рф)\b)",
    re.I,
)
MENTION_LINK_RE = re.compile(r"@\w{4,}")
INVITE_RE = re.compile(r"(joinchat|\+[A-Za-z0-9_-]{10,})")

# Запрещённые темы — используются эвристикой до LLM и strict-fallback при ошибке LLM
CASINO_RE = re.compile(r"(казино|casino|1xbet|ставки|слот|jackpot|крипта|биток|инвест|заработай|пирамида|vavada|pin-?up|stake|букмекер|фриспин)", re.I)
SEX_RE = re.compile(r"(onlyfans|эскорт|интим|порно|sex|18\+|приват|эротик|вебкам|webcam|шлюх|проститут|индивидуалк)", re.I)
DRUGS_RE = re.compile(r"(закладк|наркот|мефедрон|шишки|амфетамин|cocaine|weed|героин|метадон|спайс|кладмен|трамадол|lsd|мдма|mdma|гашиш)", re.I)


def has_link(text: str) -> bool:
    t = text or ""
    return bool(URL_RE.search(t) or MENTION_LINK_RE.search(t))


def has_url(text: str) -> bool:
    """Только реальные ссылки (без @mention) — для строгого probation,
    чтобы упоминание друга не стало нарушением."""
    return bool(URL_RE.search(text or ""))


def has_banned_topic(text: str) -> bool:
    t = text or ""
    return bool(CASINO_RE.search(t) or SEX_RE.search(t) or DRUGS_RE.search(t))


def should_prefilter(text: str) -> bool:
    """Если есть ссылка — точно проверять LLM."""
    return has_link(text)


def extract_hidden_urls(entities: Any, reply_markup: Any) -> list[str]:
    """Ссылки, которые не видны в тексте сообщения:
    - text_link entities (слово-гиперссылка, Telegram Desktop Ctrl+K)
    - inline-кнопки с url (reply_markup от ботов)
    Принимает duck-typed объекты aiogram (MessageEntity/InlineKeyboardMarkup) — тестируется без aiogram.
    """
    out: list[str] = []
    for e in entities or []:
        url = getattr(e, "url", None)
        if getattr(e, "type", None) == "text_link" and url:
            out.append(url)
    markup = getattr(reply_markup, "inline_keyboard", None) or []
    for row in markup:
        for btn in row or []:
            url = getattr(btn, "url", None)
            if url:
                out.append(url)
    return out


def normalize_text(text: str) -> str:
    """Нормализация для кэш-хэша: NFKC, lower, схлопнуть пробелы."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"\s+", " ", t.strip().lower())
    return t


def verdict_hash(text: str) -> str:
    return hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()
