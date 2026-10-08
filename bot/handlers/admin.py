"""Private chat administration handlers and FSM for trigger & debtor management."""

from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.services.scheduler import send_debt_closed_notification, send_debt_reminder
from bot.services.storage import Database
from bot.services.triggers import format_card_response, normalize_text

logger = logging.getLogger(__name__)

router = Router(name="admin_router")

# Restrict all events in this router to private chats with the bot owner
router.message.filter(F.chat.type == "private", F.from_user.id == settings.OWNER_ID)
router.callback_query.filter(F.message.chat.type == "private", F.from_user.id == settings.OWNER_ID)


# ---------------- FSM State Groups ----------------


class AddTriggerStates(StatesGroup):
    """FSM states for creating a new trigger."""

    waiting_for_keyword = State()
    waiting_for_target = State()
    waiting_for_match_type = State()
    waiting_for_response = State()


class AddDebtStates(StatesGroup):
    """FSM states for registering a new debtor."""

    waiting_for_user = State()
    waiting_for_name = State()
    waiting_for_amount = State()
    waiting_for_comment = State()
    waiting_for_confirmation = State()


# ---------------- Keyboards ----------------


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main dashboard keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Должники на учете", callback_data="menu_debts"),
                InlineKeyboardButton(text="➕ Поставить на учет", callback_data="menu_add_debt"),
            ],
            [
                InlineKeyboardButton(text="📋 Список триггеров", callback_data="menu_triggers"),
                InlineKeyboardButton(text="ℹ️ Статус подключения", callback_data="menu_status"),
            ],
        ]
    )


def get_target_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting who triggers the response."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Только собеседник (interlocutor)", callback_data="target:interlocutor"),
            ],
            [
                InlineKeyboardButton(text="👑 Только я (owner)", callback_data="target:owner"),
            ],
            [
                InlineKeyboardButton(text="👥 Для обоих (both)", callback_data="target:both"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm"),
            ],
        ]
    )


def get_match_type_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting matching strategy."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 Точное совпадение (exact)", callback_data="match:exact"),
            ],
            [
                InlineKeyboardButton(text="🔍 Поиск подстроки (contains)", callback_data="match:contains"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm"),
            ],
        ]
    )


