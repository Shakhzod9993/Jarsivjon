"""Telegram Business updates handlers: business_connection, business_message, etc."""

from __future__ import annotations

import html
import logging
from typing import Optional

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message

from bot.config import settings
from bot.services.storage import Database
from bot.services.triggers import CooldownManager, LoopGuard, TriggerMatcher, format_card_response

logger = logging.getLogger(__name__)

router = Router(name="business_router")

# Shared state instances
cooldown_manager = CooldownManager()
loop_guard = LoopGuard()


def setup_business_router(db: Database) -> Router:
    """Configure business router with database dependency."""

    @router.business_connection()
    async def on_business_connection(connection: BusinessConnection) -> None:
        """Handle business connection establishment, updates, or disconnection."""
        status_text = "ENABLED" if connection.is_enabled else "DISABLED"
<<<<<<< HEAD
        # Telegram endi `can_reply` o'rniga `rights.can_reply` yuboradi (eski maydon None bo'lishi mumkin)
        can_reply = connection.can_reply
        if can_reply is None:
            can_reply = bool(connection.rights and connection.rights.can_reply)
        logger.info(
            f"Business connection update: id={connection.id}, user_id={connection.user.id}, "
            f"can_reply={can_reply}, status={status_text}"
=======
        logger.info(
            f"Business connection update: id={connection.id}, user_id={connection.user.id}, "
            f"can_reply={connection.can_reply}, status={status_text}"
>>>>>>> e046cb5058410ea38cab523ccbe0ff629d7fe9ed
        )

        try:
            await db.save_business_connection(
                connection_id=connection.id,
                user_id=connection.user.id,
                user_chat_id=connection.user_chat_id,
<<<<<<< HEAD
                can_reply=can_reply,
=======
                can_reply=connection.can_reply,
>>>>>>> e046cb5058410ea38cab523ccbe0ff629d7fe9ed
                is_enabled=connection.is_enabled,
            )
        except Exception as e:
            logger.error(f"Failed to persist business connection {connection.id}: {e}", exc_info=True)

    @router.business_message()
    async def on_business_message(message: Message, bot: Bot) -> None:
        """Process messages received in connected business chats."""
        # 1. Ignore non-text messages safely without raising exceptions
        if not message.text:
            return

        # 2. Prevent loops: ignore messages sent by business bot itself
        if getattr(message, "sender_business_bot", None) is not None:
            return

        if loop_guard.is_loop(message.message_id, message.text):
            logger.debug(f"Loop guard suppressed message {message.message_id}")
            return

        # 3. Verify business connection ID
        conn_id: Optional[str] = message.business_connection_id
        if not conn_id:
            logger.warning(f"Business message {message.message_id} missing business_connection_id")
            return

        # 4. Identify the sender role: owner vs interlocutor
        # Check against connection record or fallback to OWNER_ID
        conn = await db.get_business_connection(conn_id)
        owner_id = conn["user_id"] if conn else settings.OWNER_ID

        sender_id = message.from_user.id if message.from_user else 0
        is_owner = (sender_id == owner_id)
        role = "owner" if is_owner else "interlocutor"

        # 4.1. Fast debtor management directly from chat by owner
        # Command: .debt <amount> [comment] or .долг <сумма> [комментарий]
        if is_owner and message.text.strip().lower().startswith((".debt ", ".долг ", ".debt\n", ".долг\n")):
            parts = message.text.split(maxsplit=2)
            if len(parts) >= 2:
                amount = parts[1].strip()
                comment = parts[2].strip() if len(parts) > 2 else ""
                target_user_id = message.chat.id
                target_name = message.chat.first_name or "собеседник"
                if message.chat.last_name:
                    target_name += f" {message.chat.last_name}"
                target_username = message.chat.username

                debtor_id = await db.add_debtor(
                    user_id=target_user_id,
                    name=target_name,
                    amount=amount,
                    comment=comment,
                    username=target_username,
                )

                reply_text = (
                    f"Договорились, записал: <b>{html.escape(amount)}</b>"
                    + (f" ({html.escape(comment)})" if comment else "")
                    + f". Напоминание будет приходить каждый день в {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d}."
                )

                try:
                    sent_msg = await bot.send_message(
                        chat_id=message.chat.id,
                        text=reply_text,
                        business_connection_id=conn_id,
                        parse_mode=ParseMode.HTML,
                    )
                    loop_guard.record_bot_reply(sent_msg.message_id, reply_text)
                    if settings.DELETE_TRIGGER_MESSAGE:
                        await _cleanup_owner_trigger_message(bot, message, conn_id, reply_text)

                    # Notify owner in bot PM
                    await bot.send_message(
                        chat_id=owner_id,
                        text=(
                            f"✅ <b>Должник поставлен на учет из бизнес-чата!</b>\n\n"
                            f"• Имя: <b>{html.escape(target_name)}</b>\n"
                            f"• ID чата: <code>{target_user_id}</code>\n"
                            f"• Сумма: <code>{html.escape(amount)}</code>\n"
                            f"• Примечание: <i>{html.escape(comment or 'Без примечания')}</i>\n\n"
                            "Управление должниками: /debts"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e:
                    logger.error(f"Error handling .debt business command: {e}", exc_info=True)
                return

        # Command: .closedebt or .долгзакрыт or .payed
        if is_owner and message.text.strip().lower() in (".closedebt", ".долгзакрыт", ".payed"):
            active_debts = await db.get_active_debts_for_user(message.chat.id)
            if active_debts:
                for d in active_debts:
                    await db.close_debt(d["id"])

                reply_text = "Долг успешно закрыт! Претензий больше нет. Спасибо!"
                try:
                    sent_msg = await bot.send_message(
                        chat_id=message.chat.id,
                        text=reply_text,
                        business_connection_id=conn_id,
                        parse_mode=ParseMode.HTML,
                    )
                    loop_guard.record_bot_reply(sent_msg.message_id, reply_text)
                    if settings.DELETE_TRIGGER_MESSAGE:
                        await _cleanup_owner_trigger_message(bot, message, conn_id, reply_text)

                    await bot.send_message(
                        chat_id=owner_id,
                        text=f"✅ <b>Долг с {html.escape(message.chat.first_name or '')} (ID: {message.chat.id}) успешно закрыт!</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e:
                    logger.error(f"Error closing debt in business chat: {e}", exc_info=True)
                return

        # 5. Load applicable triggers
        triggers = await db.get_triggers_for_target(role)
        if not triggers:
            return

        # 6. Match message text against triggers
        matched_trigger = TriggerMatcher.match(message.text, triggers)
        if not matched_trigger:
            return

        trigger_id = matched_trigger["id"]
        trigger_keyword = matched_trigger.get("display_keyword", matched_trigger["keyword"])

        # 7. Check anti-spam cooldown per chat per trigger
        if cooldown_manager.is_on_cooldown(message.chat.id, trigger_id, settings.COOLDOWN_SECONDS):
            logger.info(
                f"Cooldown active for chat {message.chat.id} on trigger '{trigger_keyword}'. Suppressing reply."
            )
            return

        # 8. Prepare response text, dynamically interpolating CARD_NUMBER (handles multiple cards)
        raw_response = matched_trigger["response"]
        response_text = format_card_response(raw_response, settings.CARD_NUMBER)

        # 9. Send response on behalf of the account
        msg_type = matched_trigger.get("msg_type", "text")
        sent_message = None

        try:
            if msg_type == "text":
                sent_message = await bot.send_message(
                    chat_id=message.chat.id,
                    text=response_text,
                    business_connection_id=conn_id,
                    parse_mode=ParseMode.HTML,
                )
            elif msg_type == "photo":
                sent_message = await bot.send_photo(
                    chat_id=message.chat.id,
                    photo=response_text,
                    business_connection_id=conn_id,
                    parse_mode=ParseMode.HTML,
                )
            elif msg_type == "document":
                sent_message = await bot.send_document(
                    chat_id=message.chat.id,
                    document=response_text,
                    business_connection_id=conn_id,
                    parse_mode=ParseMode.HTML,
                )
            elif msg_type == "sticker":
                sent_message = await bot.send_sticker(
                    chat_id=message.chat.id,
                    sticker=response_text,
                    business_connection_id=conn_id,
                )
            else:
                sent_message = await bot.send_message(
                    chat_id=message.chat.id,
                    text=response_text,
                    business_connection_id=conn_id,
                    parse_mode=ParseMode.HTML,
                )

            logger.info(
                f"Fired trigger '{trigger_keyword}' [target={role}] in chat {message.chat.id}. "
                f"Sent message ID: {getattr(sent_message, 'message_id', 'unknown')}"
            )

            # Record cooldown and loop guard
            cooldown_manager.record_trigger(message.chat.id, trigger_id)
            if sent_message:
                loop_guard.record_bot_reply(sent_message.message_id, response_text)

        except TelegramRetryAfter as e:
            logger.warning(f"Telegram flood control: retry after {e.retry_after}s for chat {message.chat.id}")
            return
        except TelegramForbiddenError as e:
            logger.error(
                f"Forbidden error sending business message to chat {message.chat.id}: {e}. "
                "Check bot rights and chatbot permissions in Telegram settings."
            )
            return
        except TelegramBadRequest as e:
            logger.error(f"Bad request sending business message to chat {message.chat.id}: {e}")
            return
        except TelegramAPIError as e:
            logger.error(f"Telegram API error sending business message: {e}")
            return
        except Exception as e:
            logger.error(f"Unexpected error sending business message: {e}", exc_info=True)
            return

        # 10. Clean up owner's trigger message if configured
        if is_owner and settings.DELETE_TRIGGER_MESSAGE:
            await _cleanup_owner_trigger_message(bot, message, conn_id, response_text)

    @router.edited_business_message()
    async def on_edited_business_message(message: Message) -> None:
        """Handle edited messages in business chats safely."""
        logger.debug(f"Edited business message {message.message_id} in chat {message.chat.id}")

    @router.deleted_business_messages()
    async def on_deleted_business_messages(action: BusinessMessagesDeleted) -> None:
        """Handle deleted messages notifications in business chats."""
        logger.debug(
            f"Business messages deleted in chat {action.chat.id}: {action.message_ids} "
            f"(connection: {action.business_connection_id})"
        )

    return router


async def _cleanup_owner_trigger_message(
    bot: Bot, message: Message, conn_id: str, fallback_text: str
) -> None:
    """Attempt to delete the owner trigger message, or gracefully fall back if rights are missing."""
    try:
        # Use Bot API 7.2+ delete_business_messages method
        await bot.delete_business_messages(
            business_connection_id=conn_id,
            message_ids=[message.message_id],
        )
        logger.debug(f"Deleted owner trigger message {message.message_id}")
    except TelegramBadRequest as e:
        logger.warning(
            f"Cannot delete business message {message.message_id} ({e}). "
            "Attempting fallback edit to clean chat..."
        )
        try:
            # Fallback: Edit the trigger message so the chat looks clean
            await bot.edit_message_text(
                text=fallback_text,
                chat_id=message.chat.id,
                message_id=message.message_id,
                business_connection_id=conn_id,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Successfully edited owner trigger message {message.message_id} as fallback.")
        except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as edit_err:
            logger.warning(
                f"Fallback edit also failed for message {message.message_id}: {edit_err}. "
                "Ensure 'Delete sent messages' permission is granted in Telegram Business settings."
            )
    except (TelegramForbiddenError, TelegramAPIError) as e:
        logger.warning(f"Could not delete owner trigger message: {e}")
