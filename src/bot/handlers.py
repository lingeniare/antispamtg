from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, ChatPermissions, Message, User

from src.ai.media import resolve_image
from src.ai.vega_client import ai_chat_reply, ai_is_spam, strict_fallback
from src.config import chat_prompt, load_settings
from src.filters.content import (
    extract_hidden_urls,
    has_banned_topic,
    has_link,
    has_url,
    verdict_hash,
)
from src.storage.db import (
    add_violation,
    bump_member_msgs,
    flag_member,
    get_member,
    get_recent_messages,
    media_verdict_get,
    media_verdict_set,
    record_join,
    save_recent_message,
    set_mute_level,
    verdict_get,
    verdict_set,
)

router = Router()
log = logging.getLogger("tg-antispam")

# --- in-memory состояние ---
_admins_cache: dict[int, tuple[float, set[int]]] = {}  # chat_id -> (ts, admin_ids)
_ADMINS_TTL = 600
_rate: dict[tuple[int, int], deque[float]] = {}  # (chat_id, user_id) -> ts окна (модерация)
_chat_rate: dict[tuple[int, int], deque[float]] = {}  # отдельный счётчик чата — иначе модерация+чат считают дважды
_notify_ts: dict[int, deque[float]] = {}  # chat_id -> ts отправленных уведомлений
_suppressed: dict[int, int] = {}  # chat_id -> сколько уведомлений подавлено
_summary_task: dict[int, asyncio.Task] = {}
_PROFILE_SCANNED: set[tuple[int, int]] = set()  # био сканим один раз за процесс
_bot_me_cache: tuple[float, object | None] = (0, None)
_NAME_RE = re.compile(r"\b(дейнерис|дени|daenerys|dany|кхалиси|khaleesi|вега|vega)\b", re.I)


def _is_stale(m: Message, limit_sec: int = 300) -> bool:
    """Сообщение из буфера даунтайма: модерировать можно, отвечать — нет
    (Дейнерис не должна отвечать на вчерашние «приветы» после рестарта)."""
    d = getattr(m, "edit_date", None) or getattr(m, "date", None)
    if not d:
        return False
    if getattr(d, "tzinfo", None) is None:
        d = d.replace(tzinfo=UTC)
    return (datetime.now(UTC) - d).total_seconds() > limit_sec
# поисковый интент: с такими словами чат-ответ идёт через :online (веб-поиск)
_WEB_RE = re.compile(
    r"(найди|поищи|погугли|загугли|поиск|новост|актуальн|свеж|последн|"
    r"сейчас|сегодня|на данный момент|сколько стоит|цена|курс|погода|выиграл|"
    r"когда вышл|какая версия|latest|search|look ?up|news|today|current|"
    r"right now|price|weather|who won|recent)",
    re.I,
)


async def _bot_me(bot) -> object | None:
    """Кэш get_me — нужен для детекта @упоминания бота."""
    global _bot_me_cache
    now = time.monotonic()
    if _bot_me_cache[1] is not None and now - _bot_me_cache[0] < 3600:
        return _bot_me_cache[1]
    try:
        me = await bot.get_me()
        _bot_me_cache = (now, me)
        return me
    except Exception as e:
        log.warning("get_me failed: %s", e)
        return None


