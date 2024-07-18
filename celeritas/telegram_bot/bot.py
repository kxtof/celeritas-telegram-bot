import logging
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
    filters
)
from celeritas.db import user_db
from celeritas.user import User
from celeritas.config import config
from celeritas.constants import LAMPORTS_PER_SOL
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.utils import utc_time_now, sol_dollar_value
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.telegram_bot.handlers.buy_handler import token_buy_conv_handler
from celeritas.telegram_bot.handlers.settings_handler import settings_conv_handler
from celeritas.telegram_bot.handlers.sell_menu_handler import (
    sell_menu_conv_handler,
    withdraw_menu_conv_handler,
)
from celeritas.telegram_bot.handlers.withdraw_handler import withdraw_conv_handler
from celeritas.telegram_bot.handlers.sniper_menu_handler import sniper_menu_conv_handler
from celeritas.telegram_bot.user_sequential_update_processor import UserSequentialUpdateProcessor

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def generate_start_message(user, new=False):
    message_text = (
        (
            f"<i>Welcome {user.full_name} to the TurboTendies bot!\n"
            "Fund your account by sending SOL to the wallet below.</i>\n\n"
        )   
        if new
        else ""
    )
    balance = user.sol_in_wallet
    logger.info(balance)
    balance_str = (
        f"0 SOL ($0.00)"
        if balance == 0
        else f"{nfpf(balance)} SOL (${nfpf(balance*sol_dollar_value())})"
    )  # $VALUE should be calculated based on the current SOL price

    message_text += (
        f'<b>Wallet</b> · <a href="https://solscan.io/account/{user.wallet_public}">🌐</a>\n'
        f"<code>{user.wallet_public}</code> (Tap me)\n\n"
        f"<b>Balance</b>: <code>{balance_str}</code>"
        f"<i>{"\nFund me 🥺👉👈" if balance == 0 else ""}</i>"
        "\n\nClick '<i>Refresh</i>' to update your balance.\n\n"
        "ℹ️ <b>TurboTendies is in a public beta</b>\n"
        "Please join us @turbo_tendies to report any bugs (and get a prize? 🤔😉) or ask questions.\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("Buy", callback_data=str(BUY)),
            InlineKeyboardButton("Sell", callback_data=str(NEW_SELL_MENU)),
        ],
        [
            # InlineKeyboardButton("Limit Orders", callback_data=str(LIMIT_ORDERS)),
            # InlineKeyboardButton("DCA Orders", callback_data=str(DCA_ORDERS)),
            InlineKeyboardButton("Pump.fun Sniper 💊🎯", callback_data=str(NEW_SNIPER_MENU)),
        ],
        [
            # InlineKeyboardButton("Copy Trade", callback_data=str(COPY_TRADE)),
        ],
        [
            InlineKeyboardButton("Referrals 🌟", callback_data=str(REFERRALS)),
            InlineKeyboardButton("Withdraw 📤", callback_data=str(NEW_WITHDRAW_MENU)),
        ],
        [
            InlineKeyboardButton("Settings ⚙️", callback_data=str(SETTINGS_NEW)),
        ],
        [
            InlineKeyboardButton("Help ❓", callback_data=str(HELP)),
            InlineKeyboardButton("Refresh 🔄", callback_data=str(REFRESH)),
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
    if not user_db.user_exists(user_id):
        user = User(id=user_id, name=name, full_name=full_name)
        if context.args and len(context.args) > 0:
            try:
                referrer_id = int(context.args[0])
                logger.info(f"should not be here {referrer_id}")
                if user_db.user_exists(referrer_id):
                    user.referrer = referrer_id
                    n = user_db.get_attribute(referrer_id, "users_referred") + 1
                    user_db.update_attribute(referrer_id, "users_referred", n)
            except ValueError:
                pass
        user_db.add_user(user)
        new = True
        # Inform admin about new user
        await context.bot.send_message(
            config.admin_telegram_account_id,
            f"<u><b>New user detected</b></u>\n<code>{user.id}</code>\n<code>{user.name}</code>\n<code>{user.full_name}</code>",
            parse_mode='HTML'
        )
    else:
        user = user_db.get_user(user_id)

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
    user_db.update_sol_balance(user_id)
    user = user_db.get_user(user_id)
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
    if query: await query.answer()

    user_id = update.effective_user.id
    text = (
        "🪙 <b>Ready to buy your token?</b> 🪙\n\n"
        "Just <b>paste the token mint</b> you'd like to purchase below. 📥\n\n"
        "ℹ <i>You can always paste a mint and the bot will return the buy menu, no need to tap the buy button.</i>"
    )

    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]]
    )

    message_func = query.message.reply_text if query else update.message.reply_text
    await message_func(
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    return MAIN_MENU


async def help_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query: await query.answer()

    text = (
        "🚀🐔 <b>TurboTendies</b> is your ultimate Solana trading companion, right here in Telegram! "
        "We're here to make trading smooth and fast, whether you're a seasoned pro or just starting out. 💰\n\n"
        "✨ <u><b>Buying Tokens</b></u>\n"
        "Simply paste the token mint address, and we'll handle the rest!"
        " Choose your buy amount (in SOL) and adjust your slippage tolerance."
        " We'll hunt down the best route and execute your trade with lightning speed! ⚡\n\n"
        "💰 <u><b>Selling Tokens:</b></u>\n"
        "Browse through your token holdings and select the one you'd like to sell."
        " Decide how much to sell (percentage or custom amount) and set your slippage."
        " We'll find the most efficient path and sell your tokens in a flash! 💸\n\n"
        "⚙️ <u><b>Settings</b></u>\n"
        "  <b>Priority Fees:</b> Control transaction speed by choosing between Fast, Lightning, or Custom fees.\n"
        "  <b>Buy/Sell Settings:</b> Customize your default buy and sell amounts and slippage preferences.\n"
        "  <b>Confirm Trades:</b> Add an extra layer of security by requiring confirmation before each trade.\n"
        "  <b>MEV Protection:</b> Protect yourself from front-running bots (may increase execution time).\n"
        "  <b>Min Pos Value:</b> Set the minimum value for token positions to appear in the Sell menu.\n"
        "  <b>Auto Buy:</b> Enable automatic purchases by sending a token mint as a message.\n"
        "  ⚠️ Use with caution! This feature will execute trades automatically based on your settings.\n\n"
        "💊 <u><b>Pump.fun Sniper</b></u>\n"
        "Be the first to snatch up new tokens."
        " Add wallets you wish to snipe."
        " Configure your sniping setup for each wallet."
        " The bot will automatically try to buy tokens when a new mint is detected.\n\n"
        "🤝 <u><b>Referrals</b></u>\n"
        "Share the love! Invite your friends and earn rewards for every trade they make.\n\n"
        "➡️ <u><b>Withdraw</b></u>\n"
        "Easily transfer your SOL or SPL tokens to any Solana wallet.\n\n"
        "❓ <u><b>Need more help?</b></u> Join our Telegram group @turbo_tendies for support and updates!\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )

    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]]
    )
    # Handle updates from both the callbackqueryhandler and the commandhandler
    message_func = query.message.reply_text if query else update.message.reply_text
    await message_func(
        text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    return MAIN_MENU


async def referrals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query: await query.answer()
    
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)
    referral_link = f"t.me/{context.bot.username}?start={user_id}"

    text = (
        "🌟 <b>TurboTendies Referral Program</b> 🌟\n\n"
        "Share the trading magic and rake in the rewards! 💰\n\n"
        f"<b>Your Unique Referral Link:</b>\n<code>{referral_link}</code>\n\n"
        "🤝 <b>How It Works:</b>\n"
        "• Invite your friends to join TurboTendies using your link.\n"
        "• You'll earn a percentage of the trading fees they pay when they use the bot.\n"
        "• The more friends you invite, the more you earn! 📈\n\n"
        "💰 <b>Earnings Breakdown:</b>\n"
        f"• You get <b>{nfpf(user.referral_share[0]*100)}%</b> of your direct referrals' trading fees. 🎉\n"
        "• You also earn from users referred by your referrals, up to 5 levels deep! 🤯\n"
        f" • Level 2: {nfpf(user.referral_share[1]*100)}%\n"
        f" • Level 3: {nfpf(user.referral_share[2]*100)}%\n"
        f" • Level 4: {nfpf(user.referral_share[3]*100)}%\n"
        f" • Level 5: {nfpf(user.referral_share[4]*100)}%\n\n"
        "📊 <b>Your Referral Stats:</b>\n"
        f"• Friends Invited: <code>{user.users_referred}</code>\n"
        f"• Total Earnings: <code>{nfpf(user.trading_fees_earned)} SOL</code>\n"
        f"• Paid Out: <code>{nfpf(user.trading_fees_paid_out)} SOL</code>\n"
        f"• Available: <code>{nfpf(user.trading_fees_earned - user.trading_fees_paid_out)} SOL</code>\n\n"
        "ℹ️ <b><u>Fees are paid out daily to users with at least 0.005 SOL in accrued unpaid fees.</u></b>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_MESSAGE))]]
    )

    message_func = query.message.reply_text if query else update.message.reply_text
    await message_func(
        text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    return MAIN_MENU


async def close_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    return MAIN_MENU


async def get_referral_payouts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Generates a CSV file with users eligible for referral payouts and their respective amounts.
    """
    users = user_db.users.find()
    referral_payouts = []
    
    for user in users:
        available_fees = user["trading_fees_earned"] - user["trading_fees_paid_out"]
        if available_fees >= 0.005:
            referral_payouts.append(
                {
                    "user_id": user["_id"],
                    "wallet": user["referral_wallet"],
                    "amount": available_fees,
                }
            )

    # Update only the eligible users in bulk
    if context.args and context.args[0] == 'no_test':
        logger.info(f"Referral amounts updated for users. ({update.effective_chat.id})")
        user_db.users.update_many(
            {"_id": {"$in": [payout['user_id'] for payout in referral_payouts]}},
            [{"$set": {"trading_fees_paid_out": "$trading_fees_earned"}}]
        )

    # Generate CSV content
    csv_content = "user,wallet,lamports,sol\n"
    for payout in referral_payouts:
        csv_content += f"{payout['user_id']},{payout['referral_wallet']},{int(payout['amount']*LAMPORTS_PER_SOL)},{payout['amount']}\n"
    
    # Send the CSV file to the administrator
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=csv_content.encode('utf-8'),
        filename="referral_payouts.csv",
    )

    logger.info(f"Referral payout CSV generated and sent to admin. ({update.effective_chat.id})")


async def get_revenue_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    total_revenue = sum(user["revenue"] for user in user_db.users.find())
    text = (
        "<u><b>Total revenue earned</b></u>\n"
        f"<code>{nfpf(total_revenue)} SOL (${nfpf(total_revenue*sol_dollar_value())})</code>"
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="HTML"
    )

    logger.info(f"Revenue report sent to admin. ({update.effective_chat.id})")

def main() -> None:
    """Run the bot."""
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .concurrent_updates(UserSequentialUpdateProcessor(10))
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(refresh, pattern="^" + "start" + "$"),
                CallbackQueryHandler(refresh, pattern="^" + str(REFRESH) + "$"),
                CallbackQueryHandler(prompt_for_token, pattern="^" + str(BUY) + "$"),
                CommandHandler("buy", prompt_for_token),
                CallbackQueryHandler(help_message, pattern="^" + str(HELP) + "$"),
                CommandHandler("help", help_message),
                CallbackQueryHandler(close_message, pattern="^" + str(CLOSE_MESSAGE) + "$"),
                CallbackQueryHandler(referrals, pattern="^" + str(REFERRALS) + "$"),
                CommandHandler("referrals", referrals),
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
    application.add_handler(settings_conv_handler)
    application.add_handler(sell_menu_conv_handler)
    application.add_handler(withdraw_menu_conv_handler)
    application.add_handler(sniper_menu_conv_handler)

    # Admin only
    application.add_handler(CommandHandler("get_referral_payouts", get_referral_payouts, filters.User(user_id=config.admin_telegram_account_id)))
    application.add_handler(CommandHandler("get_revenue_report", get_revenue_report, filters.User(user_id=config.admin_telegram_account_id)))

    application.add_handler(conv_handler)
    application.add_handler(token_buy_conv_handler)

    # Development mode
    if not config.webhook_url:
        application.run_polling(allowed_updates=Update.ALL_TYPES)   
    else:
        application.run_webhook(
            listen="0.0.0.0",
            port=int(config.webhook_port),
            secret_token="somethingverysecretyoucouldnotguessevenIfyoutrYed",
            key='private.key',
            cert='cert.pem',
            url_path=config.telegram_bot_token,
            webhook_url=f"https://{config.webhook_url}:{config.webhook_port}/{config.telegram_bot_token}"
        )
