from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import Update
from telegram.ext import CallbackQueryHandler
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes
from telegram.ext import ConversationHandler
from telegram.ext import filters
from telegram.ext import MessageHandler

from celeritas.db import user_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.utils import delete_messages
from celeritas.telegram_bot.utils import edit_message
from celeritas.telegram_bot.utils import utc_time_now

"""
sell_settings, SELL_SETTINGS - f. and handler to to enter sell settings
sell_slippage, SELL_SLIPPAGE - f. to change slippage, goes to sell_slippage_input
sell_slippage_input, SELL_SLIPPAGE_INPUT
sell_amount, SELL_AMOUNT:{index}, f. to change sell amounts based on index, goes to sell_amount_input
sell_amount_input, SELL_AMOUNT_INPUT
"""


def generate_sell_settings_keyboard(user_settings):
    keyboard = [
        [
            InlineKeyboardButton(
                "-- Sell Percentages --",
                callback_data="none",
            )
        ],
    ]
    keyboard.append(
        [
            InlineKeyboardButton(
                f"🔴 {user_settings.sell_amounts[0]}% ✏️",
                callback_data=f"{SELL_AMOUNT}:{0}",
            ),
            InlineKeyboardButton(
                f"🟡 {user_settings.sell_amounts[1]}% ✏️",
                callback_data=f"{SELL_AMOUNT}:{1}",
            ),
            InlineKeyboardButton(
                f"🟢 {user_settings.sell_amounts[2]}% ✏️",
                callback_data=f"{SELL_AMOUNT}:{2}",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                f"Sell Slippage: {user_settings.sell_slippage}% ✏️",
                callback_data=str(SELL_SLIPPAGE),
            )
        ]
    )
    keyboard.append([InlineKeyboardButton("← Back", callback_data=str(SETTINGS))])
    return InlineKeyboardMarkup(keyboard)


def sell_settings_text():
    return (
        "🛠 <b><u>Sell Settings</u></b> 🛠\n\n"
        "Configure how you want to sell your tokens.\n\n"
        "📊 <b>Sell Percentages:</b>\n"
        "Set predetermined sell percentages of your holdings.\n"
        "🔴 Low  |  🟡 Medium  |  🟢 High\n"
        "Allowed values: 1-100%\n\n"
        "📈 <b>Sell Slippage:</b>\n"
        "Set the maximum price increase you're willing to accept. "
        "Higher slippage increases the likelihood of order execution, but may lead to less favorable prices.\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )


async def sell_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_settings = user_db.get_user_settings(user_id)
    query = update.callback_query
    await query.answer()
    reply_markup = generate_sell_settings_keyboard(user_settings)

    # Edit the settings panel message and store the message ID
    message = await query.edit_message_text(
        text=sell_settings_text(), reply_markup=reply_markup, parse_mode="HTML"
    )
    context.user_data["sell_settings_message_id"] = message.message_id

    return SELL_SETTINGS


async def sell_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    target_index = int(query.data.split(":")[1])
    context.user_data["target_index"] = target_index
    message = await query.message.reply_text(text="Please enter your Sell Percentage in %: (e.g. 50 or 50%)")
    context.user_data["sell_message_id"] = message.message_id
    return SELL_AMOUNT_INPUT


async def sell_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    target_index = context.user_data["target_index"]
    try:
        sell_amount = float(update.message.text.replace("%", ""))
        sell_amount = int(min(100, max(1, sell_amount)))
        user_settings = user_db.get_user_settings(user_id)
        user_settings.sell_amounts[target_index] = sell_amount
        user_db.update_user_settings(user_id, "sell_amounts", user_settings.sell_amounts)
        reply_markup = generate_sell_settings_keyboard(user_settings)
        # Delete the message where the user entered their sell amount
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("sell_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("sell_settings_message_id"),
            sell_settings_text(),
            reply_markup,
        )
        return SELL_SETTINGS
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("sell_message_id"),
            text="Invalid input. Please enter a valid percentage. (e.g. 20 or 20% for 20%)",
        )
        return SELL_AMOUNT_INPUT


async def sell_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your Sell slippage in % (e.g. 25 or 25%):")
    context.user_data["sell_message_id"] = message.message_id
    return SELL_SLIPPAGE_INPUT


async def sell_slippage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        slippage = float(update.message.text.replace("%", ""))
        slippage = int(max(1, slippage))
        user_settings = user_db.update_user_settings(user_id, "sell_slippage", slippage)
        reply_markup = generate_sell_settings_keyboard(user_settings)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("sell_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("sell_settings_message_id"),
            sell_settings_text(),
            reply_markup,
        )
        return SELL_SETTINGS
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("sell_message_id"),
            text="Invalid input. Please enter a valid number for the Sell slippage. (e.g. 20 would mean slippage of 20%)",
        )
        return SELL_SLIPPAGE_INPUT


sell_settings_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(sell_settings, pattern="^" + str(SELL_SETTINGS) + "$")],
    states={
        SELL_SETTINGS: [
            CallbackQueryHandler(sell_settings, pattern="^" + str(SELL_SETTINGS) + "$"),
            CallbackQueryHandler(sell_amount, pattern="^" + str(SELL_AMOUNT) + r":\d+$"),
            CallbackQueryHandler(sell_slippage, pattern="^" + str(SELL_SLIPPAGE) + "$"),
        ],
        SELL_AMOUNT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_amount_input)],
        SELL_SLIPPAGE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_slippage_input)],
    },
    fallbacks=[CommandHandler(str(SELL_SETTINGS), sell_settings)],
)
