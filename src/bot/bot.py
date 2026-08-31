from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import router
from src.config import load_settings
from src.storage.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tg-antispam")

async def main():
    s = load_settings()
    if not s.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Запустите install.sh или задайте .env / config/config.yaml")
    await init_db()
    bot = Bot(token=s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # важно: сбросить webhook, иначе polling не работает (Conflict)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Webhook deleted, starting polling")
    except Exception as e:
        log.warning("delete_webhook failed: %s", e)
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Bot starting polling model=%s lang=%s chats=%s", s.vega_model, s.default_language, s.allowed_chat_list)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
