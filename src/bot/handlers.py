from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, ChatPermissions, Message
from aiogram.enums import ChatMemberStatus

from src.config import load_settings
from src.storage.db import add_violation, del_captcha, get_captcha, save_recent_message, set_captcha, set_mute_level
from src.filters.captcha import check_answer, generate_captcha
from src.ai.vega_client import ai_is_spam

router = Router()
log = logging.getLogger("tg-antispam")


def is_group_chat(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup", "channel")


def is_allowed_chat(chat_id: int, username: str | None) -> bool:
    s = load_settings()
    lst = s.allowed_chat_list
    if not lst:
        return True
    chat_str = str(chat_id)
    # нормализуем: wpexpro == @wpexpro == @WPEXPRO
    norm = [c.strip().lower().lstrip("@") for c in lst]
    uname = (username or "").strip().lower().lstrip("@")
    return chat_str in lst or chat_str in norm or uname in norm


def is_whitelisted(user_id: int) -> bool:
    return user_id in load_settings().whitelist_list


def is_admin(user_id: int) -> bool:
    return user_id in load_settings().admin_list


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
        "other": "спам",
    }.get(category.lower(), category)
    return f"🗑️ Сообщение от {user_mention} удалено\n{emoji} Причина: {cat_ru} — {reason}"


async def _notify_and_cleanup(bot, chat_id: int, text: str, delay: int = 30):
    try:
        msg = await bot.send_message(chat_id, text)
        # удаляем уведомление через 30с чтобы не засорять чат
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        log.warning("notify failed: %s", e)


@router.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        "Привет! Я антиспам-бот.\n"
        "Добавь меня в группу/канал и выдай права админа (удаление сообщений + бан).\n"
        "⚠️ Для КАНАЛА: добавь меня ещё и в группу комментариев (Канал → Настройки → Обсуждение → Группа), иначе не увижу комментарии. Там тоже дай админа.\n"
        "Команды в ЛС (только для админов):\n"
        "/status — статус\n"
        "/add_chat <id|@username> — добавить чат\n"
        "/whitelist <user_id> — в белый список\n"
        "Настройки хранятся в config/config.yaml и data/bot.db — не удаляй их при обновлении.\n"
        "Больше возможностей: https://tg.vega.chat"
    )


