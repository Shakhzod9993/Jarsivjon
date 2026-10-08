"""Scheduler and notification service for debtor daily reminders via Telegram Business account."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import html
import logging
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from bot.config import settings
from bot.services.storage import Database

logger = logging.getLogger(__name__)


def format_debt_reminder_message(debtor: Dict[str, Any]) -> str:
    """
    Format message sent from owner's personal account to debtor in private chat.
    Written from first person since it comes directly from owner's Telegram profile.
    """
    amount = html.escape(str(debtor.get("amount", "")))
    comment = html.escape(str(debtor.get("comment", "") or "").strip())

    msg = (
        "Привет! Напоминаю по поводу задолженности.\n\n"
        f"💰 <b>Сумма к оплате:</b> <code>{amount}</code>\n"
    )
    if comment:
        msg += f"📝 <b>Информация:</b>\n{comment}\n"

    return msg


def format_debt_closed_message(debtor: Dict[str, Any]) -> str:
    """
    Format message sent from owner's personal account when debt is cleared.
    Written from first person.
    """
    amount = html.escape(str(debtor.get("amount", "")))

    return (
        f"Долг на сумму <b>{amount}</b> успешно закрыт!\n\n"
        "✅ Претензий больше нет, всё в порядке. Спасибо!"
    )


async def get_owner_business_connection_id(db: Database) -> Optional[str]:
    """Retrieve active Telegram Business connection ID for the owner account."""
    conn = await db.get_active_connection_for_user(settings.OWNER_ID)
    if conn and conn.get("is_enabled"):
        return conn.get("connection_id")
    return None


async def send_debt_reminder(bot: Bot, db: Database, debtor: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Send daily debt reminder to debtor directly from the OWNER'S PERSONAL ACCOUNT
    using Telegram Business Connection.
    """
    user_id = debtor.get("user_id")
    if not user_id:
        return False, "Отсутствует Telegram ID пользователя."

    conn_id = await get_owner_business_connection_id(db)
    if not conn_id:
        return False, (
            "Telegram Business не подключен! Откройте Настройки → Telegram Business → Чат-боты "
            "и подключите бота, чтобы отправлять сообщения от своего аккаунта."
        )

    text = format_debt_reminder_message(debtor)

    try:
        # Send from owner's account into the private chat with the debtor
        await bot.send_message(
            chat_id=user_id,
            text=text,
            business_connection_id=conn_id,
            parse_mode=ParseMode.HTML,
        )
        return True, "Успешно отправлено от вашего аккаунта"
    except TelegramForbiddenError as e:
        logger.warning(f"Forbidden error sending business message to {user_id}: {e}")
        return False, f"Ошибка доступа Telegram Business: {e}. Проверьте права бота в настройках Business."
    except TelegramBadRequest as e:
        logger.warning(f"Bad request sending business reminder to user {user_id}: {e}")
        return False, f"Ошибка Telegram: {e.message if hasattr(e, 'message') else str(e)}"
    except TelegramRetryAfter as e:
        logger.warning(f"Telegram flood control: retry after {e.retry_after}s for user {user_id}")
        return False, f"Лимит Telegram: повторите через {e.retry_after}с"
    except TelegramAPIError as e:
        logger.error(f"Telegram API error sending business reminder to {user_id}: {e}")
        return False, f"Ошибка Telegram API: {e}"
    except Exception as e:
        logger.error(f"Unexpected error sending business reminder to {user_id}: {e}", exc_info=True)
        return False, f"Непредвиденная ошибка: {e}"


