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
buy_settings, BUY_SETTINGS - f. and handler to to enter buy settings
buy_slippage, BUY_SLIPPAGE - f. to change slippage, goes to buy_slippage_input
buy_slippage_input, BUY_SLIPPAGE_INPUT
buy_amount, BUY_AMOUNT:{index}, f. to change buy amounts based on index, goes to buy_amount_input
buy_amount_input, BUY_AMOUNT_INPUT
"""


def generate_buy_settings_keyboard(user_settings):
    keyboard = [
        [
            InlineKeyboardButton(
                "💰 Buy Amounts 💰",
                callback_data="none",
            )
        ],
    ]
    keyboard.append(
        [
            InlineKeyboardButton(
                f"🟢 {user_settings.buy_amounts[0]} SOL ✏️",
                callback_data=f"{BUY_AMOUNT}:{0}",
            ),
            InlineKeyboardButton(
                f"🔵 {user_settings.buy_amounts[1]} SOL ✏️",
                callback_data=f"{BUY_AMOUNT}:{1}",
            ),
            InlineKeyboardButton(
                f"🟣 {user_settings.buy_amounts[2]} SOL ✏️",
                callback_data=f"{BUY_AMOUNT}:{2}",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                f"🟠 {user_settings.buy_amounts[3]} SOL ✏️",
                callback_data=f"{BUY_AMOUNT}:{3}",
            ),
            InlineKeyboardButton(
                f"🔴 {user_settings.buy_amounts[4]} SOL ✏️",
                callback_data=f"{BUY_AMOUNT}:{4}",
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                f"📈 Buy Slippage: {user_settings.buy_slippage}% ✏️",
                callback_data=str(BUY_SLIPPAGE),
            )
        ]
    )
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=str(SETTINGS))])
    return InlineKeyboardMarkup(keyboard)


def buy_settings_text():
    return (
        "🛒 <b><u>Buy Settings</u></b> 🛒\n\n"
        "Configure your token purchase preferences.\n\n"
        "💰 <b>Buy Amounts:</b>\n"
        "Set predefined amounts of SOL to use for buying tokens.\n"
        "🟢 Smallest | 🔵 Small | 🟣 Medium | 🟠 Large | 🔴 Largest\n"
        "Click on a button to change the amount.\n\n"
        "📈 <b>Buy Slippage:</b>\n"
        "Set the maximum price increase you're willing to accept.\n"
        "Higher slippage may result in faster execution but potentially higher prices.\n\n"
        "ℹ️ <i>Tip: Diversify your buy amounts to adapt to different market conditions.</i>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )


async def buy_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_settings = user_db.get_user_settings(user_id)
    query = update.callback_query
    await query.answer()
    reply_markup = generate_buy_settings_keyboard(user_settings)

    message = await query.edit_message_text(
        text=buy_settings_text(), reply_markup=reply_markup, parse_mode="HTML"
    )
    context.user_data["buy_settings_message_id"] = message.message_id

    return BUY_SETTINGS


async def buy_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    target_index = int(query.data.split(":")[1])
    context.user_data["target_index"] = target_index
    message = await query.message.reply_text(text="Please enter your Buy amount in SOL: (e.g. 1, 5,...)")
    context.user_data["buy_message_id"] = message.message_id
    return BUY_AMOUNT_INPUT


async def buy_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    target_index = context.user_data["target_index"]
    try:
        buy_amount = max(0.002, float(update.message.text))
        user_settings = user_db.get_user_settings(user_id)
        user_settings.buy_amounts[target_index] = buy_amount
        user_db.update_user_settings(user_id, "buy_amounts", user_settings.buy_amounts)
        reply_markup = generate_buy_settings_keyboard(user_settings)
        # Delete the message where the user entered their buy amount
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("buy_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("buy_settings_message_id"),
            buy_settings_text(),
            reply_markup,
        )
        return BUY_SETTINGS
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("buy_message_id"),
            text="Invalid input. Please enter a valid number for the Buy amount.",
        )
        return BUY_AMOUNT_INPUT


async def buy_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your Buy slippage in %: (e.g. 50 or 50%)")
    context.user_data["buy_message_id"] = message.message_id
    return BUY_SLIPPAGE_INPUT


async def buy_slippage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        slippage = float(update.message.text.replace("%", ""))
        slippage = int(max(1, slippage))
        user_settings = user_db.update_user_settings(user_id, "buy_slippage", slippage)
        reply_markup = generate_buy_settings_keyboard(user_settings)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("buy_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("buy_settings_message_id"),
            buy_settings_text(),
            reply_markup,
        )
        return BUY_SETTINGS
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("buy_message_id"),
            text="Invalid input. Please enter a valid number for the Buy slippage. (e.g. 20 would mean slippage of 20%)",
        )
        return BUY_SLIPPAGE_INPUT


buy_settings_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(buy_settings, pattern="^" + str(BUY_SETTINGS) + "$")],
    states={
        BUY_SETTINGS: [
            CallbackQueryHandler(buy_settings, pattern="^" + str(BUY_SETTINGS) + "$"),
            CallbackQueryHandler(buy_amount, pattern="^" + str(BUY_AMOUNT) + r":\d+$"),
            CallbackQueryHandler(buy_slippage, pattern="^" + str(BUY_SLIPPAGE) + "$"),
        ],
        BUY_AMOUNT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_amount_input)],
        BUY_SLIPPAGE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_slippage_input)],
    },
    fallbacks=[CommandHandler(str(BUY_SETTINGS), buy_settings)],
)
