import logging

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
from celeritas.telegram_bot.handlers.auto_buy_handler import autobuy_conv_handler
from celeritas.telegram_bot.handlers.auto_sell_handler import autosell_conv_handler
from celeritas.telegram_bot.handlers.buy_settings_handler import buy_settings_handler
from celeritas.telegram_bot.handlers.sell_settings_handler import sell_settings_handler
from celeritas.telegram_bot.handlers.wallet_settings_handler import wallet_settings_handler
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.telegram_bot.utils import delete_messages
from celeritas.telegram_bot.utils import edit_message
from celeritas.telegram_bot.utils import utc_time_now

logger = logging.getLogger(__name__)

FAST_FEE, LIGHTNING_FEE = 0.001, 0.008


async def generate_settings_keyboard(user_settings) -> InlineKeyboardMarkup:
    # Handle speed settings
    fast, lightning, custom = "Fast 🚀", "Lightning ⚡", "Custom ✏️"
    if user_settings.priority_fee == FAST_FEE:
        fast = "🔵 " + fast
    elif user_settings.priority_fee == LIGHTNING_FEE:
        lightning = "🔵 " + lightning
    else:
        custom = f"🔵 {user_settings.priority_fee} SOL"

    def toggle_button(text, is_active):
        return ("🟢" if is_active else "🔴") + f" {text}"

    confirm_trades = toggle_button("Confirm Trades", user_settings.confirm_trades)
    autobuy = toggle_button("Auto Buy", user_settings.autobuy)
    mev_protect = toggle_button("MEV Protection", user_settings.mev_protection)
    chart_previews = toggle_button("Chart Previews", user_settings.chart_previews)
    min_pos_value = f"Min Pos Value: {f"{nfpf(user_settings.min_pos_value)} USD" if user_settings.min_pos_value else '--'}"

    keyboard = [
        [InlineKeyboardButton("--Priority Fees--", callback_data="none")],
        [
            InlineKeyboardButton(fast, callback_data=str(FEE_FAST)),
            InlineKeyboardButton(lightning, callback_data=str(FEE_LIGHTNING)),
            InlineKeyboardButton(custom, callback_data=str(FEE_CUSTOM)),
        ],
        [InlineKeyboardButton("--Trading Settings--", callback_data="none")],
        [
            InlineKeyboardButton("Buy Settings 💰", callback_data=str(BUY_SETTINGS)),
            InlineKeyboardButton("Sell Settings 💸", callback_data=str(SELL_SETTINGS)),
        ],
        [InlineKeyboardButton("--General Settings--", callback_data="none")],
        [
            InlineKeyboardButton(confirm_trades, callback_data=str(CONFIRM_TRADES)),
            InlineKeyboardButton(mev_protect, callback_data=str(MEV_PROTECTION)),
        ],
        [
            InlineKeyboardButton(autobuy, callback_data=str(AUTO_BUY)),
            InlineKeyboardButton(chart_previews, callback_data=str(CHART_PREVIEWS)),
        ],
        [
            InlineKeyboardButton(min_pos_value, callback_data=str(MIN_POS_VALUE)),
            InlineKeyboardButton("Wallet Settings" ,callback_data=str(WALLET_SETTINGS)),
        ],
        [InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_SETTINGS_MENU))],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    return reply_markup


def settings_text():
    return (
        "🚀 <b><u>Priority Fees:</u></b>\n"
        f"<b>Fast</b>: <code>{FAST_FEE} SOL</code> - Average speed, good for most transactions.\n"
        f"<b>Lightning</b>: <code>{LIGHTNING_FEE} SOL</code> - Fastest speed, ensures high success rate.\n"
        "<b>Custom</b>: Set your own fee value for more precise control.\n\n"
        "🤖 <b><u>Auto Buy:</u></b>\n"
        "Enable automated token purchases upon sending a token mint as a message.\n"
        "⚠️ <i>Use Auto Buy cautiously as it executes trades automatically based on your settings!</i>\n\n"
        "<u><b>Confirm Trades</b></u>\nEnable/disable confirmation prompts before executing any buy or sell order. \n\n"
        "<u><b>MEV Protection</b></u>\nReduce the risk of front-running bots by adding additional steps to trades. Be aware, this can lead to slower execution times. 🐢\n\n"
        "<u><b>Min Pos Value</b></u>\nSet the minimum USD value for token positions to appear in the sell menu. \n\n"
        "<u><b>Chart Previews</b></u>\nEnable/disable previews for links with trading chart data (like Dexscreener). 📈\n\n"
        f"🕒 <i>{utc_time_now()}</i>\n\n"
    )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE, new=False) -> int:
    user_id = update.effective_user.id
    current_user_settings = user_db.get_user_settings(user_id)
    query = update.callback_query
    if query: await query.answer()
    
    reply_markup = await generate_settings_keyboard(current_user_settings)

    if new:
        message_func = query.message.reply_text if query else update.message.reply_text
        message = await message_func(
            text=settings_text(), reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        message = await query.edit_message_text(
            text=settings_text(), reply_markup=reply_markup, parse_mode="HTML"
        )
    context.user_data["settings_message_id"] = message.message_id

    return SETTINGS


async def close_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Delete the settings message
    chat_id = query.message.chat_id
    await context.bot.delete_message(chat_id, query.message.message_id)


async def settings_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await settings(update, context, new=True)


async def set_fee_fast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    current_user_settings = user_db.update_user_settings(user_id, "priority_fee", FAST_FEE)
    # Edit keyboard to reflect changed settings
    reply_markup = await generate_settings_keyboard(current_user_settings)
    await query.edit_message_text(text=settings_text(), reply_markup=reply_markup, parse_mode="HTML")
    return SETTINGS


async def set_fee_lightning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    current_user_settings = user_db.update_user_settings(user_id, "priority_fee", LIGHTNING_FEE)
    # Edit keyboard to reflect changed settings
    reply_markup = await generate_settings_keyboard(current_user_settings)
    await query.edit_message_text(text=settings_text(), reply_markup=reply_markup, parse_mode="HTML")
    return SETTINGS


async def set_fee_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your custom fee in SOL:")
    context.user_data["custom_fee_message_id"] = message.message_id
    return CUSTOM_FEE


async def custom_fee_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        fee_size = float(update.message.text)
        fee_size = max(0.0004, fee_size)
        current_user_settings = user_db.update_user_settings(user_id, "priority_fee", fee_size)
        reply_markup = await generate_settings_keyboard(current_user_settings)
        # Delete the message where the user entered their custom fee
        # and the user's response message
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("custom_fee_message_id"),
            update.message.message_id,
        )
        # Edit the original settings message
        await edit_message(
            context,
            chat_id,
            context.user_data.get("settings_message_id"),
            settings_text(),
            reply_markup,
        )
        return SETTINGS
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("custom_fee_message_id"),
            text="Invalid input. Please enter a valid number for the custom fee.",
        )
        return CUSTOM_FEE


