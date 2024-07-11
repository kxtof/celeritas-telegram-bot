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

"""
auto_sell, AUTO_SELL - function and handler to enter auto_sell settings
auto_sell_change, AUTO_SELL_CHANGE - f. and handler to change bool value of user.settings.autosell

auto_sell_target, AUTO_SELL_TARGET - set a target, should also handle indexing
auto_sell_target_input, AUTO_SELL_TARGET_INPUT - input for targe, in percentages

auto_sell_amount, AUTO_SELL_AMOUNT - set a target, should also handle indexing
auto_sell_amount_input, AUTO_SELL_AMOUNT_INPUT - input for amount, in percentages

auto_sell_add_order, AUTO_SELL_ADD_ORDER - f. to add order row

auto_sell_slippage, AUTO_SELL_SLIPPAGE - f. to change, slippage
auto_sell_slippage_input, AUTO_SELL_SLIPPAGE_INPUT
"""


def generate_auto_sell_keyboard(user_settings):
    keyboard = [
        [
            InlineKeyboardButton(
                ("🟢" if user_settings.autosell else "🔴"),
                callback_data=str(AUTO_SELL_CHANGE),
            )
        ],
    ]
    for index, target in enumerate(user_settings.autosell_targets):
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"T/P: {(str(target[0])+'% ') if target[0] else '--'} ✏️",
                    callback_data=f"{AUTO_SELL_TARGET}:{index}",
                ),
                InlineKeyboardButton(
                    f"Amount: {(str(target[1])+'%') if target[1] else '--'} ✏️",
                    callback_data=f"{AUTO_SELL_AMOUNT}:{index}",
                ),
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton("Add order", callback_data=str(AUTO_SELL_ADD_ORDER)),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                f"Slippage: {user_settings.autosell_slippage}% ✏️",
                callback_data=str(AUTO_SELL_SLIPPAGE),
            )
        ]
    )
    keyboard.append(
        [InlineKeyboardButton("← Back", callback_data=str(SETTINGS))],
    )
    return InlineKeyboardMarkup(keyboard)


async def auto_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_settings = user_db.get_user_settings(user_id)
    query = update.callback_query
    await query.answer()
    reply_markup = generate_auto_sell_keyboard(user_settings)
    # Edit the settings panel message and store the message ID
    message = await query.edit_message_text(text="Auto Sell Settings", reply_markup=reply_markup)
    context.user_data["auto_sell_settings_message_id"] = message.message_id

    return AUTO_SELL


async def auto_sell_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    # Get current settings, update value, write to user_db
    user_settings = user_db.update_user_settings(
        user_id, "autosell", not user_db.get_user_settings(user_id).autosell
    )
    # Edit keyboard to reflect changed settings
    reply_markup = generate_auto_sell_keyboard(user_settings)
    await query.edit_message_text(text="Auto Sell Settings", reply_markup=reply_markup)
    return AUTO_SELL


async def auto_sell_add_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    # Get current settings, update value, write to user_db
    user_settings = user_db.get_user_settings(user_id)
    user_settings.autosell_targets.append([None, None])
    user_db.update_user_settings(user_id, "autosell_targets", user_settings.autosell_targets)
    # Edit keyboard to reflect changed settings
    reply_markup = generate_auto_sell_keyboard(user_settings)
    await query.edit_message_text(text="Auto Sell Settings", reply_markup=reply_markup)
    return AUTO_SELL


async def auto_sell_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    target_index = int(query.data.split(":")[1])
    context.user_data["target_index"] = target_index
    message = await query.message.reply_text(
        text=f"Please enter your target price percentage for the target:"
    )
    context.user_data["auto_sell_message_id"] = message.message_id
    return AUTO_SELL_TARGET_INPUT


