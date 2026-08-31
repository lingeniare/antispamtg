from __future__ import annotations
import json
import re
from openai import AsyncOpenAI
from src.config import load_settings, filter_prompt

SYSTEM_FALLBACK = "Ты модератор. Ответь JSON {\"spam\":bool,\"reason\":str,\"category\":str}"

_link_re = re.compile(
    r"(https?://|t\.me/|telegram\.me/|www\.|@\w+|\b[\w-]+\.(ru|com|net|io|me|xyz|online|shop|site|info|biz|pro|fun|top|click|link|store|app|su|by|ua|kz|рф)\b)",
    re.I,
)
_casino_re = re.compile(r"(казино|casino|1xbet|ставки|слот|jackpot|крипта|биток|инвест|заработай|пирамида)", re.I)
_sex_re = re.compile(r"(onlyfans|эскорт|интим|порно|sex|18\+|приват|эротик)", re.I)
_drugs_re = re.compile(r"(закладк|наркот|мефедрон|шишки|амфетамин|cocaine|weed)", re.I)

def heuristic_spam(text: str) -> tuple[bool, str] | None:
    """Быстрые эвристики до вызова LLM. Возвращает (is_spam, reason) или None если неясно."""
    if not text:
        return None
    # если есть ссылка + ключевые слова — точно спам, не тратим LLM
    has_link = bool(_link_re.search(text))
    if has_link and (_casino_re.search(text) or _sex_re.search(text) or _drugs_re.search(text)):
        return True, "heuristic: link + banned topic"
    # одиночная ссылка без контекста — подозрительно, пусть решает LLM (return None)
    return None

async def ai_is_spam(text: str, image_url: str | None = None) -> dict:
    """
    Возвращает {"spam":bool,"reason":str,"category":str,"via":"heuristic|llm"}
    """
    h = heuristic_spam(text or "")
    if h is not None:
        is_spam, reason = h
        return {"spam": is_spam, "reason": reason, "category": "heuristic", "via": "heuristic"}

    s = load_settings()
    if not s.vega_api_key:
        # без ключа — только эвристики
        has_link = bool(_link_re.search(text or ""))
        return {"spam": has_link, "reason": "no api key, link heuristic", "category": "link" if has_link else "ok", "via": "heuristic"}

    prompt = filter_prompt() or SYSTEM_FALLBACK
    client = AsyncOpenAI(api_key=s.vega_api_key, base_url=s.vega_base_url)
    content: list[dict] = [{"type":"text","text": f"Сообщение:\n{text[:3000]}\n\nВерни только JSON."}]
    # image moderation (если модель vision) — пока опционально
    if image_url:
        content.append({"type":"image_url","image_url":{"url": image_url}})

    try:
        resp = await client.chat.completions.create(
            model=s.vega_model,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],  # type: ignore
            temperature=0.1,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content or "{}"
        m = re.search(r"\{.*\}", raw, re.S)
        j = json.loads(m.group(0)) if m else {}
        spam = bool(j.get("spam"))
        return {"spam": spam, "reason": j.get("reason", "llm"), "category": j.get("category", "other"), "via": "llm", "raw": raw[:500]}
    except Exception as e:
        import logging
        logging.getLogger("tg-antispam").warning("Vega LLM error model=%s: %s", s.vega_model, e)
        return {"spam": False, "reason": f"llm error: {e}", "category": "error", "via": "llm"}
