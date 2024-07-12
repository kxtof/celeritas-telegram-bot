from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, filters, MessageHandler

from celeritas.db import user_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.utils import delete_messages, edit_message, utc_time_now

from solders.keypair import Keypair

import logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def generate_wallet_settings_keyboard(user):
    keyboard = []
    keyboard.append(
        [
            InlineKeyboardButton(
                "Wallet Import ✏️",
                callback_data=str(WALLET_IMPORT),
            ),
            InlineKeyboardButton(
                "Wallet Export",
                callback_data=str(WALLET_EXPORT)
            )
        ]
    )
    keyboard.append([InlineKeyboardButton("← Back", callback_data=str(SETTINGS))])
    return InlineKeyboardMarkup(keyboard)


def wallet_settings_text(user):
    return (
        f'<b>Wallet</b> • '
        f'<a href="https://solscan.io/account/{user.wallet_public}">🌐</a>\n'
        f'<code>{user.wallet_public}</code> (Tap me)\n\n'
        f'💡 <b>Options:</b>\n'
        f'• <b>Import:</b> Import a new wallet using its secret key.\n'
        f"• <b>Export:</b> Download your current wallet's secret key. \n\n"
        f'    <i>⚠️ <b>Please handle your secret key with utmost care!</b> Exporting your wallet removes its security guarantees.</i>\n\n'
        f"🕒 <i>{utc_time_now()}</i>"
    )


async def wallet_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user = user_db.get_user(user_id)
    reply_markup = generate_wallet_settings_keyboard(user)

    # Edit the settings panel message and store the message ID
    message = await query.edit_message_text(
        text=wallet_settings_text(user), reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    context.user_data["wallet_settings_message_id"] = message.message_id

    return WALLET_SETTINGS


async def wallet_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)
    text = (
        f"<code>{user.wallet_secret}</code>\n\n"
        "<b>Beware that by exporting your wallet,"
        " its security can no longer be ensured by us.</b>"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]])
    message = await query.message.reply_text(
        text=text, reply_markup=reply_markup, parse_mode="HTML"
    )

    return WALLET_SETTINGS


async def close_wallet_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    return WALLET_SETTINGS


async def wallet_import(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    confirmation_text = (
        f"⚠️ <b><u>Important: Wallet Import Confirmation</u></b>\n\n"
        f"<b>Importing a new wallet will completely replace your current wallet:</b>\n<code>{user.wallet_public}</code>\n\n"
        f"This action is <b>irreversible and will result in the loss</b> of access to your current wallet. "
        f"You will lose all of your previous transactions, holdings, and settings. \n\n"
        f"<b>Are you sure you want to proceed with importing a new wallet?</b>"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data=str(CONFIRM_WALLET_IMPORT)),
            InlineKeyboardButton("❌ Cancel", callback_data=str(CLOSE_MESSAGE)),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = await query.message.reply_text(
        text=confirmation_text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    context.user_data["wallet_import_message_id"] = message.message_id
    return WALLET_SETTINGS


async def confirm_wallet_import(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    await delete_messages(context, chat_id, context.user_data["wallet_import_message_id"])
    message = await query.message.reply_text(text="Please enter your new wallet's secret key:")
    context.user_data["wallet_import_input_message_id"] = message.message_id
    return WALLET_IMPORT_INPUT


async def wallet_import_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        keypair = Keypair.from_base58_string(update.message.text)
        user_db.update_attribute(user_id, "wallet_public", str(keypair.pubkey()))
        user_db.update_attribute(user_id, "wallet_secret", str(keypair))
        user = user_db.get_user(user_id)
        reply_markup = generate_wallet_settings_keyboard(user)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("wallet_import_input_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("wallet_settings_message_id"),
            wallet_settings_text(user),
            reply_markup,
        )
        return WALLET_SETTINGS
    except:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("wallet_import_input_message_id"),
            text="Invalid input. Please enter a valid keypair.",
        )
        return WALLET_IMPORT_INPUT


wallet_settings_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(wallet_settings, pattern="^" + str(WALLET_SETTINGS) + "$")],
    states={
        WALLET_SETTINGS: [
            CallbackQueryHandler(wallet_settings, pattern="^" + str(WALLET_SETTINGS) + "$"),
            CallbackQueryHandler(wallet_import, pattern="^" + str(WALLET_IMPORT) + "$"),
            CallbackQueryHandler(wallet_export, pattern="^" + str(WALLET_EXPORT) + "$"),
            CallbackQueryHandler(close_wallet_export, pattern="^" + str(CLOSE_MESSAGE) + "$"),
            CallbackQueryHandler(confirm_wallet_import, pattern="^" + str(CONFIRM_WALLET_IMPORT) + "$"),
            #CallbackQueryHandler(close_wallet_export, pattern="^" + str(CLOSE_MESSAGE) + "$"),
        ],
        WALLET_IMPORT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_import_input)],
    },
    fallbacks=[CallbackQueryHandler(wallet_settings, pattern="^" + str(WALLET_SETTINGS) + "$")],
)