@router.message(Command("status"))
async def cmd_status(m: Message):
    if m.chat.type != "private" or not m.from_user or not is_admin(m.from_user.id):
        return
    s = load_settings()
    await m.answer(
        f"Model: <code>{s.vega_model}</code>\n"
        f"Lang: {s.default_language}\n"
        f"Chats: {s.allowed_chat_list or 'ALL'}\n"
        f"Whitelist: {s.whitelist_list}\n"
        f"Vega: {'ok' if s.vega_api_key else 'NOT SET'}\n"
        f"Сервис: tg.vega.chat — защита без сервера"
    )


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated):
    try:
        if event.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
            user = event.new_chat_member.user
            if user.is_bot:
                return
            if is_whitelisted(user.id):
                return
            if not is_allowed_chat(event.chat.id, getattr(event.chat, "username", None)):
                return
            s = load_settings()
            try:
                until = datetime.now(timezone.utc) + timedelta(seconds=s.captcha_timeout_sec + 60)
                await event.bot.restrict_chat_member(
                    event.chat.id,
                    user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
            except Exception as e:
                log.warning("restrict failed: %s", e)
            cap = generate_captcha(s.default_language)
            await set_captcha(event.chat.id, user.id, cap["answer"], cap["trap_answer"])
            mention = f"<a href='tg://user?id={user.id}'>{user.full_name}</a>"
            msg = await event.bot.send_message(
                event.chat.id,
                f"{mention} напишите пожалуйста ответ на вопрос числом, сколько будет {cap['a']}+{cap['b']} ({cap['text'].split('(')[-1]}",
            )

            async def timeout_kick():
                await asyncio.sleep(s.captcha_timeout_sec)
                st = await get_captcha(event.chat.id, user.id)
                if st:
                    try:
                        if s.mute_on_captcha_fail:
                            await event.bot.ban_chat_member(event.chat.id, user.id)
                            await event.bot.unban_chat_member(event.chat.id, user.id)
                        await event.bot.send_message(
                            event.chat.id, f"⛔ {mention} не прошел проверку и удален (таймаут капчи)."
                        )
                        try:
                            await msg.delete()
                        except Exception:
                            pass
                    finally:
                        await del_captcha(event.chat.id, user.id)

            asyncio.create_task(timeout_kick())
    except Exception as e:
        log.exception("chat_member error: %s", e)


@router.message(F.new_chat_members)
async def on_new_members(m: Message):
    s = load_settings()
    for u in m.new_chat_members or []:
        if u.is_bot or is_whitelisted(u.id):
            continue
        if not is_allowed_chat(m.chat.id, getattr(m.chat, "username", None)):
            continue
        cap = generate_captcha(s.default_language)
        await set_captcha(m.chat.id, u.id, cap["answer"], cap["trap_answer"])
        mention = f"<a href='tg://user?id={u.id}'>{u.full_name}</a>"
        await m.answer(
            f"{mention} напишите пожалуйста ответ на вопрос числом, сколько будет {cap['a']}+{cap['b']} ({cap['text'].split('(')[-1]}"
        )


@router.message(F.text)
@router.channel_post(F.text)
async def on_text(m: Message):
    try:
        await save_recent_message(
            m.chat.id, m.message_id, m.from_user.id if m.from_user else 0, m.text or m.caption or ""
        )
    except Exception as e:
        log.warning("save_recent failed: %s", e)

    if m.from_user:
        st = await get_captcha(m.chat.id, m.from_user.id)
        if st:
            res = check_answer(m.text or "", st["expected"], st["trap_expected"])
            s = load_settings()
            mention = f"<a href='tg://user?id={m.from_user.id}'>{m.from_user.full_name}</a>"
            if res == "ok":
                await del_captcha(m.chat.id, m.from_user.id)
                try:
                    await m.bot.restrict_chat_member(
                        m.chat.id,
                        m.from_user.id,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_media_messages=True,
                            can_send_other_messages=True,
                            can_add_web_page_previews=True,
                        ),
                    )
                except Exception:
                    pass
                await m.answer(f"✅ {mention} проверка пройдена, добро пожаловать!")
                return
            elif res == "trap":
                await del_captcha(m.chat.id, m.from_user.id)
                try:
                    if s.mute_on_captcha_fail:
                        await m.bot.ban_chat_member(m.chat.id, m.from_user.id)
                        await m.bot.unban_chat_member(m.chat.id, m.from_user.id)
                    await m.delete()
                except Exception:
                    pass
                await m.bot.send_message(m.chat.id, f"🚫 {mention} не прошел проверку (ловушка для ботов).")
                return
            else:
                if m.chat.type in ("group", "supergroup"):
                    try:
                        await m.delete()
                    except Exception:
                        pass
                await m.bot.send_message(m.chat.id, f"{mention} неверно, попробуйте еще раз числом.")
                return

    if m.from_user and is_whitelisted(m.from_user.id):
        log.info("skip whitelist user=%s chat=%s", m.from_user.id, m.chat.id)
        return

    if is_group_chat(m) and not is_allowed_chat(m.chat.id, getattr(m.chat, "username", None)):
        log.info("skip not allowed chat=%s username=%s allowed=%s", m.chat.id, getattr(m.chat, "username", None), load_settings().allowed_chat_list)
        return

    text = (m.text or m.caption or "").strip()
    if not text and not m.photo:
        log.info("skip empty text chat=%s", m.chat.id)
        return

    # диагностический лог — видно в journalctl
    s_dbg = load_settings()
    log.info("check msg chat=%s type=%s user=%s vega_key=%s model=%s text=%.100s", m.chat.id, m.chat.type, getattr(m.from_user, "id", 0), "set" if s_dbg.vega_api_key else "EMPTY", s_dbg.vega_model, text)

    result = await ai_is_spam(text, None)
    if result.get("spam"):
        s = load_settings()
        log.info(
            "spam detected chat=%s user=%s reason=%s cat=%s via=%s text=%.120s",
            m.chat.id,
            getattr(m.from_user, "id", 0),
            result.get("reason"),
            result.get("category"),
            result.get("via"),
            text,
        )
        if s.delete_spam and is_group_chat(m):
            try:
                await m.delete()
            except Exception as e:
                log.warning("delete failed: %s", e)
            # уведомление в чат с причиной (с эмодзи, авто-удаление через 30с)
            try:
                uname = f"<a href='tg://user?id={m.from_user.id}'>{m.from_user.full_name}</a>" if m.from_user else "пользователя"
                reason = result.get("reason") or result.get("category") or "спам"
                cat = result.get("category") or "other"
                asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, _notify_text(uname, cat, reason)))
            except Exception:
                pass
            # прогрессивный мьют: 2×/24ч→1д, +2×/3д→7д, +ещё→перманент
            if m.from_user:
                try:
                    c24, c3d, level = await add_violation(m.chat.id, m.from_user.id, cat, reason)
                    uname2 = f"<a href='tg://user?id={m.from_user.id}'>{m.from_user.full_name}</a>"
                    if level == 0 and c24 >= 2:
                        until = datetime.now(timezone.utc) + timedelta(days=1)
                        await m.bot.restrict_chat_member(m.chat.id, m.from_user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                        await set_mute_level(m.chat.id, m.from_user.id, 1, int(until.timestamp()))
                        asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, f"🔇 {uname2} мьют на 1 день — 2 нарушения за 24ч", delay=60))
                        log.info("mute 1d user=%s chat=%s c24=%s c3d=%s", m.from_user.id, m.chat.id, c24, c3d)
                    elif level == 1 and c3d >= 4:
                        until = datetime.now(timezone.utc) + timedelta(days=7)
                        await m.bot.restrict_chat_member(m.chat.id, m.from_user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                        await set_mute_level(m.chat.id, m.from_user.id, 2, int(until.timestamp()))
                        asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, f"🔇 {uname2} мьют на 7 дней — повторные нарушения за 3 дня", delay=60))
                        log.info("mute 7d user=%s chat=%s c24=%s c3d=%s", m.from_user.id, m.chat.id, c24, c3d)
                    elif level >= 2 and c3d >= 6:
                        await m.bot.restrict_chat_member(m.chat.id, m.from_user.id, permissions=ChatPermissions(can_send_messages=False))
                        await set_mute_level(m.chat.id, m.from_user.id, 3, None)
                        asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, f"⛔ {uname2} постоянный мьют — многократные нарушения", delay=60))
                        log.info("mute forever user=%s chat=%s c24=%s c3d=%s", m.from_user.id, m.chat.id, c24, c3d)
                except Exception as e:
                    log.warning("mute escalation failed: %s", e)
        return


