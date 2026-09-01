from __future__ import annotations

import json
import logging
import re

import httpx
from openai import AsyncOpenAI

from src.config import filter_prompt, load_settings

SYSTEM_FALLBACK = 'Ты модератор. Ответь JSON {"spam":bool,"reason":str,"category":str}'

_link_re = re.compile(
    r"(https?://|t\.me/|telegram\.me/|www\.|@\w+|\b[\w-]+\.(ru|com|net|io|me|xyz|online|shop|site|info|biz|pro|fun|top|click|link|store|app|su|by|ua|kz|рф)\b)",
    re.I,
)
_casino_re = re.compile(r"(казино|casino|1xbet|ставки|слот|jackpot|крипта|биток|инвест|заработай|пирамида)", re.I)
_sex_re = re.compile(r"(onlyfans|эскорт|интим|порно|sex|18\+|приват|эротик)", re.I)
_drugs_re = re.compile(r"(закладк|наркот|мефедрон|шишки|амфетамин|cocaine|weed)", re.I)

_client: AsyncOpenAI | None = None
_client_key: tuple[str, str] | None = None

log = logging.getLogger("tg-antispam")


def _get_client(api_key: str, base_url: str) -> AsyncOpenAI:
    global _client, _client_key
    key = (api_key, base_url)
    if _client is None or _client_key != key:
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            max_retries=1,
        )
        _client_key = key
    return _client


def heuristic_spam(text: str, is_forward: bool = False) -> tuple[bool, str] | None:
    """Быстрые эвристики до вызова LLM. Возвращает (is_spam, reason) или None если неясно."""
    if not text:
        return None
    has_link = bool(_link_re.search(text))
    if has_link and (_casino_re.search(text) or _sex_re.search(text) or _drugs_re.search(text)):
        return True, "heuristic: link + banned topic"
    _ = is_forward  # зарезервирован для будущих строгих правил; сейчас LLM решает по [FORWARDED]
    return None


async def ai_is_spam(text: str, image_url: str | None = None, is_forward: bool = False) -> dict:
    """
    Возвращает {"spam":bool,"reason":str,"category":str,"via":"heuristic|llm"}
    """
    h = heuristic_spam(text or "", is_forward=is_forward)
    if h is not None:
        is_spam, reason = h
        return {"spam": is_spam, "reason": reason, "category": "heuristic", "via": "heuristic"}

    s = load_settings()
    if not s.vega_api_key:
        has_link = bool(_link_re.search(text or ""))
        # без ключа: форвард с ссылкой тоже считаем спамом
        return {
            "spam": has_link,
            "reason": "no api key, link heuristic" + (" + forward" if is_forward and has_link else ""),
            "category": "link" if has_link else "ok",
            "via": "heuristic",
        }

    prompt = filter_prompt() or SYSTEM_FALLBACK
    client = _get_client(s.vega_api_key, s.vega_base_url)
    prefix = "[FORWARDED] " if is_forward else ""
    content: list[dict] = [{"type": "text", "text": f"Сообщение:\n{prefix}{text[:3000]}\n\nВерни только JSON."}]
    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})

    try:
        resp = await client.chat.completions.create(
            model=s.vega_model,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],  # type: ignore
            temperature=0.1,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content or "{}"
        m = re.search(r"\{.*?\}", raw, re.S)
        j = json.loads(m.group(0)) if m else {}
        spam = bool(j.get("spam"))
        return {
            "spam": spam,
            "reason": j.get("reason", "llm"),
            "category": j.get("category", "other"),
            "via": "llm",
            "raw": raw[:500],
        }
    except Exception as e:
        log.warning("Vega LLM error model=%s: %s", s.vega_model, e)
        return {"spam": False, "reason": f"llm error: {e}", "category": "error", "via": "llm"}