def get_debtor_card_keyboard(debtor_id: int) -> InlineKeyboardMarkup:
    """Inline buttons under each debtor card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔔 Напомнить сейчас", callback_data=f"debt_remind:{debtor_id}"),
                InlineKeyboardButton(text="✅ Снять с учета (погашен)", callback_data=f"debt_clear_confirm:{debtor_id}"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить запись", callback_data=f"debt_delete:{debtor_id}"),
            ],
        ]
    )


def format_debtor_card_text(d: Dict[str, Any]) -> str:
    """Format single debtor representation for admin view."""
    d_id = d["id"]
    name = html.escape(str(d["name"]))
    user_id = d["user_id"]
    username = f"(@{d['username']})" if d.get("username") else ""
    amount = html.escape(str(d["amount"]))
    comment = html.escape(str(d["comment"] or "Без примечания"))
    created_at = d.get("created_at", "—")
    last_notified = d.get("last_notified_at") or "Ещё не отправлялось"

    return (
        f"👤 <b>Должник #{d_id}: {name}</b>\n"
        f"• Telegram ID: <code>{user_id}</code> {username}\n"
        f"• Сумма долга: <b>{amount}</b>\n"
        f"• Примечание: <i>{comment}</i>\n"
        f"• На учете с: <code>{created_at}</code>\n"
        f"• Посл. напоминание: <code>{last_notified}</code>\n"
        f"• Режим: <i>ежедневно в {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d}</i>"
    )


def setup_admin_router(db: Database) -> Router:
    """Setup admin commands and FSM dialogs."""

    # ---------------- Start and Cancel ----------------

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        """Handle /start command with user instructions."""
        text = (
            "👋 <b>Панель управления ботом</b>\n\n"
            "<b>💳 Управление должниками:</b>\n"
            "▫️ /debts — список должников на учете и управление ими\n"
            "▫️ /add_debt — поставить человека на учет (напоминания каждый день в 12:00)\n\n"
            "<b>⚡️ Telegram Business & Автоответы:</b>\n"
            "▫️ /status — статус подключения к Business и права бота\n"
            "▫️ /list_triggers — список всех настроенных триггеров\n"
            "▫️ /add_trigger — пошаговое добавление нового триггера\n"
            "▫️ /del_trigger &lt;триггер_или_id&gt; — удаление триггера\n"
            "▫️ /cancel — отмена любого текущего действия\n\n"
            f"⏰ <i>Ежедневные напоминания должникам настроены на {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d} ({settings.TIMEZONE}).</i>"
        )
        await message.answer(text, reply_markup=get_main_menu_keyboard())

    @router.message(Command("cancel"), StateFilter("*"))
    @router.callback_query(F.data == "cancel_fsm", StateFilter("*"))
    async def process_cancel(event: Message | CallbackQuery, state: FSMContext) -> None:
        """Cancel any ongoing FSM conversation."""
        await state.clear()
        text = "❌ Действие отменено."
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.edit_text(text)
        else:
            await event.answer(text, reply_markup=get_main_menu_keyboard())

    # ---------------- Telegram Business Status ----------------

    @router.message(Command("status"))
    @router.callback_query(F.data == "menu_status")
    async def cmd_status(event: Message | CallbackQuery) -> None:
        """Show current business connection status and permissions."""
        conn = await db.get_active_connection_for_user(settings.OWNER_ID)
        all_conns = await db.get_all_business_connections()
        active_debts = await db.get_active_debtors()

        status_icon = "🟢" if conn and conn.get("is_enabled") else "🔴"
        can_reply = "✅ Да" if conn and conn.get("can_reply") else "❌ Нет"
        delete_setting = "✅ Включено" if settings.DELETE_TRIGGER_MESSAGE else "❌ Отключено"

        masked_card = settings.CARD_NUMBER
        if len(masked_card) > 8:
            masked_card = f"{masked_card[:4]} **** **** {masked_card[-4:]}"

        text = (
            f"<b>Статус системы:</b>\n\n"
            f"• <b>Telegram Business:</b> {status_icon} {'Активно' if conn and conn.get('is_enabled') else 'Не подключено / Отключено'}\n"
            f"• <b>ID соединения:</b> <code>{conn['connection_id'] if conn else 'отсутствует'}</code>\n"
            f"• <b>Право отвечать:</b> {can_reply}\n"
            f"• <b>Удаление триггеров:</b> {delete_setting}\n"
            f"• <b>Анти-спам кулдаун:</b> {settings.COOLDOWN_SECONDS} сек.\n"
            f"• <b>Номер карты:</b> <code>{masked_card}</code>\n"
            f"• <b>Должников на учете:</b> <b>{len(active_debts)}</b> чел.\n"
            f"• <b>Время рассылки:</b> {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d} ({settings.TIMEZONE})\n\n"
        )

        if not conn or not conn.get("is_enabled"):
            text += (
                "⚠️ <b>Внимание:</b> Бот пока не подключен в Telegram Business!\n"
                "1. Откройте <i>Настройки → Telegram Business → Чат-боты</i> на вашем аккаунте.\n"
                "2. Найдите этого бота и подключите его.\n"
                "3. Обязательно выдайте права: <b>Отвечать на сообщения</b> и <b>Управлять сообщениями</b>."
            )
        else:
            text += "🚀 Бот готов к обработке входящих и исходящих сообщений в подключенных бизнес-чатах."

        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(text)
        else:
            await event.answer(text)

    # ---------------- Debtors Management ----------------

    @router.message(Command("debts"))
    @router.callback_query(F.data == "menu_debts")
    async def cmd_debts(event: Message | CallbackQuery) -> None:
        """Display all active debtors with management buttons."""
        if isinstance(event, CallbackQuery):
            await event.answer()
            message = event.message
        else:
            message = event

        debtors = await db.get_active_debtors()
        if not debtors:
            text = (
                "📭 <b>На учете пока нет должников.</b>\n\n"
                "Чтобы поставить человека на учет, используйте команду /add_debt или кнопку ниже."
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Поставить должника на учет", callback_data="menu_add_debt")],
                ]
            )
            if message:
                await message.answer(text, reply_markup=keyboard)
            return

        header = (
            f"📋 <b>Список активных должников ({len(debtors)}):</b>\n"
            f"<i>Каждый день в {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d} "
            f"бот автоматически отправляет им напоминания.</i>\n"
        )
        if message:
            await message.answer(header)

            for d in debtors:
                card_text = format_debtor_card_text(d)
                keyboard = get_debtor_card_keyboard(d["id"])
                await message.answer(card_text, reply_markup=keyboard)

    @router.callback_query(F.data.startswith("debt_remind:"))
    async def cb_debt_remind(callback: CallbackQuery, bot: Bot) -> None:
        """Manually trigger a reminder message to a debtor from owner account."""
        debtor_id = int(callback.data.split(":")[1])
        debtor = await db.get_debtor_by_id(debtor_id)

        if not debtor or not debtor.get("is_active"):
            await callback.answer("⚠️ Должник не найден или уже снят с учета.", show_alert=True)
            return

        success, err = await send_debt_reminder(bot, db, debtor)
        if success:
            await db.update_last_notified(debtor_id)
            await callback.answer(f"✅ Отправлено {debtor['name']} от вашего аккаунта!", show_alert=True)
            # Update debtor card
            updated = await db.get_debtor_by_id(debtor_id)
            if updated and callback.message:
                try:
                    await callback.message.edit_text(
                        format_debtor_card_text(updated),
                        reply_markup=get_debtor_card_keyboard(debtor_id),
                    )
                except Exception:
                    pass
        else:
            await callback.answer(f"⚠️ Ошибка: {err}", show_alert=True)
            if callback.message:
                await callback.message.answer(
                    f"⚠️ <b>Не удалось отправить напоминание от вашего аккаунта</b>\n"
                    f"Должник: <b>{html.escape(debtor['name'])}</b> (ID: <code>{debtor['user_id']}</code>)\n"
                    f"Причина: <i>{err}</i>\n\n"
                    "💡 <i>Проверьте подключение: Настройки → Telegram Business → Чат-боты. "
                    "У бота должны быть включены права «Отвечать на сообщения».</i>"
                )

    @router.callback_query(F.data.startswith("debt_clear_confirm:"))
    async def cb_debt_clear_confirm(callback: CallbackQuery) -> None:
        """Ask confirmation before clearing debt and sending paid notification."""
        debtor_id = int(callback.data.split(":")[1])
        debtor = await db.get_debtor_by_id(debtor_id)

        if not debtor or not debtor.get("is_active"):
            await callback.answer("⚠️ Должник не найден или уже закрыт.", show_alert=True)
            return

        name = html.escape(str(debtor["name"]))
        amount = html.escape(str(debtor["amount"]))

        text = (
            f"❓ <b>Подтверждение закрытия задолженности</b>\n\n"
            f"Вы уверены, что хотите снять с учета <b>{name}</b>?\n"
            f"Сумма долга: <code>{amount}</code>\n\n"
            f"📩 <i>В ваш личный диалог с {name} от вашего имени будет отправлено сообщение о том, что долг закрыт и претензий нет.</i>"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, снять долг", callback_data=f"debt_clear_exec:{debtor_id}"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"debt_back:{debtor_id}"),
                ]
            ]
        )
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith("debt_back:"))
    async def cb_debt_back(callback: CallbackQuery) -> None:
        """Return to normal debtor card."""
        debtor_id = int(callback.data.split(":")[1])
        debtor = await db.get_debtor_by_id(debtor_id)
        if debtor and callback.message:
            await callback.message.edit_text(
                format_debtor_card_text(debtor),
                reply_markup=get_debtor_card_keyboard(debtor_id),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("debt_clear_exec:"))
    async def cb_debt_clear_exec(callback: CallbackQuery, bot: Bot) -> None:
        """Execute debt closing and send notification to debtor from owner's account."""
        debtor_id = int(callback.data.split(":")[1])
        debtor = await db.get_debtor_by_id(debtor_id)

        if not debtor or not debtor.get("is_active"):
            await callback.answer("⚠️ Должник не найден или уже закрыт.", show_alert=True)
            return

        # Mark closed in DB
        await db.close_debt(debtor_id)

        # Notify debtor from owner's personal account
        success, err = await send_debt_closed_notification(bot, db, debtor)

        name = html.escape(str(debtor["name"]))
        amount = html.escape(str(debtor["amount"]))

        if success:
            text = (
                f"✅ <b>Долг успешно закрыт!</b>\n\n"
                f"Должник <b>{name}</b> (сумма: <code>{amount}</code>) снят с учета.\n"
                f"🎉 В ваш диалог с ним <b>от вашего имени</b> отправлено подтверждение о погашении долга."
            )
        else:
            text = (
                f"✅ <b>Долг снят с учета в базе!</b>\n\n"
                f"Должник <b>{name}</b> (сумма: <code>{amount}</code>) закрыт.\n"
                f"⚠️ Однако сообщение от вашего аккаунта не доставлено ({err})."
            )

        if callback.message:
            await callback.message.edit_text(text)
        await callback.answer("Долг закрыт!", show_alert=True)

    @router.callback_query(F.data.startswith("debt_delete:"))
    async def cb_debt_delete(callback: CallbackQuery) -> None:
        """Delete debtor record without sending notifications."""
        debtor_id = int(callback.data.split(":")[1])
        await db.delete_debtor(debtor_id)
        if callback.message:
            await callback.message.edit_text(f"🗑 Запись о должнике #{debtor_id} полностью удалена из базы данных.")
        await callback.answer("Запись удалена.")

    # ---------------- FSM: Add Debtor Flow ----------------

    @router.message(Command("add_debt"))
    @router.callback_query(F.data == "menu_add_debt")
    async def cmd_add_debt(event: Message | CallbackQuery, state: FSMContext, bot: Bot) -> None:
        """Start the debtor onboarding wizard."""
        await state.clear()
        await state.set_state(AddDebtStates.waiting_for_user)

        bot_user = await bot.get_me()

        text = (
            "📝 <b>Шаг 1 из 4: Должник (пользователь Telegram)</b>\n\n"
            "Укажите человека, которого нужно поставить на учет:\n"
            "• <b>Перешлите (Forward)</b> любое сообщение от этого человека сюда\n"
            "• Или отправьте его <b>Telegram ID</b> (число, например <code>123456789</code>)\n"
            "• Или отправьте его <b>@username</b> (если он уже писал боту)\n\n"
            f"💡 <i>Чтобы бот гарантированно доставлял сообщения, должник должен хотя бы 1 раз нажать /start в боте: @{bot_user.username}</i>\n\n"
            "Для отмены отправьте /cancel"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
            ]
        )

        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(text, reply_markup=keyboard)
        else:
            await event.answer(text, reply_markup=keyboard)

    @router.message(AddDebtStates.waiting_for_user)
    async def process_debt_user(message: Message, state: FSMContext, bot: Bot) -> None:
        """Identify debtor user ID from forwarded message, contact, ID or username."""
        user_id: Optional[int] = None
        user_name_guess: Optional[str] = None
        username: Optional[str] = None

        # 1. Forwarded message check
        if message.forward_from:
            user_id = message.forward_from.id
            user_name_guess = message.forward_from.full_name
            username = message.forward_from.username
            await db.save_known_user(user_id, username, user_name_guess)
        elif message.forward_sender_name:
            bot_user = await bot.get_me()
            await message.answer(
                f"⚠️ У пользователя <b>{html.escape(message.forward_sender_name)}</b> скрыт Telegram ID в настройках приватности пересылки сообщений.\n\n"
                f"Пожалуйста, отправьте его цифровой Telegram ID (можно узнать через @userinfobot) "
                f"или попросите его запустить бота: @{bot_user.username}"
            )
            return

        # 2. Contact card check
        elif message.contact:
            user_id = message.contact.user_id
            user_name_guess = f"{message.contact.first_name} {message.contact.last_name or ''}".strip()
            username = None

        # 3. Text check: ID or @username
        elif message.text:
            cleaned = message.text.strip()
            if cleaned.isdigit():
                user_id = int(cleaned)
                known = await db.get_known_user(user_id)
                if known:
                    user_name_guess = known.get("full_name")
                    username = known.get("username")
            elif cleaned.startswith("@") or not cleaned.isdigit():
                clean_uname = cleaned.lstrip("@").lower()
                known = await db.get_known_user_by_username(clean_uname)
                if known:
                    user_id = known["user_id"]
                    user_name_guess = known.get("full_name")
                    username = known.get("username")
                else:
                    bot_user = await bot.get_me()
                    await message.answer(
                        f"⚠️ Пользователь <b>@{html.escape(clean_uname)}</b> ещё ни разу не писал этому боту, поэтому его цифровой ID не найден.\n\n"
                        f"Пожалуйста, введите его <b>цифровой Telegram ID</b> (можно узнать через бота @userinfobot) "
                        f"либо попросите его сначала написать /start боту @{bot_user.username}."
                    )
                    return

        if not user_id:
            await message.answer(
                "⚠️ Не удалось определить пользователя. Пожалуйста, отправьте цифровой Telegram ID или перешлите сообщение:"
            )
            return

        await state.update_data(
            user_id=user_id,
            username=username,
            name_guess=user_name_guess,
        )
        await state.set_state(AddDebtStates.waiting_for_name)

        buttons = []
        if user_name_guess:
            buttons.append([
                InlineKeyboardButton(
                    text=f"Использовать «{user_name_guess[:25]}»",
                    callback_data="use_name_guess"
                )
            ])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        uname_line = f" (@{username})" if username else ""
        await message.answer(
            f"👤 <b>Шаг 2 из 4: Имя или пометка должника</b>\n\n"
            f"Telegram ID: <code>{user_id}</code>{uname_line}\n\n"
            "Как записать человека в системе учета?\n"
            "<i>(Например: «Иван Иванов» или «Сергей Ремонт»)</i>",
            reply_markup=keyboard,
        )

    @router.callback_query(AddDebtStates.waiting_for_name, F.data == "use_name_guess")
    async def cb_use_name_guess(callback: CallbackQuery, state: FSMContext) -> None:
        """Use default name from forward/contact and proceed to amount."""
        data = await state.get_data()
        name = data.get("name_guess") or "Клиент"
        await state.update_data(name=name)
        await state.set_state(AddDebtStates.waiting_for_amount)
        await callback.answer()

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]]
        )
        if callback.message:
            await callback.message.edit_text(
                f"💰 <b>Шаг 3 из 4: Сумма задолженности</b>\n\n"
                f"Имя: <b>{html.escape(name)}</b>\n\n"
                "Напишите сумму долга (например: <code>5000 руб</code>, <code>25 000 ₽</code>, <code>$300</code>):",
                reply_markup=keyboard,
            )

    @router.message(AddDebtStates.waiting_for_name, F.text)
    async def process_debt_name(message: Message, state: FSMContext) -> None:
        """Receive custom debtor name and prompt for amount."""
        name = message.text.strip()
        if not name:
            await message.answer("⚠️ Имя не может быть пустым. Введите имя:")
            return

        await state.update_data(name=name)
        await state.set_state(AddDebtStates.waiting_for_amount)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]]
        )
        await message.answer(
            f"💰 <b>Шаг 3 из 4: Сумма задолженности</b>\n\n"
            f"Имя: <b>{html.escape(name)}</b>\n\n"
            "Напишите сумму долга (например: <code>5000 руб</code>, <code>25 000 ₽</code>, <code>$300</code>):",
            reply_markup=keyboard,
        )

    @router.message(AddDebtStates.waiting_for_amount, F.text)
    async def process_debt_amount(message: Message, state: FSMContext) -> None:
        """Receive debt amount and prompt for comments/notes."""
        amount = message.text.strip()
        if not amount:
            await message.answer("⚠️ Сумма не может быть пустой. Введите сумму:")
            return

        await state.update_data(amount=amount)
        await state.set_state(AddDebtStates.waiting_for_comment)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить (без примечания)", callback_data="skip_debt_comment")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
            ]
        )

        await message.answer(
            f"📝 <b>Шаг 4 из 4: Дополнительная информация / примечание</b>\n\n"
            f"Сумма: <b>{html.escape(amount)}</b>\n\n"
            "Напишите информацию, которая будет приходить должнику каждый день в 12:00 вместе с суммой долга.\n"
            "<i>(Например: реквизиты для оплаты, срок возврата, причина долга и т.д.)</i>\n\n"
            "Или нажмите «Пропустить»:",
            reply_markup=keyboard,
        )

    @router.callback_query(AddDebtStates.waiting_for_comment, F.data == "skip_debt_comment")
    async def cb_skip_debt_comment(callback: CallbackQuery, state: FSMContext) -> None:
        """Skip note step and show confirmation."""
        await state.update_data(comment="")
        await state.set_state(AddDebtStates.waiting_for_confirmation)
        await callback.answer()
        await _show_debt_confirmation(callback.message, state, is_edit=True)

    @router.message(AddDebtStates.waiting_for_comment, F.text)
    async def process_debt_comment(message: Message, state: FSMContext) -> None:
        """Receive note and show confirmation."""
        comment = message.text.strip()
        await state.update_data(comment=comment)
        await state.set_state(AddDebtStates.waiting_for_confirmation)
        await _show_debt_confirmation(message, state, is_edit=False)

    async def _show_debt_confirmation(msg_or_callback_msg: Optional[Message], state: FSMContext, is_edit: bool = False) -> None:
        """Helper to render confirmation summary before saving."""
        if not msg_or_callback_msg:
            return

        data = await state.get_data()
        name = html.escape(str(data.get("name", "")))
        user_id = data.get("user_id")
        username = f"(@{data.get('username')})" if data.get("username") else ""
        amount = html.escape(str(data.get("amount", "")))
        comment = html.escape(str(data.get("comment", "") or "Без примечания"))

        text = (
            "📋 <b>Проверьте данные перед постановкой на учет:</b>\n\n"
            f"• <b>Имя должника:</b> {name}\n"
            f"• <b>Telegram ID:</b> <code>{user_id}</code> {username}\n"
            f"• <b>Сумма долга:</b> <code>{amount}</code>\n"
            f"• <b>Дополнительная информация:</b>\n<i>{comment}</i>\n\n"
            f"⏰ <i>Бот будет автоматически отправлять уведомление с суммой и информацией каждый день в "
            f"{settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d} ({settings.TIMEZONE}).</i>"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить и поставить на учет", callback_data="confirm_debt_add")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
            ]
        )

        if is_edit:
            await msg_or_callback_msg.edit_text(text, reply_markup=keyboard)
        else:
            await msg_or_callback_msg.answer(text, reply_markup=keyboard)

    @router.callback_query(AddDebtStates.waiting_for_confirmation, F.data == "confirm_debt_add")
    async def cb_confirm_debt_add(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        """Save debtor record and offer immediate reminder."""
        data = await state.get_data()
        await state.clear()

        user_id = data["user_id"]
        name = data["name"]
        amount = data["amount"]
        comment = data.get("comment", "")
        username = data.get("username")

        debtor_id = await db.add_debtor(
            user_id=user_id,
            name=name,
            amount=amount,
            comment=comment,
            username=username,
        )

        text = (
            f"✅ <b>Должник {html.escape(name)} успешно поставлен на учет! (ID #{debtor_id})</b>\n\n"
            f"• Сумма: <code>{html.escape(amount)}</code>\n"
            f"• Время рассылки: <b>каждый день в {settings.NOTIFICATION_HOUR:02d}:{settings.NOTIFICATION_MINUTE:02d}</b>\n\n"
            "Вы можете отправить первое напоминание прямо сейчас или оставить отправку на 12:00."
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔔 Отправить напоминание сейчас", callback_data=f"debt_remind:{debtor_id}")],
                [InlineKeyboardButton(text="👥 Список всех должников", callback_data="menu_debts")],
            ]
        )

        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer("Должник поставлен на учет!")

    # ---------------- Triggers Management ----------------

    @router.message(Command("list_triggers"))
    @router.callback_query(F.data == "menu_triggers")
    async def cmd_list_triggers(event: Message | CallbackQuery) -> None:
        """Display all configured triggers with action buttons."""
        if isinstance(event, CallbackQuery):
            await event.answer()
            message = event.message
        else:
            message = event

        triggers = await db.get_all_triggers()
        if not triggers:
            if message:
                await message.answer("📭 Список триггеров пуст. Используйте /add_trigger для добавления.")
            return

        text = f"📋 <b>Список настроенных триггеров ({len(triggers)}):</b>\n\n"

        target_labels = {
            "owner": "👑 Владелец",
            "interlocutor": "👤 Собеседник",
            "both": "👥 Оба",
        }

        match_labels = {
            "exact": "точный",
            "contains": "подстрока",
        }

        buttons = []
        for trig in triggers:
            t_id = trig["id"]
            kw = trig["display_keyword"]
            tgt = target_labels.get(trig["target"], trig["target"])
            mt = match_labels.get(trig["match_type"], trig["match_type"])
            resp_preview = format_card_response(trig["response"], settings.CARD_NUMBER)
            if len(resp_preview) > 50:
                resp_preview = resp_preview[:47] + "..."

            text += (
                f"<b>#{t_id}</b> <code>{html.escape(kw)}</code>\n"
                f"  └ Для кого: <i>{tgt}</i> | Тип: <i>{mt}</i>\n"
                f"  └ Ответ: {html.escape(resp_preview)}\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    text=f"🗑 Удалить #{t_id} ({kw[:15]})",
                    callback_data=f"del_trig:{t_id}"
                )
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        if message:
            await message.answer(text, reply_markup=keyboard)

    @router.callback_query(F.data.startswith("del_trig:"))
    async def cb_delete_trigger(callback: CallbackQuery) -> None:
        """Handle trigger deletion via inline button."""
        trigger_id = callback.data.split(":")[1]
        deleted = await db.delete_trigger(trigger_id)

        if deleted:
            await callback.answer(f"Триггер #{trigger_id} удалён!", show_alert=True)
            if callback.message:
                await callback.message.edit_text(f"✅ Триггер #{trigger_id} успешно удалён.")
        else:
            await callback.answer("Ошибка: триггер не найден.", show_alert=True)

    @router.message(Command("del_trigger"))
    async def cmd_del_trigger(message: Message) -> None:
        """Delete trigger by keyword or ID passed in command args."""
        args = message.text.split(maxsplit=1)[1:] if message.text else []
        if not args:
            await message.answer(
                "ℹ️ Укажите ID или ключевое слово триггера для удаления.\n"
                "Пример: <code>/del_trigger .card</code> или <code>/del_trigger 2</code>\n"
                "Либо используйте /list_triggers для интерактивного удаления."
            )
            return

        target_identifier = args[0].strip()
        deleted = await db.delete_trigger(target_identifier)
        if deleted:
            await message.answer(f"✅ Триггер <code>{html.escape(target_identifier)}</code> успешно удалён.")
        else:
            await message.answer(f"❌ Триггер <code>{html.escape(target_identifier)}</code> не найден в базе.")

    # ---------------- FSM: Add Trigger Flow ----------------

    @router.message(Command("add_trigger"))
    async def cmd_add_trigger(message: Message, state: FSMContext) -> None:
        """Initiate trigger creation flow."""
        await state.clear()
        await state.set_state(AddTriggerStates.waiting_for_keyword)
        await message.answer(
            "📝 <b>Шаг 1 из 4: Ключевое слово или фраза</b>\n\n"
            "Напишите слово или фразу, на которую должен реагировать бот.\n"
            "<i>Примеры:</i> <code>.card</code>, <code>скинь карту</code>, <code>.hello</code>\n\n"
            "Для отмены отправьте /cancel"
        )

    @router.message(AddTriggerStates.waiting_for_keyword, F.text)
    async def process_keyword(message: Message, state: FSMContext) -> None:
        """Receive keyword and prompt for target audience."""
        keyword = message.text.strip()
        if not keyword:
            await message.answer("⚠️ Ключевое слово не может быть пустым. Попробуйте еще раз:")
            return

        await state.update_data(display_keyword=keyword, keyword=keyword.lower())
        await state.set_state(AddTriggerStates.waiting_for_target)
        await message.answer(
            f"🎯 <b>Шаг 2 из 4: Для кого срабатывает триггер?</b>\n\n"
            f"Ключевое слово: <code>{html.escape(keyword)}</code>\n\n"
            "Выберите, чьи сообщения должен отслеживать бот:",
            reply_markup=get_target_keyboard(),
        )

    @router.callback_query(AddTriggerStates.waiting_for_target, F.data.startswith("target:"))
    async def process_target(callback: CallbackQuery, state: FSMContext) -> None:
        """Receive target selection and prompt for match type."""
        target = callback.data.split(":")[1]
        await state.update_data(target=target)
        await state.set_state(AddTriggerStates.waiting_for_match_type)
        await callback.answer()

        target_names = {
            "interlocutor": "👤 Собеседник",
            "owner": "👑 Владелец",
            "both": "👥 Оба",
        }

        if callback.message:
            await callback.message.edit_text(
                f"🔍 <b>Шаг 3 из 4: Тип сопоставления</b>\n\n"
                f"Получатель: <b>{target_names.get(target, target)}</b>\n\n"
                "Выберите способ проверки текста сообщения:\n"
                "• <b>Точное совпадение (exact)</b> — сообщение должно в точности совпадать с триггером\n"
                "• <b>Поиск подстроки (contains)</b> — триггер может находиться внутри предложения",
                reply_markup=get_match_type_keyboard(),
            )

    @router.callback_query(AddTriggerStates.waiting_for_match_type, F.data.startswith("match:"))
    async def process_match_type(callback: CallbackQuery, state: FSMContext) -> None:
        """Receive match type and prompt for response text."""
        match_type = callback.data.split(":")[1]
        await state.update_data(match_type=match_type)
        await state.set_state(AddTriggerStates.waiting_for_response)
        await callback.answer()

        if callback.message:
            await callback.message.edit_text(
                "💬 <b>Шаг 4 из 4: Текст ответа бота</b>\n\n"
                "Отправьте текст сообщения, который бот должен отправлять в чат.\n\n"
                "<i>Поддерживается HTML-разметка: &lt;b&gt;жирный&lt;/b&gt;, &lt;code&gt;моноширинный&lt;/code&gt;.\n"
                "Вы также можете вставить <code>{CARD_NUMBER}</code> — бот автоматически заменит его на номер карты из .env.</i>\n\n"
                "Для отмены отправьте /cancel"
            )

    @router.message(AddTriggerStates.waiting_for_response, F.text)
    async def process_response(message: Message, state: FSMContext) -> None:
        """Save trigger and display confirmation."""
        response_text = message.text.strip()
        data = await state.get_data()
        await state.clear()

        keyword = data["keyword"]
        display_keyword = data["display_keyword"]
        target = data["target"]
        match_type = data["match_type"]

        trigger_id = await db.add_trigger(
            keyword=keyword,
            display_keyword=display_keyword,
            response=response_text,
            target=target,
            match_type=match_type,
            msg_type="text",
        )

        preview = response_text.replace("{CARD_NUMBER}", settings.CARD_NUMBER)

        await message.answer(
            f"✅ <b>Триггер успешно сохранён! (ID #{trigger_id})</b>\n\n"
            f"• <b>Триггер:</b> <code>{html.escape(display_keyword)}</code>\n"
            f"• <b>Для кого:</b> <code>{target}</code>\n"
            f"• <b>Тип:</b> <code>{match_type}</code>\n"
            f"• <b>Ответ:</b>\n{preview}\n\n"
            "Триггер уже активен в подключенных бизнес-чатах."
        )

    return router
