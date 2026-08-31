from __future__ import annotations
import re

# Быстрые regex до LLM
URL_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)", re.I)
MENTION_LINK_RE = re.compile(r"@\w{4,}")
INVITE_RE = re.compile(r"(joinchat|\+[A-Za-z0-9_-]{10,})")

def has_link(text: str) -> bool:
    return bool(URL_RE.search(text or "") or MENTION_LINK_RE.search(text or ""))

def should_prefilter(text: str) -> bool:
    """Если есть ссылка — точно проверять LLM."""
    return has_link(text)