async def send_debt_closed_notification(bot: Bot, db: Database, debtor: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Send 'debt cleared' notification to debtor directly from the OWNER'S PERSONAL ACCOUNT.
    """
    user_id = debtor.get("user_id")
    if not user_id:
        return False, "Отсутствует Telegram ID пользователя."

    conn_id = await get_owner_business_connection_id(db)
    if not conn_id:
        return False, (
            "Telegram Business не подключен! Подключите бота в Telegram Business → Чат-боты."
        )

    text = format_debt_closed_message(debtor)

    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            business_connection_id=conn_id,
            parse_mode=ParseMode.HTML,
        )
        return True, "Успешно отправлено от вашего аккаунта"
    except TelegramForbiddenError as e:
        logger.warning(f"Forbidden error sending business closed message to {user_id}: {e}")
        return False, f"Ошибка прав Business: {e}"
    except TelegramBadRequest as e:
        logger.warning(f"Bad request sending closed notification to user {user_id}: {e}")
        return False, f"Ошибка Telegram: {e.message if hasattr(e, 'message') else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error sending closed notification to {user_id}: {e}", exc_info=True)
        return False, f"Ошибка: {e}"


async def run_daily_reminder_round(bot: Bot, db: Database) -> Dict[str, Any]:
    """
    Execute a round of reminders for all active debtors from owner's account.
    Updates last_notified_at in DB and notifies owner.
    """
    active_debtors = await db.get_active_debtors()
    if not active_debtors:
        logger.info("Daily debt reminder round: no active debtors found.")
        return {"total": 0, "sent": 0, "failed": 0}

    logger.info(f"Starting daily debt reminders for {len(active_debtors)} debtors via owner account...")
    sent_count = 0
    failed_count = 0
    failures = []

    for debtor in active_debtors:
        success, err = await send_debt_reminder(bot, db, debtor)
        if success:
            sent_count += 1
            await db.update_last_notified(debtor["id"])
        else:
            failed_count += 1
            failures.append(f"• <b>{html.escape(debtor['name'])}</b> (ID: {debtor['user_id']}): {err}")
        # Small delay between messages to respect Telegram limits
        await asyncio.sleep(0.5)

    logger.info(f"Daily reminders finished: {sent_count} sent, {failed_count} failed.")

    # Notify owner in bot PM about daily digest
    try:
        report_lines = [
            f"⏰ <b>Отчёт о ежедневной рассылке напоминаний от вашего аккаунта ({settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d})</b>\n",
            f"• Всего должников на учете: <b>{len(active_debtors)}</b>",
            f"• Успешно отправлено от вашего имени: <b>{sent_count}</b>",
            f"• Не удалось отправить: <b>{failed_count}</b>",
        ]
        if failures:
            report_lines.append("\n⚠️ <b>Ошибки:</b>")
            report_lines.extend(failures[:10])
            if len(failures) > 10:
                report_lines.append(f"... и ещё {len(failures) - 10}")

        await bot.send_message(
            chat_id=settings.OWNER_ID,
            text="\n".join(report_lines),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Failed to send daily reminder report to owner: {e}")

    return {"total": len(active_debtors), "sent": sent_count, "failed": failed_count}


def get_seconds_until_next_run(target_hour: int = 12, target_minute: int = 0, tz_name: str = "Europe/Moscow") -> float:
    """Calculate seconds until next occurrence of target_hour:target_minute in tz_name."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Moscow")

    now = datetime.now(tz)
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    diff = (target - now).total_seconds()
    return max(diff, 1.0)


async def debt_reminder_worker(bot: Bot, db: Database) -> None:
    """
    Background worker loop that runs daily at NOTIFICATION_HOUR:NOTIFICATION_MINUTE.
    Sends reminders from owner's account.
    """
    logger.info(
        f"Debtor reminder background worker started. Scheduled for "
        f"{settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d} ({settings.TIMEZONE})."
    )

    while True:
        try:
            wait_seconds = get_seconds_until_next_run(
                target_hour=settings.NOTIFICATION_HOUR,
                target_minute=settings.NOTIFICATION_MINUTE,
                tz_name=settings.TIMEZONE,
            )
            hours_wait = wait_seconds / 3600
            logger.info(f"Next debt reminder round in {hours_wait:.2f} hours ({int(wait_seconds)} seconds).")

            await asyncio.sleep(wait_seconds)

            # Trigger reminder round
            await run_daily_reminder_round(bot, db)

            # Sleep 65 seconds so we don't accidentally re-trigger in the same minute
            await asyncio.sleep(65)

        except asyncio.CancelledError:
            logger.info("Debt reminder background worker cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in debt reminder worker: {e}", exc_info=True)
            await asyncio.sleep(30)
