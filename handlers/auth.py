import logging

from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Message,
)
from telegram.ext import ContextTypes, ConversationHandler

from database import get_user, save_user
from sheets import is_valid_key, append_to_requests

logger = logging.getLogger(__name__)

AWAITING_KEY   = 0
AWAITING_NAME  = 1
AWAITING_PHONE = 2


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск соискателя", callback_data="search")]
    ])


async def _send_main_menu(message: Message) -> None:
    await message.reply_text("🏠 Главное меню", reply_markup=_main_keyboard())


# ── /start ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    row = get_user(user_id)

    if row:
        context.user_data["access_key"] = row["access_key"]
        await _send_main_menu(update.message)
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n🔑 Введите ключ доступа:"
    )
    return AWAITING_KEY


# ── Этап 1: проверка ключа ───────────────────────────────────────────────────

async def check_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = update.message.text.strip()

    if is_valid_key(key):
        save_user(update.effective_user.id, key)
        context.user_data["access_key"] = key
        await _send_main_menu(update.message)
        return ConversationHandler.END

    await update.message.reply_text(
        "Вы не имеете доступа к резюме, но это временно...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Ввести ключ ещё раз", callback_data="retry_key")],
            [InlineKeyboardButton("📝 Оставить заявку",     callback_data="apply_access")],
        ]),
    )
    return AWAITING_KEY


# ── Этап 2а: пользователь нажал «Ввести ключ ещё раз» ──────────────────────

async def handle_retry_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🔑 Введите ключ доступа:")
    return AWAITING_KEY


# ── Этап 2б: пользователь нажал «Оставить заявку» ───────────────────────────

async def handle_apply_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Введите ФИО попечителя или наименование компании:"
    )
    return AWAITING_NAME


# ── Этап 3: пользователь ввёл имя ───────────────────────────────────────────

async def handle_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["applicant_name"] = update.message.text.strip()

    phone_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Поделиться телефоном", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Отправьте контакты для связи:",
        reply_markup=phone_keyboard,
    )
    return AWAITING_PHONE


# ── Этап 4: пользователь поделился телефоном ────────────────────────────────

async def handle_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contact = update.message.contact
    phone   = contact.phone_number
    user_id = update.effective_user.id
    name    = context.user_data.get("applicant_name", "—")

    timestamp = datetime.now().strftime("%d.%m.%y %H:%M")

    try:
        append_to_requests([str(user_id), name, phone, timestamp])
        logger.info("Заявка записана: user_id=%s name=%s", user_id, name)
    except Exception as exc:
        logger.error("Ошибка записи заявки в Sheets: %s", exc)

    await update.message.reply_text(
        "✅ Ваша заявка принята! Мы свяжемся с вами в ближайшее время.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── Главное меню (callback) ──────────────────────────────────────────────────

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🏠 Главное меню", reply_markup=_main_keyboard())
