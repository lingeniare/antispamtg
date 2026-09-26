from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import router
from src.config import load_settings
from src.storage.db import init_db, kv_get, kv_set

log = logging.getLogger("tg-antispam")

# если между последним heartbeat и стартом прошло больше — считаем даунтаймом
_DOWN_ALERT_SEC = 600
_HEARTBEAT_SEC = 60


async def _heartbeat() -> None:
    """Пишет last_alive в kv раз в минуту — по разрыву при старте видно даунтайм."""
    while True:
        try:
            await kv_set("last_alive", str(int(time.time())))
        except Exception:
            pass
        await asyncio.sleep(_HEARTBEAT_SEC)


async def _notify_downtime(bot: Bot, admin_ids: list[int]) -> None:
    """Если сервис лежал — предупреждаем создателей в ЛС.
    Telegram хранит апдейты ~24ч: дольше — пропущенный спам уже не достать."""
    try:
        last = await kv_get("last_alive")
        if not last:
            return
        gap = int(time.time()) - int(last)
        if gap < _DOWN_ALERT_SEC:
            return
        since = datetime.fromtimestamp(int(last), UTC).strftime("%d.%m %H:%M UTC")
        hours, mins = divmod(gap, 3600)[0], (gap % 3600) // 60
        gap_s = f"{hours}ч {mins}м" if hours else f"{mins}м"
        tail = " (>24ч — часть сообщений Telegram уже не отдаёт, спам мог остаться)" if gap > 86400 else ""
        text = f"⚠️ Я была недоступна с {since} — даунтайм ~{gap_s}.{tail} Сейчас снова на посту."
        for aid in admin_ids:
            try:
                await bot.send_message(aid, text)
            except Exception as e:
                log.warning("downtime notify admin=%s failed: %s", aid, e)
        log.warning("downtime detected: %s sec since %s", gap, since)
    except Exception as e:
        log.warning("downtime check failed: %s", e)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    if not s.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Запустите install.sh или задайте .env / config/config.yaml")
    await init_db()
    bot = Bot(token=s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # сбросить webhook, иначе polling не работает (Conflict);
    # drop_pending_updates=False — после падения доедаем буфер (~24ч) и чистим спам задним числом
    try:
        await bot.delete_webhook(drop_pending_updates=s.drop_pending_updates)
        log.info("Webhook deleted, starting polling")
    except Exception as e:
        log.warning("delete_webhook failed: %s", e)
    await _notify_downtime(bot, s.admin_list)
    asyncio.create_task(_heartbeat())
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Bot starting polling model=%s lang=%s chats=%s", s.vega_model, s.default_language, s.allowed_chat_list)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