@router.message(F.photo | F.document | F.video)
@router.channel_post(F.photo | F.document | F.video)
async def on_media(m: Message):
    try:
        await save_recent_message(
            m.chat.id, m.message_id, m.from_user.id if m.from_user else 0, m.caption or "[media]"
        )
    except Exception:
        pass
    if m.from_user and is_whitelisted(m.from_user.id):
        return
    if m.caption:
        result = await ai_is_spam(m.caption)
        if result.get("spam") and load_settings().delete_spam and is_group_chat(m):
            try:
                await m.delete()
            except Exception:
                pass
            try:
                uname = f"<a href='tg://user?id={m.from_user.id}'>{m.from_user.full_name}</a>" if m.from_user else "пользователя"
                reason = result.get("reason") or result.get("category") or "спам"
                cat = result.get("category") or "other"
                asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, _notify_text(uname, cat, reason)))
                if m.from_user:
                    c24, c3d, level = await add_violation(m.chat.id, m.from_user.id, cat, reason)
                    if level == 0 and c24 >= 2:
                        until = datetime.now(timezone.utc) + timedelta(days=1)
                        await m.bot.restrict_chat_member(m.chat.id, m.from_user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                        await set_mute_level(m.chat.id, m.from_user.id, 1, int(until.timestamp()))
                        asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, f"🔇 {uname} мьют на 1 день — 2 нарушения за 24ч", delay=60))
                    elif level == 1 and c3d >= 4:
                        until = datetime.now(timezone.utc) + timedelta(days=7)
                        await m.bot.restrict_chat_member(m.chat.id, m.from_user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                        await set_mute_level(m.chat.id, m.from_user.id, 2, int(until.timestamp()))
                        asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, f"🔇 {uname} мьют на 7 дней — повторные нарушения", delay=60))
                    elif level >= 2 and c3d >= 6:
                        await m.bot.restrict_chat_member(m.chat.id, m.from_user.id, permissions=ChatPermissions(can_send_messages=False))
                        await set_mute_level(m.chat.id, m.from_user.id, 3, None)
                        asyncio.create_task(_notify_and_cleanup(m.bot, m.chat.id, f"⛔ {uname} постоянный мьют", delay=60))
            except Exception:
                pass