def is_group_chat(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup", "channel")


def is_allowed_chat(chat_id: int, username: str | None) -> bool:
    s = load_settings()
    lst = s.allowed_chat_list
    if not lst:
        return True
    chat_str = str(chat_id)
    norm = [c.strip().lower().lstrip("@") for c in lst]
    uname = (username or "").strip().lower().lstrip("@")
    return chat_str in lst or chat_str in norm or uname in norm


def is_whitelisted(user_id: int) -> bool:
    return user_id in load_settings().whitelist_list


def is_admin(user_id: int) -> bool:
    return user_id in load_settings().admin_list


def _is_forward(m: Message) -> bool:
    return bool(
        getattr(m, "forward_origin", None)
        or getattr(m, "forward_from", None)
        or getattr(m, "forward_from_chat", None)
    )


CATEGORY_EMOJI = {
    "link": "🔗",
    "ads": "📢",
    "sex": "🔞",
    "drugs": "💊",
    "casino": "🎰",
    "crypto": "🪙",
    "games": "🎮",
    "illegal": "⚠️",
    "profanity": "🤬",
    "mat": "🤬",
    "insult": "😡",
    "hate": "🚫",
    "flood": "💬",
    "other": "🚫",
    "ok": "✅",
    "heuristic": "⚡",
}


def _notify_text(user_mention: str, category: str, reason: str) -> str:
    emoji = CATEGORY_EMOJI.get(category.lower(), "🚫")
    cat_ru = {
        "link": "ссылки/реклама",
        "ads": "реклама",
        "sex": "эротика",
        "drugs": "запрещёнка",
        "casino": "казино",
        "crypto": "крипта",
        "games": "игры",
        "illegal": "нарушение",
        "profanity": "мат",
        "mat": "мат",
        "insult": "оскорбление",
        "hate": "хейт",
        "flood": "флуд/спам",
        "heuristic": "спам",
        "other": "спам",
    }.get(category.lower(), category)
    return f"🗑️ Сообщение от {user_mention} удалено\n{emoji} Причина: {cat_ru} — {reason}"


async def _notify_and_cleanup(bot, chat_id: int, text: str, delay: int = 30) -> None:
    try:
        msg = await bot.send_message(chat_id, text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        log.warning("notify failed: %s", e)


async def _flush_notify_summary(bot, chat_id: int) -> None:
    await asyncio.sleep(30)
    n = _suppressed.pop(chat_id, 0)
    if n:
        await _notify_and_cleanup(bot, chat_id, f"🗑️ Удалено ещё {n} спам-сообщений (рейд)", delay=60)


def _notify(bot, chat_id: int, text: str) -> None:
    """Троттлинг уведомлений: >3 за 30с — складываем в сводку, чтобы бот сам не флудил при рейде."""
    now = time.monotonic()
    dq = _notify_ts.setdefault(chat_id, deque())
    while dq and now - dq[0] > 30:
        dq.popleft()
    if len(dq) >= 3:
        _suppressed[chat_id] = _suppressed.get(chat_id, 0) + 1
        t = _summary_task.get(chat_id)
        if not t or t.done():
            _summary_task[chat_id] = asyncio.create_task(_flush_notify_summary(bot, chat_id))
        return
    dq.append(now)
    asyncio.create_task(_notify_and_cleanup(bot, chat_id, text))


def _rl(store: dict[tuple[int, int], deque[float]], chat_id: int, user_id: int, count: int, window: int) -> bool:
    now = time.monotonic()
    if len(store) > 5000:  # защита от раздувания мапы на больших чатах
        for k in [k for k, v in store.items() if not v]:
            store.pop(k, None)
    dq = store.setdefault((chat_id, user_id), deque())
    while dq and now - dq[0] > window:
        dq.popleft()
    dq.append(now)
    return len(dq) > count


def _is_rate_limited(chat_id: int, user_id: int, count: int, window: int) -> bool:
    return _rl(_rate, chat_id, user_id, count, window)


def _is_rate_limited_chat(chat_id: int, user_id: int, count: int, window: int) -> bool:
    return _rl(_chat_rate, chat_id, user_id, count, window)


async def _is_chat_admin(m: Message) -> bool:
    """Пропускаем админов чата — перманентный мьют за ложное срабатывание никому не нужен."""
    sc = getattr(m, "sender_chat", None)
    if sc is not None and sc.id == m.chat.id:
        return True  # анонимный админ/пост от имени чата
    if not m.from_user:
        return False
    now = time.monotonic()
    ts, ids = _admins_cache.get(m.chat.id, (0, set()))
    if now - ts > _ADMINS_TTL:
        try:
            admins = await m.bot.get_chat_administrators(m.chat.id)
            ids = {a.user.id for a in admins}
            _admins_cache[m.chat.id] = (now, ids)
        except Exception as e:
            log.warning("get_chat_administrators failed chat=%s: %s", m.chat.id, e)
            ids = set()
    return m.from_user.id in ids


def _is_new_member(member: dict) -> bool:
    s = load_settings()
    if member.get("flagged"):
        return True
    fresh = member["joined_ts"] > int(time.time()) - s.probation_hours * 3600
    few_msgs = member["msg_count"] < s.probation_msgs
    return bool(fresh or few_msgs)


async def _member_or_join(chat_id: int, user_id: int) -> dict:
    member = await get_member(chat_id, user_id)
    if member is None:
        await record_join(chat_id, user_id)
        member = {"chat_id": chat_id, "user_id": user_id, "joined_ts": int(time.time()), "msg_count": 0, "flagged": 0}
    return member


_MUTE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


async def _punish(bot, chat_id: int, user_id: int, category: str, reason: str, full_name: str) -> None:
    """Перманентный мьют с первого нарушения (mute_policy=permanent) или старая эскалация."""
    try:
        c24, c3d, level = await add_violation(chat_id, user_id, category, reason)
    except Exception as e:
        log.warning("add_violation failed: %s", e)
        c24, c3d, level = 1, 1, 0
    s = load_settings()
    uname = f"<a href='tg://user?id={user_id}'>{html.escape(full_name)}</a>"
    try:
        if s.mute_policy == "permanent":
            if s.ban_on_repeat_spam:
                await bot.ban_chat_member(chat_id, user_id)
                await bot.unban_chat_member(chat_id, user_id)
                _notify(bot, chat_id, f"⛔ {uname} удалён — спам ({reason})")
            else:
                await bot.restrict_chat_member(chat_id, user_id, permissions=_MUTE_PERMS)
                await set_mute_level(chat_id, user_id, 3, None)
                _notify(bot, chat_id, f"⛔ {uname} в перманентном мьюте — спам ({reason})")
            log.info("permanent punish user=%s chat=%s cat=%s", user_id, chat_id, category)
            return
        # progressive: старая схема 1д -> 7д -> пермач
        if level == 0 and c24 >= 2:
            until = datetime.now(UTC) + timedelta(days=1)
            await bot.restrict_chat_member(chat_id, user_id, permissions=_MUTE_PERMS, until_date=until)
            await set_mute_level(chat_id, user_id, 1, int(until.timestamp()))
            _notify(bot, chat_id, f"🔇 {uname} мьют на 1 день — 2 нарушения за 24ч")
        elif level == 1 and c3d >= 4:
            until = datetime.now(UTC) + timedelta(days=7)
            await bot.restrict_chat_member(chat_id, user_id, permissions=_MUTE_PERMS, until_date=until)
            await set_mute_level(chat_id, user_id, 2, int(until.timestamp()))
            _notify(bot, chat_id, f"🔇 {uname} мьют на 7 дней — повторные нарушения за 3 дня")
        elif level >= 2 and c3d >= 6:
            await bot.restrict_chat_member(chat_id, user_id, permissions=_MUTE_PERMS)
            await set_mute_level(chat_id, user_id, 3, None)
            _notify(bot, chat_id, f"⛔ {uname} постоянный мьют — многократные нарушения")
    except Exception as e:
        log.warning("punish failed: %s", e)


async def _handle_spam(m: Message, result: dict) -> None:
    s = load_settings()
    if not s.delete_spam or not is_group_chat(m):
        return
    try:
        await m.delete()
    except Exception as e:
        log.warning("delete failed: %s", e)
    try:
        uname = (
            f"<a href='tg://user?id={m.from_user.id}'>{html.escape(m.from_user.full_name)}</a>"
            if m.from_user
            else "пользователя"
        )
        reason = result.get("reason") or result.get("category") or "спам"
        cat = result.get("category") or "other"
        _notify(m.bot, m.chat.id, _notify_text(uname, cat, reason))
        if m.from_user:
            await _punish(m.bot, m.chat.id, m.from_user.id, cat, reason, m.from_user.full_name)
    except Exception:
        pass


async def _scan_profile(bot, chat_id: int, user: User) -> None:
    """Скан профиля при входе: ссылка+запрещённая тема в bio/имени → перманентный мьют
    (порно-наживки «смотри профиль»), только ссылка → пометка для строгого probation."""
    s = load_settings()
    if not s.bio_scan or user.is_bot:
        return
    key = (chat_id, user.id)
    if key in _PROFILE_SCANNED:
        return
    if len(_PROFILE_SCANNED) > 10000:
        _PROFILE_SCANNED.clear()
    _PROFILE_SCANNED.add(key)
    parts = [user.full_name or ""]
    if user.username:
        parts.append(f"@{user.username}")
    try:
        chat = await bot.get_chat(user.id)
        bio = getattr(chat, "bio", "") or ""
        if bio:
            parts.append(bio)
    except Exception as e:
        log.info("bio unavailable user=%s: %s", user.id, e)
    profile = "\n".join(p for p in parts if p)
    if not profile:
        return
    # has_url, не has_link: собственный @username — не нарушение
    if has_url(profile) and has_banned_topic(profile):
        log.info("profile bait user=%s chat=%s profile=%.120s", user.id, chat_id, profile)
        await _punish(bot, chat_id, user.id, "link", "ссылка+запретка в профиле", user.full_name)
    elif has_url(profile):
        await flag_member(chat_id, user.id)


def _vision_wanted(mode: str, strict_user: bool, is_fwd: bool, analysis_text: str) -> bool:
    if mode == "off":
        return False
    if mode == "always":
        return True
    if mode == "new_users":
        return strict_user or is_fwd
    # suspect (default): новые юзеры, форварды, медиа со ссылкой в подписи
    return strict_user or is_fwd or has_link(analysis_text)


async def _moderate(m: Message) -> dict:
    """Ядро модерации. Возвращает вердикт {"spam": bool, ...}."""
    s = load_settings()
    uid = m.from_user.id if m.from_user else None
    is_fwd = _is_forward(m)

    if uid and _is_rate_limited(m.chat.id, uid, s.rate_limit_count, s.rate_window_sec):
        return {
            "spam": True,
            "category": "flood",
            "reason": f">{s.rate_limit_count} сообщений за {s.rate_window_sec}с",
            "via": "ratelimit",
        }

    text = (m.text or m.caption or "").strip()
    # скрытые ссылки: слово-гиперссылка (text_link) и url-кнопки (reply_markup)
    hidden = extract_hidden_urls(
        getattr(m, "entities", None) or getattr(m, "caption_entities", None),
        getattr(m, "reply_markup", None),
    )
    analysis = text
    for u in hidden:
        analysis += f"\n[LINK] {u}"

    member = await _member_or_join(m.chat.id, uid) if uid else None
    strict_user = bool(member and _is_new_member(member))
    if uid and member and member["msg_count"] == 0 and s.bio_scan:
        asyncio.create_task(_scan_profile(m.bot, m.chat.id, m.from_user))

    # строгий probation: новый юзер + реальная ссылка (видимая или скрытая) = спам;
    # @mention не считается нарушением
    if strict_user and (hidden or has_url(analysis)):
        return {
            "spam": True,
            "category": "link",
            "reason": "probation: ссылка от нового участника",
            "via": "probation",
        }

    has_media = bool(
        m.photo or m.sticker or m.animation or m.video or m.video_note or m.document
    )
    image_url = file_uid = None
    if has_media and _vision_wanted(s.vision_mode, strict_user, is_fwd, analysis):
        image_url, file_uid = await resolve_image(m.bot, m)
        if file_uid:
            cached_media = await media_verdict_get(file_uid)
            if cached_media is not None:
                if cached_media["spam"]:
                    return {
                        "spam": True,
                        "category": cached_media.get("category") or "other",
                        "reason": f"media cache: {cached_media.get('reason')}",
                        "via": "media_cache",
                    }
                image_url = None  # кэш говорит чисто — vision-вызов не нужен

    if not analysis and image_url is None:
        return {"spam": False, "category": "ok", "reason": "empty", "via": "skip"}

    # кэш вердиктов по хэшу текста — одинаковый спам не жжёт LLM повторно
    vhash = verdict_hash(analysis) if analysis else None
    if vhash and image_url is None:
        cached = await verdict_get(vhash)
        if cached is not None:
            return {
                "spam": bool(cached["spam"]),
                "category": cached.get("category") or "other",
                "reason": f"cache: {cached.get('reason')}",
                "via": "cache",
            }

    context = None
    try:
        recent = await get_recent_messages(m.chat.id)
        context = [r["text"] for r in reversed(recent) if r["message_id"] != m.message_id and r["text"]]
    except Exception:
        pass

    vision_model = (s.vega_vision_model or s.vega_model) if image_url else None
    result = await ai_is_spam(
        analysis, image_url=image_url, is_forward=is_fwd, model=vision_model, context=context
    )

    if result.get("error"):
        sf = strict_fallback(analysis, is_fwd)
        result = sf if sf else {"spam": False, "reason": result["reason"], "category": "error", "via": "llm"}

    if image_url and file_uid:
        await media_verdict_set(file_uid, bool(result.get("spam")), result.get("category", ""), result.get("reason", ""))
    if vhash and image_url is None:
        await verdict_set(vhash, bool(result.get("spam")), result.get("category", ""), result.get("reason", ""))

    return result


async def _chat_triggered(m: Message) -> bool:
    """Когда ВЕГА может захотеть ответить: reply на неё, @упоминание, имя в тексте.
    Само решение — за моделью (она может промолчать через [SILENT])."""
    text = (m.text or m.caption or "").strip()
    if not text:
        return False
    rep = getattr(m, "reply_to_message", None)
    if rep and rep.from_user and rep.from_user.id == getattr(m.bot, "id", None):
        return True
    me = await _bot_me(m.bot)
    uname = getattr(me, "username", None) if me else None
    if uname and f"@{uname}".lower() in text.lower():
        return True
    return bool(_NAME_RE.search(text))


async def _chat_reply(m: Message, force: bool = False) -> None:
    """Диалог личности ВЕГА. force=True — в ЛС от админа/whitelist отвечаем всегда
    (ну, если сама модель не промолчит)."""
    s = load_settings()
    if not s.chat_enabled:
        return
    uid = m.from_user.id if m.from_user else 0
    if uid and _is_rate_limited_chat(m.chat.id, uid, s.rate_limit_count, s.rate_window_sec):
        return
    if not force and not await _chat_triggered(m):
        # свобода воли: с шансом chat_ambient_pct ВЕГА сама посмотрит сообщение
        # и решит — встрять или промолчать ([SILENT])
        if s.chat_ambient_pct <= 0 or random.random() * 100 >= s.chat_ambient_pct:
            return

    context = None
    try:
        recent = await get_recent_messages(m.chat.id)
        context = [r["text"] for r in reversed(recent) if r["message_id"] != m.message_id and r["text"]]
    except Exception:
        pass

    text = (m.text or m.caption or "").strip()
    # reply-контекст: на что отвечают
    rep = getattr(m, "reply_to_message", None)
    rep_text = ""
    if rep:
        rt = (rep.text or rep.caption or "").strip()
        if rt:
            rep_text = f"\n(Ответ на сообщение: {rt[:500]})"

    content: list[dict] = []
    parts = []
    if context:
        parts.append("Последние сообщения чата:\n" + "\n".join(f"- {c[:200]}" for c in context[:3]))
    parts.append(f"Сообщение от {m.from_user.full_name if m.from_user else 'юзера'}:{rep_text}\n{text}")
    content.append({"type": "text", "text": "\n\n".join(parts)})

    # картинка в диалоге: «вега, что тут?» + фото → тот же vision-резолвер
    if m.photo or m.sticker or m.animation or m.video or m.video_note or m.document:
        image_url, _ = await resolve_image(m.bot, m)
        if image_url:
            content.append({"type": "image_url", "image_url": {"url": image_url}})

    prompt = chat_prompt()
    if not prompt:
        return
    model = s.vega_chat_model or s.vega_model
    # веб-поиск по необходимости: CHAT_WEB_SEARCH=true — всегда; иначе — только при поисковом интенте
    web = s.chat_web_search or bool(_WEB_RE.search(text))
    out = await ai_chat_reply(prompt, content, model, s.chat_max_tokens, web)
    if out:
        log.info("vega chat reply chat=%s len=%s", m.chat.id, len(out))
        try:
            # escape: у бота parse_mode=HTML, дерзкая ВЕГА может выдать «<» и уронить отправку
            await m.reply(html.escape(out[:4000]))
        except Exception as e:
            log.warning("chat reply failed: %s", e)
    else:
        log.info("vega chat silent/empty chat=%s", m.chat.id)


_PRIVATE_REFUSAL = (
    "В личке я общаюсь только со своими создателями — @ingeniare и @SmirnNadya. "
    "Хочешь поговорить — зови меня в группе."
)


async def _handle_private(m: Message) -> None:
    """ЛС: диалог только с создателями (ADMIN_USER_IDS). Чужим — один вежливый
    отказ в характере ВЕГИ (без LLM-вызова), со троттлингом от флуда."""
    if not m.from_user:
        return
    if _is_stale(m):
        return  # старый апдейт из буфера — ЛС не отвечаем задним числом
    if is_admin(m.from_user.id):
        await _chat_reply(m, force=True)
        return
    if _is_rate_limited(m.chat.id, m.from_user.id, 2, 300):
        return
    try:
        await m.answer(_PRIVATE_REFUSAL)
    except Exception as e:
        log.warning("private refusal failed: %s", e)


async def _process(m: Message) -> None:
    """Общий вход для новых и отредактированных сообщений."""
    if m.chat.type == "private":
        await _handle_private(m)
        return
    if not is_group_chat(m):
        return
    if not is_allowed_chat(m.chat.id, getattr(m.chat, "username", None)):
        return
    try:
        await save_recent_message(
            m.chat.id, m.message_id, m.from_user.id if m.from_user else 0, m.text or m.caption or "[media]"
        )
    except Exception as e:
        log.warning("save_recent failed: %s", e)

    # чужие боты: модерируем (бот-спаммер реален), но НЕ разговариваем —
    # иначе петля: бот отвечает боту → бот отвечает → бесконечные LLM-вызовы
    if m.from_user and m.from_user.is_bot:
        result = await _moderate(m)
        if result.get("spam"):
            await _handle_spam(m, result)
        return

    # свои (whitelist/админы бота/админы чата) модерацию пропускаем,
    # но разговаривать с ВЕГАй они могут — иначе админ не услышит ответа никогда
    privileged = bool(m.from_user) and (
        is_whitelisted(m.from_user.id) or is_admin(m.from_user.id)
    )
    if privileged or await _is_chat_admin(m):
        if not _is_stale(m):
            await _chat_reply(m)
        return

    result = await _moderate(m)
    if result.get("spam"):
        log.info(
            "spam detected chat=%s user=%s reason=%s cat=%s via=%s text=%.120s",
            m.chat.id,
            getattr(m.from_user, "id", 0),
            result.get("reason"),
            result.get("category"),
            result.get("via"),
            m.text or m.caption or "[media]",
        )
        await _handle_spam(m, result)
    elif m.from_user:
        await bump_member_msgs(m.chat.id, m.from_user.id)
        # чистое сообщение → ВЕГА может ответить, если к ней обратились;
        # старые (из буфера даунтайма) — только модерируем
        if not _is_stale(m):
            await _chat_reply(m)


@router.message(Command("start"))
async def cmd_start(m: Message) -> None:
    await m.answer(
        "Привет! Я Дейнерис — антиспам-бот и мать драконов антиспама. Обращайся — позови «Дейнерис»/«Дени» или ответь на моё сообщение.\n"
        "Добавь меня в группу/канал и выдай права админа (удаление сообщений + бан).\n"
        "⚠️ Для КАНАЛА: добавь меня ещё и в группу комментариев (Канал → Настройки → Обсуждение → Группа), иначе не увижу комментарии. Там тоже дай админа.\n"
        "Команды в ЛС (только для админов):\n"
        "/status — статус\n"
        "/add_chat &lt;id|@username&gt; — добавить чат\n"
        "/whitelist <user_id> — в белый список\n"
        "Настройки хранятся в config/config.yaml и data/bot.db — не удаляй их при обновлении.\n"
        "Больше возможностей: https://tg.vega.chat"
    )


@router.message(Command("status"))
async def cmd_status(m: Message) -> None:
    if m.chat.type != "private" or not m.from_user or not is_admin(m.from_user.id):
        return
    s = load_settings()
    await m.answer(
        f"Model: <code>{s.vega_model}</code>\n"
        f"Vision: <code>{s.vega_vision_model or s.vega_model}</code> ({s.vision_mode})\n"
        f"Lang: {s.default_language}\n"
        f"Chats: {s.allowed_chat_list or 'ALL'}\n"
        f"Whitelist: {s.whitelist_list}\n"
        f"Mute policy: {s.mute_policy}\n"
        f"Probation: {s.probation_hours}h/{s.probation_msgs}msg bio_scan={s.bio_scan}\n"
        f"Chat: <code>{s.vega_chat_model or s.vega_model}</code> enabled={s.chat_enabled} web={s.chat_web_search}\n"
        f"Vega: {'ok' if s.vega_api_key else 'NOT SET'}\n"
        f"Сервис: tg.vega.chat — защита без сервера"
    )


# --- вход юзеров: испытательный срок + скан профиля ---
# Два триггера (chat_member для аппрувов по заявкам, new_chat_members — сервисное сообщение),
# дублирование безопасно: record_join идемпотентен (INSERT OR IGNORE).


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated) -> None:
    try:
        if event.new_chat_member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
            return
        user = event.new_chat_member.user
        if user.is_bot or is_whitelisted(user.id) or is_admin(user.id):
            return
        if not is_allowed_chat(event.chat.id, getattr(event.chat, "username", None)):
            return
        if await record_join(event.chat.id, user.id):
            asyncio.create_task(_scan_profile(event.bot, event.chat.id, user))
    except Exception as e:
        log.exception("chat_member error: %s", e)


@router.message(F.new_chat_members)
async def on_new_members(m: Message) -> None:
    if not is_allowed_chat(m.chat.id, getattr(m.chat, "username", None)):
        return
    for u in m.new_chat_members or []:
        if u.is_bot or is_whitelisted(u.id) or is_admin(u.id):
            continue
        if await record_join(m.chat.id, u.id):
            asyncio.create_task(_scan_profile(m.bot, m.chat.id, u))


@router.message(F.text)
@router.channel_post(F.text)
async def on_text(m: Message) -> None:
    await _process(m)


@router.message(F.photo | F.document | F.video | F.animation | F.sticker | F.video_note)
@router.channel_post(F.photo | F.document | F.video | F.animation | F.sticker | F.video_note)
async def on_media(m: Message) -> None:
    await _process(m)


# отредактированные сообщения проверяем заново — иначе спам подменой после проверки проходит
@router.edited_message(F.text)
@router.edited_channel_post(F.text)
async def on_edited_text(m: Message) -> None:
    await _process(m)


@router.edited_message(F.photo | F.document | F.video | F.animation | F.sticker | F.video_note)
@router.edited_channel_post(F.photo | F.document | F.video | F.animation | F.sticker | F.video_note)
async def on_edited_media(m: Message) -> None:
    await _process(m)
