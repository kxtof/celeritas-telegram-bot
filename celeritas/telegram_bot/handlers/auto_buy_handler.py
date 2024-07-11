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
auto_buy, AUTO_BUY - function and handler to enter auto_buy settings
auto_buy_change, AUTO_BUY_CHANGE - f. and handler to change bool value of user.settings.autobuy

auto_buy_amount, AUTO_BUY_AMOUNT - f. to change amount, goes to auto_buy_input 
auto_buy_amount_input - user inputs amount for autobuy
AUTO_BUY_AMOUNT_INPUT - handler to input autobuy amount

auto_buy_slippage, AUTO_BUY_SLIPPAGE - f. to change, slippage
auto_buy_slippage_input
AUTO_BUY_SLIPPAGE_INPUT
"""


def generate_auto_buy_keyboard(user_settings):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    ("🟢" if user_settings.autobuy else "🔴"),
                    callback_data=str(AUTO_BUY_CHANGE),
                )
            ],
            [
                InlineKeyboardButton(
                    f"Buy Amount: {user_settings.autobuy_amount} SOL ✏️",
                    callback_data=str(AUTO_BUY_AMOUNT),
                ),
                InlineKeyboardButton(
                    f"Slippage: {user_settings.autobuy_slippage}% ✏️",
                    callback_data=str(AUTO_BUY_SLIPPAGE),
                ),
            ],
            [InlineKeyboardButton("← Back", callback_data=str(SETTINGS))],
        ]
    )


def auto_buy_text():
    return (
        "🤖 <b>Auto Buy Settings</b> 🤖\n\n"
        "Configure your automatic purchase preferences:\n\n"
        "🔘 <b>Auto Buy:</b> Enable or disable automatic buying\n"
        "💰 <b>Buy Amount:</b> Set the amount of SOL to use for each auto buy\n"
        "📈 <b>Slippage:</b> Set the maximum price increase you're willing to accept\n\n"
        "<i>Click on any button to adjust its setting.</i>\n\n"
        "⚠️ <b>Explanation:</b> Auto Buy will use your predefined settings to make purchases automatically when you send a mint as a message.\n"
        "<b>Use with caution!</b>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )


async def auto_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_settings = user_db.get_user_settings(user_id)
    query = update.callback_query
    await query.answer()
    reply_markup = generate_auto_buy_keyboard(user_settings)
    # Edit the settings panel message and store the message ID
    message = await query.edit_message_text(
        text=auto_buy_text(), reply_markup=reply_markup, parse_mode="HTML"
    )
    context.user_data["auto_buy_settings_message_id"] = message.message_id

    return AUTO_BUY


async def auto_buy_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    # Get current settings, update value, write to user_db
    user_settings = user_db.update_user_settings(
        user_id, "autobuy", not user_db.get_user_settings(user_id).autobuy
    )
    # Edit keyboard to reflect changed settings
    reply_markup = generate_auto_buy_keyboard(user_settings)
    await query.edit_message_text(text=auto_buy_text(), reply_markup=reply_markup, parse_mode="HTML")
    return AUTO_BUY


async def auto_buy_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your Auto Buy amount in SOL:")
    context.user_data["auto_buy_message_id"] = message.message_id
    return AUTO_BUY_AMOUNT_INPUT


async def auto_buy_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        buy_amount = max(0.002, float(update.message.text))
        user_settings = user_db.update_user_settings(user_id, "autobuy_amount", buy_amount)
        reply_markup = generate_auto_buy_keyboard(user_settings)
        # Delete the message where the user entered their buy amount
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("auto_buy_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("auto_buy_settings_message_id"),
            auto_buy_text(),
            reply_markup,
        )
        return AUTO_BUY
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("auto_buy_message_id"),
            text="Invalid input. Please enter a valid number for the Auto Buy amount.",
        )
        return AUTO_BUY_AMOUNT_INPUT


async def auto_buy_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(
        text="Please enter your Auto Buy slippage in %: (e.g. 50 or 50%)"
    )
    context.user_data["auto_buy_message_id"] = message.message_id
    return AUTO_BUY_SLIPPAGE_INPUT


async def auto_buy_slippage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        slippage = float(update.message.text.replace("%", ""))
        slippage = int(max(1, slippage))
        user_settings = user_db.update_user_settings(user_id, "autobuy_slippage", slippage)
        reply_markup = generate_auto_buy_keyboard(user_settings)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("auto_buy_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("auto_buy_settings_message_id"),
            auto_buy_text(),
            reply_markup,
        )
        return AUTO_BUY
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("auto_buy_message_id"),
            text="Invalid input. Please enter a valid number for the Auto Buy slippage. (e.g. 20 would mean slippage of 20%)",
        )
        return AUTO_BUY_SLIPPAGE_INPUT


autobuy_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(auto_buy, pattern="^" + str(AUTO_BUY) + "$")],
    states={
        AUTO_BUY: [
            CallbackQueryHandler(auto_buy, pattern="^" + str(AUTO_BUY) + "$"),
            CallbackQueryHandler(auto_buy_change, pattern="^" + str(AUTO_BUY_CHANGE) + "$"),
            CallbackQueryHandler(auto_buy_amount, pattern="^" + str(AUTO_BUY_AMOUNT) + "$"),
            CallbackQueryHandler(auto_buy_slippage, pattern="^" + str(AUTO_BUY_SLIPPAGE) + "$"),
        ],
        AUTO_BUY_AMOUNT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_buy_amount_input)],
        AUTO_BUY_SLIPPAGE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_buy_slippage_input)],
    },
    fallbacks=[CommandHandler(str(AUTO_BUY), auto_buy)],
)
