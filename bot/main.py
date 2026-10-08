"""Application entry point: Dispatcher setup, allowed_updates, DB init, and polling loop."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path so 'bot' package imports work cleanly in all environments
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.handlers.admin import setup_admin_router
from bot.handlers.business import setup_business_router
from bot.services.scheduler import debt_reminder_worker
from bot.services.storage import Database


def configure_logging() -> None:
    """Setup structured application logging."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Suppress verbose external loggers if not debugging
    if log_level > logging.DEBUG:
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def main() -> None:
    """Bootstrap and start the Telegram Business bot."""
    configure_logging()
    logger = logging.getLogger("bot.main")
    logger.info("Initializing Telegram Business Auto-Responder Bot...")

    # 1. Initialize persistent storage
    db = Database(db_path=settings.DB_PATH)
    await db.init_db()
    logger.info(f"Database initialized at {settings.DB_PATH}")

    # 2. Setup Bot client with Default HTML formatting
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # 3. Setup Dispatcher with in-memory FSM storage
    dp = Dispatcher(storage=MemoryStorage())

    # 4. Register handler routers
    admin_router = setup_admin_router(db)
    business_router = setup_business_router(db)

    dp.include_router(admin_router)
    dp.include_router(business_router)

    # 5. Explicitly declare allowed updates (CRITICAL: Telegram Business updates are NOT sent by default)
    allowed_updates = [
        "message",
        "edited_message",
        "callback_query",
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ]

    # Verify bot identity on startup
    try:
        bot_user = await bot.get_me()
        logger.info(f"Bot connected: @{bot_user.username} (ID: {bot_user.id})")
        logger.info(f"Configured OWNER_ID: {settings.OWNER_ID}")
        logger.info(f"Auto-delete owner trigger messages: {settings.DELETE_TRIGGER_MESSAGE}")
        logger.info(f"Anti-spam cooldown: {settings.COOLDOWN_SECONDS}s")
        logger.info(f"Daily debtor reminders: {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d} ({settings.TIMEZONE})")
    except Exception as e:
        logger.critical(f"Failed to connect to Telegram API with BOT_TOKEN: {e}")
        await bot.session.close()
        return

    # 6. Start background debt reminder worker
    reminder_task = asyncio.create_task(debt_reminder_worker(bot, db))

    # 7. Start polling
    logger.info(f"Starting long polling with allowed updates: {allowed_updates}")
    try:
        # Do not drop pending updates so business_connection events sent while bot was offline are preserved
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot,
            allowed_updates=allowed_updates,
        )
    finally:
        logger.info("Cancelling background tasks and shutting down bot session...")
        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Process terminated by user.")