async def auto_sell_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    target_index = context.user_data["target_index"]
    try:
        target_price = float(update.message.text.replace("%", ""))
        user_settings = user_db.get_user_settings(user_id)
        user_settings.autosell_targets[target_index][0] = target_price if abs(target_price) > 0.01 else None
        user_db.update_user_settings(user_id, "autosell_targets", user_settings.autosell_targets)
        reply_markup = generate_auto_sell_keyboard(user_settings)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("auto_sell_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("auto_sell_settings_message_id"),
            "Auto Sell target price set successfully.",
            reply_markup,
        )
        return AUTO_SELL
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("auto_sell_message_id"),
            text="Invalid input. Please enter a valid number for the target price percentage.",
        )
        return AUTO_SELL_TARGET_INPUT


async def auto_sell_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    target_index = int(query.data.split(":")[1])
    context.user_data["target_index"] = target_index
    message = await query.message.reply_text(text=f"Please enter the amount percentage for the target:")
    context.user_data["auto_sell_message_id"] = message.message_id
    return AUTO_SELL_AMOUNT_INPUT


async def auto_sell_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    target_index = context.user_data["target_index"]
    try:
        amount_percentage = float(update.message.text.replace("%", ""))
        user_settings = user_db.get_user_settings(user_id)
        user_settings.autosell_targets[target_index][1] = (
            amount_percentage if abs(amount_percentage) > 0.01 else None
        )
        user_db.update_user_settings(user_id, "autosell_targets", user_settings.autosell_targets)
        reply_markup = generate_auto_sell_keyboard(user_settings)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("auto_sell_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("auto_sell_settings_message_id"),
            "Auto Sell amount percentage set successfully.",
            reply_markup,
        )
        return AUTO_SELL
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("auto_sell_message_id"),
            text="Invalid input. Please enter a valid number for the amount percentage.",
        )
        return AUTO_SELL_AMOUNT_INPUT


async def auto_sell_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your Auto Sell slippage in %:")
    context.user_data["auto_sell_message_id"] = message.message_id
    return AUTO_SELL_SLIPPAGE_INPUT


async def auto_sell_slippage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        slippage = float(update.message.text.replace("%", ""))
        user_settings = user_db.update_user_settings(user_id, "autosell_slippage", slippage)
        reply_markup = generate_auto_sell_keyboard(user_settings)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("auto_sell_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("auto_sell_settings_message_id"),
            "Auto Sell slippage set successfully.\n\nsettings panel\n\nfaq:",
            reply_markup,
        )
        return AUTO_SELL
    except ValueError:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("auto_sell_message_id"),
            text="Invalid input. Please enter a valid number for the Auto Sell slippage. (e.g. 20 would mean slippage of 20%)",
        )
        return AUTO_SELL_SLIPPAGE_INPUT


autosell_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(auto_sell, pattern="^" + str(AUTO_SELL) + "$")],
    states={
        AUTO_SELL: [
            CallbackQueryHandler(auto_sell, pattern="^" + str(AUTO_SELL) + "$"),
            CallbackQueryHandler(auto_sell_change, pattern="^" + str(AUTO_SELL_CHANGE) + "$"),
            CallbackQueryHandler(auto_sell_slippage, pattern="^" + str(AUTO_SELL_SLIPPAGE) + "$"),
            CallbackQueryHandler(auto_sell_target, pattern="^" + str(AUTO_SELL_TARGET) + r":\d+$"),
            CallbackQueryHandler(auto_sell_amount, pattern="^" + str(AUTO_SELL_AMOUNT) + r":\d+$"),
            CallbackQueryHandler(auto_sell_add_order, pattern="^" + str(AUTO_SELL_ADD_ORDER) + "$"),
        ],
        AUTO_SELL_TARGET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_sell_target_input)],
        AUTO_SELL_AMOUNT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_sell_amount_input)],
        AUTO_SELL_SLIPPAGE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_sell_slippage_input)],
    },
    fallbacks=[CommandHandler(str(AUTO_SELL), auto_sell)],
)