async def set_min_pos_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text("Enter minimum position value in USD for sell menu:")
    context.user_data["min_pos_value_message_id"] = message.message_id
    return MIN_POS_VALUE_INPUT


async def min_pos_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        min_pos_value = max(0, float(update.message.text))
        current_user_settings = user_db.update_user_settings(user_id, "min_pos_value", min_pos_value)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("min_pos_value_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("settings_message_id"),
            settings_text(),
            await generate_settings_keyboard(current_user_settings),
        )
        return SETTINGS
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("min_pos_value_message_id"),
            text="Invalid input. Please enter a valid number.",
        )
        return MIN_POS_VALUE_INPUT


async def confirm_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    # Update user settings
    current_user_settings = user_db.update_user_settings(
        user_id, "confirm_trades", not user_db.get_user_settings(user_id).confirm_trades
    )
    # Edit keyboard to reflect changed settings
    reply_markup = await generate_settings_keyboard(current_user_settings)
    await query.edit_message_text(text=settings_text(), reply_markup=reply_markup, parse_mode="HTML")
    return SETTINGS


async def mev_protection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    # Update user settings
    current_user_settings = user_db.update_user_settings(
        user_id, "mev_protection", not user_db.get_user_settings(user_id).mev_protection
    )
    # Edit keyboard to reflect changed settings
    reply_markup = await generate_settings_keyboard(current_user_settings)
    await query.edit_message_text(text=settings_text(), reply_markup=reply_markup, parse_mode="HTML")
    return SETTINGS


async def chart_previews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    # Update user settings
    current_user_settings = user_db.update_user_settings(
        user_id, "chart_previews", not user_db.get_user_settings(user_id).chart_previews
    )
    # Edit keyboard to reflect changed settings
    reply_markup = await generate_settings_keyboard(current_user_settings)
    await query.edit_message_text(text=settings_text(), reply_markup=reply_markup, parse_mode="HTML")
    return SETTINGS


# Conversation handler for settings
settings_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(settings_new, pattern="^" + str(SETTINGS_NEW) + "$"),
        CommandHandler("settings", settings_new),
    ],
    states={
        SETTINGS: [
            CallbackQueryHandler(settings, pattern="^" + str(SETTINGS) + "$"),
            CallbackQueryHandler(settings_new, pattern="^" + str(SETTINGS_NEW) + "$"),
            CallbackQueryHandler(set_fee_fast, pattern="^" + str(FEE_FAST) + "$"),
            CallbackQueryHandler(set_fee_custom, pattern="^" + str(FEE_CUSTOM) + "$"),
            CallbackQueryHandler(set_min_pos_value, pattern="^" + str(MIN_POS_VALUE) + "$"),
            CallbackQueryHandler(mev_protection, pattern="^" + str(MEV_PROTECTION) + "$"),
            CallbackQueryHandler(set_fee_lightning, pattern="^" + str(FEE_LIGHTNING) + "$"),
            CallbackQueryHandler(confirm_trades, pattern="^" + str(CONFIRM_TRADES) + "$"),
            CallbackQueryHandler(chart_previews, pattern="^" + str(CHART_PREVIEWS) + "$"),
            CallbackQueryHandler(close_settings_menu, pattern="^" + str(CLOSE_SETTINGS_MENU) + "$"),
            autobuy_conv_handler,
            autosell_conv_handler,
            buy_settings_handler,
            sell_settings_handler,
            wallet_settings_handler,
        ],
        CUSTOM_FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_fee_input)],
        MIN_POS_VALUE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, min_pos_value_input)],
    },
    fallbacks=[CommandHandler("settings", settings_new)],
)
