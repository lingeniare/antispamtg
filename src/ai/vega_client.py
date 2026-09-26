from __future__ import annotations

import json
import logging
import re

import httpx
from openai import AsyncOpenAI

from src.config import filter_prompt, load_settings
from src.filters.content import CASINO_RE, DRUGS_RE, SEX_RE, has_banned_topic, has_link

SYSTEM_FALLBACK = 'Ты модератор. Ответь JSON {"spam":bool,"reason":str,"category":str}'

# совместимость со старыми именами
_casino_re = CASINO_RE
_sex_re = SEX_RE
_drugs_re = DRUGS_RE

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
    if has_link(text) and has_banned_topic(text):
        return True, "heuristic: link + banned topic"
    _ = is_forward  # зарезервировано; [FORWARDED] решает LLM/strict-fallback
    return None


def strict_fallback(text: str, is_forward: bool = False) -> dict | None:
    """Жёсткие правила на случай недоступности LLM (вместо чистого fail-open).
    Ссылка (включая скрытые [LINK:]) или форвард с запрещённой темой = спам."""
    if not text:
        return None
    if has_link(text):
        return {
            "spam": True,
            "reason": "strict fallback: link while LLM down",
            "category": "link",
            "via": "strict",
        }
    if is_forward and has_banned_topic(text):
        return {
            "spam": True,
            "reason": "strict fallback: forwarded banned topic",
            "category": "other",
            "via": "strict",
        }
    return None


async def ai_is_spam(
    text: str,
    image_url: str | None = None,
    is_forward: bool = False,
    model: str | None = None,
    context: list[str] | None = None,
) -> dict:
    """
    Возвращает {"spam":bool,"reason":str,"category":str,"via":"heuristic|llm"}.
    При ошибке LLM — {"spam": False, "error": True, ...}: вызывающий код решает
    через strict_fallback, чистого fail-open больше нет.
    """
    h = heuristic_spam(text or "", is_forward=is_forward)
    if h is not None:
        is_spam, reason = h
        return {"spam": is_spam, "reason": reason, "category": "heuristic", "via": "heuristic"}

    s = load_settings()
    if not s.vega_api_key:
        if has_link(text or ""):
            return {
                "spam": True,
                "reason": "no api key, link heuristic" + (" + forward" if is_forward else ""),
                "category": "link",
                "via": "heuristic",
            }
        return {"spam": False, "reason": "no api key", "category": "ok", "via": "heuristic"}

    prompt = filter_prompt() or SYSTEM_FALLBACK
    client = _get_client(s.vega_api_key, s.vega_base_url)
    prefix = "[FORWARDED] " if is_forward else ""
    ctx = ""
    if context:
        ctx = "Контекст чата (предыдущие сообщения):\n" + "\n".join(f"- {c[:200]}" for c in context[:3]) + "\n"
    content: list[dict] = [{"type": "text", "text": f"{ctx}Сообщение:\n{prefix}{text[:3000]}\n\nВерни только JSON."}]
    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})

    try:
        resp = await client.chat.completions.create(
            model=model or s.vega_model,
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
        log.warning("Vega LLM error model=%s: %s", model or s.vega_model, e)
        return {"spam": False, "reason": f"llm error: {e}", "category": "error", "via": "llm", "error": True}


async def ai_chat_reply(
    prompt: str,
    content: list[dict],
    model: str,
    max_tokens: int,
    web_search: bool = False,
) -> str | None:
    """Ответ личности ВЕГА. web_search=True → суффикс :online к модели
    (OpenRouter web-search, api.vega.chat — тот же прокси).
    None при ошибке или если модель ответила [SILENT]."""
    s = load_settings()
    if not s.vega_api_key:
        return None
    client = _get_client(s.vega_api_key, s.vega_base_url)
    use_model = model + ":online" if web_search and not model.endswith(":online") else model
    msgs = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
    # reasoning.effort=low: OpenRouter-параметр, просим модель думать мало —
    # «размышления» едят тот же max_tokens, что и ответ. Неподдержанное игнорится,
    # при ошибке параметра ретраим без него.
    for budget in (max_tokens, min(max_tokens * 20, 4096)):
        for extra in ({"reasoning": {"effort": "low"}}, None):
            try:
                resp = await client.chat.completions.create(
                    model=use_model,
                    messages=msgs,  # type: ignore
                    temperature=0.8,
                    max_tokens=budget,
                    extra_body=extra,
                )
            except Exception as e:
                log.warning("Vega chat error model=%s extra=%s: %s", use_model, extra, e)
                if extra is None:
                    return None
                continue
            out = (resp.choices[0].message.content or "").strip()
            if out:
                if "[SILENT]" in out or out.startswith("[IGNORE]"):
                    return None
                return out
            # пусто + length: reasoning съел весь бюджет → следующий, бóльший
            fr = getattr(resp.choices[0], "finish_reason", None)
            log.info("vega chat empty content finish=%s budget=%s", fr, budget)
            if fr != "length":
                return None
            break
    return None
