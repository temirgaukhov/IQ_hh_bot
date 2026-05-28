from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import ContextTypes, ConversationHandler

from database import get_user, save_user
from sheets import is_valid_key

AWAITING_KEY = 0


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Поиск соискателя", callback_data="search")]
    ])


async def _send_main_menu(message: Message) -> None:
    await message.reply_text("🏠 Главное меню", reply_markup=_main_keyboard())


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


async def check_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = update.message.text.strip()

    if is_valid_key(key):
        save_user(update.effective_user.id, key)
        context.user_data["access_key"] = key
        await _send_main_menu(update.message)
        return ConversationHandler.END

    await update.message.reply_text(
        "❌ Неверный ключ доступа. Попробуйте ещё раз:"
    )
    return AWAITING_KEY


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🏠 Главное меню", reply_markup=_main_keyboard())
