from __future__ import annotations

import re

# Единый детектор ссылок — согласован с src/ai/vega_client.py _link_re
URL_RE = re.compile(
    r"(https?://|t\.me/|telegram\.me/|www\.|\b[\w-]+\.(ru|com|net|io|me|xyz|online|shop|site|info|biz|pro|fun|top|click|link|store|app|su|by|ua|kz|рф)\b)",
    re.I,
)
MENTION_LINK_RE = re.compile(r"@\w{4,}")
INVITE_RE = re.compile(r"(joinchat|\+[A-Za-z0-9_-]{10,})")


def has_link(text: str) -> bool:
    t = text or ""
    return bool(URL_RE.search(t) or MENTION_LINK_RE.search(t))


def should_prefilter(text: str) -> bool:
    """Если есть ссылка — точно проверять LLM."""
    return has_link(text)
