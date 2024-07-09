import logging
# Remove per_message, ... warnings
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)
from celeritas.db import UserDB
from celeritas.user import User
from celeritas.config import config
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.utils import utc_time_now, sol_dollar_value
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.telegram_bot.handlers.buy_handler import token_buy_conv_handler
from celeritas.telegram_bot.handlers.settings_handler import settings_conv_handler
from celeritas.telegram_bot.handlers.sell_menu_handler import sell_menu_conv_handler, withdraw_menu_conv_handler
from celeritas.telegram_bot.handlers.withdraw_handler import withdraw_conv_handler
from celeritas.telegram_bot.handlers.sniper_menu_handler import sniper_menu_conv_handler
from celeritas.telegram_bot.user_sequential_update_processor import UserSequentialUpdateProcessor

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Database
db = UserDB()


async def generate_start_message(user, new=False):
    message_text = (
        f"<i>Welcome {user.full_name} to the Celeritas bot!\n"
        "Your new trading partner.</i>\n\n"
    ) if new else ""
    balance = user.sol_in_wallet
    balance_str = (
        f"0 SOL ($0.00)"
        if balance == 0
        else f"{round(balance, 4)} SOL (${round(balance*sol_dollar_value(), 2)})"
    )  # $VALUE should be calculated based on the current SOL price

    message_text += (
        f'Solana · <a href="https://solscan.io/account/{user.wallet_public}">🅴</a>\n'
        f"<code>{user.wallet_public}</code> (Tap to copy)\n\n"
        f"Balance: <code>{balance_str}</code>\n\n"
        "Click on the Refresh button to update your current balance.\n\n"
        "Join our Telegram group @to_be_determined_69420 for users of Celeritas!\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("Buy", callback_data=str(BUY)),
            InlineKeyboardButton("Sell", callback_data=str(NEW_SELL_MENU)),
        ],
        [
            #InlineKeyboardButton("Limit Orders", callback_data=str(LIMIT_ORDERS)),
            #InlineKeyboardButton("DCA Orders", callback_data=str(DCA_ORDERS)),
            InlineKeyboardButton("Pump.fun Sniper", callback_data=str(NEW_SNIPER_MENU)),
        ],
        [
            #InlineKeyboardButton("Copy Trade", callback_data=str(COPY_TRADE)),
        ],
        [
            InlineKeyboardButton("Referrals", callback_data=str(REFERRALS)),
            InlineKeyboardButton("Withdraw", callback_data=str(NEW_WITHDRAW_MENU)),
        ],
        [
            InlineKeyboardButton("Settings", callback_data=str(SETTINGS_NEW)),
        ],
        [
            InlineKeyboardButton("Help", callback_data=str(HELP)),
            InlineKeyboardButton("Refresh", callback_data=str(REFRESH)),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    return message_text, reply_markup


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    name = update.effective_user.name
    full_name = update.effective_user.full_name
    new = False
    # Add a new user
    if not db.user_exists(user_id):
        user = User(id=user_id, name=name, full_name=full_name)
        if context.args and len(context.args) > 0:
            try:
                referrer_id = int(context.args[0])
                if db.user_exists(referrer_id):
                    user.referrer = referrer_id
                    n = db.get_attribute(referrer_id, "users_referred")+1
                    db.update_attribute(referrer_id, "users_referred", n)
            except ValueError:
                pass
        db.add_user(user)
        new = True
    else:
        user = db.get_user(user_id)
    
    message_text, reply_markup = await generate_start_message(user, new)

    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    logger.info("User %s started a conversation.", update.message.from_user.first_name)
    return MAIN_MENU


async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    db.update_sol_balance(user_id)
    user = db.get_user(user_id)
    message_text, reply_markup = await generate_start_message(user)

    await query.edit_message_text(
        text=message_text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return MAIN_MENU

async def prompt_for_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    text = (
        "🪙 <b>Ready to buy your token?</b> 🪙\n\n"
        "Just <b>paste the token mint</b> you'd like to purchase below. 📥\n"
        "We're here to make your purchase smooth and easy! 😊"
    )

    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]]
    )

    await query.message.reply_text(
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    return MAIN_MENU


async def help_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    text = (
        "👋 <b>Welcome to Celeritas Help!</b> 👋\n\n"
        "🚀 <b>Celeritas</b> is your ultimate Solana trading companion, right here in Telegram! "
        "We're here to make trading smooth and fast, whether you're a seasoned pro or just starting out. 💰\n\n"
        "<b>Here's a quick rundown of our superpowers:</b>\n\n"
        "✨ <b>Buying Tokens:</b>\n"
        "  - Simply paste the token mint address, and we'll handle the rest!\n"
        "  - Choose your buy amount (in SOL) and adjust your slippage tolerance.\n"
        "  - We'll hunt down the best route and execute your trade with lightning speed! ⚡\n\n"
        "💰 <b>Selling Tokens:</b>\n"
        "  - Browse through your token holdings and select the one you'd like to sell.\n"
        "  - Decide how much to sell (percentage or custom amount) and set your slippage.\n"
        "  - We'll find the most efficient path and sell your tokens in a flash! 💸\n\n"
        "⚙️ <b>Settings:</b>\n"
        "  - <b>Priority Fees:</b> Control transaction speed by choosing between Rapid, Lightning, or Custom fees.\n"
        "  - <b>Buy/Sell Settings:</b> Customize your default buy and sell amounts and slippage preferences.\n"
        "  - <b>Confirm Trades:</b> Add an extra layer of security by requiring confirmation before each trade.\n"
        "  - <b>MEV Protection:</b> Protect yourself from front-running bots (may increase execution time).\n"
        "  - <b>Auto Buy:</b> Enable automatic purchases by sending a token mint as a message.\n"
        "    - ⚠️ Use with caution! This feature will execute trades automatically based on your settings.\n\n"
        "🏹 <b>Pump.fun Sniper:</b>\n"
        "  - Coming Soon! ⏳ Be the first to snatch up tokens on pump.fun.\n\n"
        "🤝 <b>Referrals:</b>\n"
        "  - Share the love! Invite your friends and earn rewards for every trade they make.\n\n"
        "➡️ <b>Withdraw:</b>\n"
        "  - Easily transfer your SOL or SPL tokens to any Solana wallet.\n\n"
        "❓ <b>Need more help?</b> Join our Telegram group @to_be_determined_69420 for support and updates!\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )

    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]]
    )
    await query.message.reply_text(
        text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    return MAIN_MENU

async def referrals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    referral_link = f"t.me/{context.bot.username}?start={user_id}"

    text = (
        "🌟 <b>Celeritas Referral Program</b> 🌟\n\n"
        "Share the trading magic and rake in the rewards! 💰\n\n"
        f"<b>Your Unique Referral Link:</b>\n<code>{referral_link}</code>\n\n"
        "🤝 <b>How It Works:</b>\n"
        "• Invite your friends to join Celeritas using your link.\n"
        "• You'll earn a percentage of the trading fees they pay when they use the bot.\n"
        "• The more friends you invite, the more you earn! 📈\n\n"
        "💰 <b>Earnings Breakdown:</b>\n"
        "• You get <b>30%</b> of your direct referrals' trading fees. 🎉\n"
        "• You also earn from users referred by your referrals, up to 5 levels deep! 🤯\n"
        " • Level 2: 3.5%\n"
        " • Level 3: 2.5%\n"
        " • Level 4: 2%\n"
        " • Level 5: 1%\n\n"
        "📊 <b>Your Referral Stats:</b>\n"
        f"• Friends Invited: <code>{user.users_referred}</code>\n"
        f"• Total Earnings: <code>{nfpf(user.trading_fees_earned)} SOL</code>\n"
        f"• Paid Out: <code>{nfpf(user.trading_fees_paid_out)} SOL</code>\n"
        f"• Available: <code>{nfpf(user.trading_fees_earned - user.trading_fees_paid_out)} SOL</code>\n\n"
        "ℹ️ <b><u>Fees are paid out daily to users with at least 0.005 SOL in accrued unpaid fees.</u></b>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]
    ])

    await query.message.reply_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    return MAIN_MENU

async def close_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.delete()


def main() -> None:
    """Run the bot."""
    with open("data/t_secret", "r") as token:
        application = (
            Application
            .builder()
            .token(config.telegram_bot_token)
            .concurrent_updates(
                UserSequentialUpdateProcessor(10)
            )
            .build()
        )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(refresh, pattern="^" + "start" + "$"),
                CallbackQueryHandler(refresh, pattern="^" + str(REFRESH) + "$"),
                CallbackQueryHandler(prompt_for_token, pattern="^" + str(BUY) + "$"),
                CallbackQueryHandler(help_message, pattern="^" + str(HELP) + "$"),
                CallbackQueryHandler(close_message, pattern="^" + str(CLOSE_MESSAGE) + "$"),
                CallbackQueryHandler(referrals, pattern="^" + str(REFERRALS) + "$"),
                settings_conv_handler,
                sell_menu_conv_handler,
                withdraw_menu_conv_handler,
                withdraw_conv_handler,
                sniper_menu_conv_handler,
                token_buy_conv_handler,

            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv_handler)
    application.add_handler(token_buy_conv_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
