import time

from solders.pubkey import Pubkey
from spl.token.constants import TOKEN_PROGRAM_ID
from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import Update
from telegram.ext import CallbackQueryHandler
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes
from telegram.ext import ConversationHandler
from telegram.ext import filters
from telegram.ext import MessageHandler

from celeritas.constants import aclient
from celeritas.db import token_db
from celeritas.db import user_db
from celeritas.db import transaction_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.fetch_tx_update_msg import schedule_tx_update
from celeritas.telegram_bot.utils import center_arrow
from celeritas.telegram_bot.utils import delete_messages
from celeritas.telegram_bot.utils import edit_message
from celeritas.telegram_bot.utils import nice_float_price_format
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import utc_time_now
from celeritas.transact import Transact


async def generate_token_buy_keyboard(user, token, options) -> InlineKeyboardMarkup:
    buy_amounts = user.settings.buy_amounts
    amount_to_buy = options["amount"]
    slippage = options["slippage"]
    base_slippage = user.settings.buy_slippage

    keyboard = [
        [
            InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_TOKEN_BUY)),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"{REFRESH_TOKEN_BUY}_{token['mint']}"),
        ],
        # [InlineKeyboardButton("--- Amount to buy (SOL) ---", callback_data="none")],
        [
            InlineKeyboardButton(
                f"{'🔵 ' if a == amount_to_buy else ''}{a} SOL",
                callback_data=f"{AMOUNT_TO_BUY}_{a}",
            )
            for a in buy_amounts[:3]
        ],
        [
            InlineKeyboardButton(
                f"{'🔵 ' if a == amount_to_buy else ''}{a} SOL",
                callback_data=f"{AMOUNT_TO_BUY}_{a}",
            )
            for a in buy_amounts[3:]
        ]
        + [
            InlineKeyboardButton(
                f"🔵 {amount_to_buy} SOL" if amount_to_buy not in buy_amounts else "Custom ✏️",
                callback_data=str(AMOUNT_TO_BUY_CUSTOM),
            )
        ],
        # [InlineKeyboardButton("--- Slippage to use ---", callback_data="none")],
        [
            InlineKeyboardButton(
                f"{'🔵 ' if base_slippage == slippage else ''}{base_slippage}% Slippage",
                callback_data=f"{SET_BASE_BUY_SLIPPAGE}_{base_slippage}",
            ),
            InlineKeyboardButton(
                f"🔵 {slippage}% Slippage" if slippage != base_slippage else "X % Slippage ✏️",
                callback_data=str(SET_CUSTOM_BUY_SLIPPAGE),
            ),
        ],
        [InlineKeyboardButton("BUY", callback_data=str(EXECUTE_BUY))],
    ]

    return InlineKeyboardMarkup(keyboard)


async def buy_token(update: Update, context: ContextTypes.DEFAULT_TYPE, new=False, token_mint=None) -> int:
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    # Handle callback query or text message
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        token_mint = token_mint or query.data.split("_")[1]
        message_func = query.message.reply_text if new else query.edit_message_text
    else:
        query = None
        assert token_mint, "Token mint must be provided for text message handling"
        message_func = update.message.reply_text

    # Get or create token and options
    token = await token_db.add_token(token_mint)
    options_key = f"buy_message_options_{token_mint}"
    if new:
        options = {
            "amount": user.settings.buy_amounts[0],
            "slippage": user.settings.buy_slippage,
            "symbol": token["symbol"],
        }
        context.user_data[options_key] = options
    else:
        options = context.user_data[options_key]
    # Generate text and keyboard
    text = await generate_token_buy_text(user, token, options)
    reply_markup = await generate_token_buy_keyboard(user, token, options)

    # Send or edit message
    message = await message_func(
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=not user.settings.chart_previews,
    )

    # Update context
    context.user_data[message.message_id] = token_mint
    context.user_data[f"buy_message_{token_mint}"] = message.message_id

    return BUY_TOKEN


async def generate_token_buy_text(user, token, options):
    t_symbol = token["symbol"].upper().replace("$", "")
    t_mint, t_name = token["mint"], token["name"]

    bd_progress = get_bonding_curve_progress(token)
    bonding_curve_text = generate_bonding_curve_text(bd_progress, t_mint) if bd_progress is not None else ""
    token_link = (
        f"https://pump.fun/{t_mint}"
        if token["is_pump_fun"] and not token["pump_fun_data"]["bonding_curve_complete"]
        else f"https://dexscreener.com/solana/{t_mint}?maker={user.wallet_public}"
    )
    sol_amount_dollar_value = options["amount"] * sol_dollar_value()
    price_impact_estimate = sol_amount_dollar_value / token["market_cap_dollars"]
    token_out_dollar_value_estimate = sol_amount_dollar_value * max(1 - price_impact_estimate, 0.05)
    token_out_estimate = token_out_dollar_value_estimate / token["price_dollars"]

    enough_funds = (
        options["amount"]
        + options["amount"] / 100 * (0.9 if user.referrer else 1)
        + user.settings.priority_fee
    ) < user.sol_in_wallet

    nfpf = nice_float_price_format
    top_line = f"{nfpf(options['amount'])} SOL | ${nfpf(sol_amount_dollar_value, underline=True)}"
    bottom_line = (
        f"{nfpf(token_out_estimate)} {t_symbol} | ${nfpf(token_out_dollar_value_estimate, underline=True)}"
    )
    transaction_display = center_arrow(top_line, bottom_line)
    return (
        f"<b>Buy ${t_symbol} - ({t_name})</b>"
        f'<a href="{token_link}"> 📈</a>\n'
        f"<code>{t_mint}</code>\n\n"
        f"Balance: <b>{nfpf(user.sol_in_wallet)} SOL</b>\n"
        f"Price: <b>${nfpf(token['price_dollars'], underline=True)}</b> | "
        f"MC: <b>${nfpf(token['market_cap_dollars'])}</b>\n"
        f"{' | '.join([f"{delta}: <b>{change:.1f}%</b>" for delta, change in token['price_change'].items()])}\n"
        f"Renounced: {'❌' if token['is_mutable'] else '✅'}\n\n"
        f"{bonding_curve_text}"
        # f"<u>Transaction</u>:\n"
        f"{"❗ <b>Not Enough Funds</b> ❗\n" if not enough_funds else ""}"
        f"<pre>{transaction_display}</pre>\n"
        f"Price Impact: <b>{price_impact_estimate*100:.2f}%</b>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )


def get_bonding_curve_progress(token):
    if token["is_pump_fun"]:
        if token["pump_fun_data"]["bonding_curve_complete"]:
            return 100
        return token["pump_fun_data"]["bonding_curve_progress"] * 100
    return None


def generate_bonding_curve_text(bd_progress, t_mint):
    return (
        f'<a href="pump.fun/{t_mint}">💊</a> Bonding Curve Progress: '
        f"<b>{bd_progress:.2f}%</b>\n{progress_bar(bd_progress, 23)}\n\n"
    )


def progress_bar(progress, length):
    if progress < 0:
        progress = 0
    elif progress > 100:
        progress = 100
    characters = ["░", "▓"]  # ['⣀', '⣇', '⣧', '⣷', '⣿']
    full_blocks = int(progress / 100 * length)
    remainder = (progress / 100 * length) - full_blocks
    partial_block_index = int(
        remainder * len(characters)
    )  # remainder ranges from 0 to 1, scaled to 0-4 for index
    partial_block = characters[partial_block_index]
    bar = characters[-1] * full_blocks
    if full_blocks < length:
        bar += partial_block
        bar += characters[0] * (length - full_blocks - 1)
    return bar


async def buy_token_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await buy_token(update, context, new=True)


async def refresh_token_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await token_db.update_price(update.callback_query.data.split("_")[1])
    return await buy_token(update, context)


async def close_token_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.delete_message(query.message.chat_id, query.message.message_id)
    return BUY_TOKEN


async def set_option(update: Update, context: ContextTypes.DEFAULT_TYPE, option: str) -> int:
    query = update.callback_query
    data = query.data.split("_")
    token_mint = context.user_data[query.message.message_id]
    amount = float(data[1])
    # float if amount of sol, else percentage => int
    context.user_data[f"buy_message_options_{token_mint}"][option] = (
        amount if option == "amount" else int(amount)
    )
    return await buy_token(update, context, token_mint=token_mint)


async def set_amount_to_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await set_option(update, context, "amount")


async def set_base_buy_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await set_option(update, context, "slippage")


async def prompt_custom_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, option: str, prompt: str, next_state: int
) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text=prompt)
    context.user_data[f"custom_{option}_message_id"] = message.message_id
    context.user_data["last_mint"] = context.user_data[query.message.message_id]
    return next_state


async def set_custom_buy_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input(
        update, context, "slippage", "Please enter your custom slippage %:", CUSTOM_BUY_SLIPPAGE
    )


async def set_custom_amount_to_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input(
        update, context, "amount", "Please enter your custom buy amount in SOL:", CUSTOM_BUY_AMOUNT
    )


async def process_custom_input(update: Update, context: ContextTypes.DEFAULT_TYPE, option: str) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        amount = float(update.message.text.replace("%", ""))
        if option == "slippage":
            amount = int(max(1, amount))
        else:
            amount = max(0.002, amount)

        token_mint = context.user_data["last_mint"]
        user = user_db.get_user(user_id)
        token = await token_db.get_token(token_mint)

        context.user_data[f"buy_message_options_{token_mint}"][option] = amount
        options = context.user_data[f"buy_message_options_{token_mint}"]
        reply_markup = await generate_token_buy_keyboard(user, token, options)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get(f"custom_{option}_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get(f"buy_message_{token_mint}"),
            await generate_token_buy_text(user, token, options),
            reply_markup,
        )
        return BUY_TOKEN
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await update.message.reply_text("Invalid input. Please enter a valid number.")
        return CUSTOM_BUY_SLIPPAGE if option == "slippage" else CUSTOM_BUY_AMOUNT


async def custom_buy_slippage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input(update, context, "slippage")


async def custom_buy_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input(update, context, "amount")


async def execute_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    mint = context.user_data[query.message.message_id]
    options = context.user_data[f"buy_message_options_{mint}"]
    context.user_data["last_mint"] = context.user_data[query.message.message_id]

    if user.settings.confirm_trades:
        # Ask for confirmation
        confirmation_text = (
            f"🔔 <b>Confirm Your Purchase</b>\n\n"
            f"You are about to buy:\n"
            f"Amount: <b>{options['amount']} SOL</b>\n"
            f"Token: <b>{options['symbol']}</b>\n"
            f"Slippage: <b>{options['slippage']}%</b>\n\n"
            f"Do you want to proceed?"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=str(CONFIRM_BUY)),
                InlineKeyboardButton("❌ Cancel", callback_data=str(CANCEL_BUY)),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(text=confirmation_text, reply_markup=reply_markup, parse_mode="HTML")
        return CONFIRM_BUY

    # If no confirmation is needed, proceed with the purchase
    return await process_buy(update, context, delete=False)


async def execute_buy_order(
    context: ContextTypes.DEFAULT_TYPE, user, mint: str, amount: float, slippage: int, message=None
):
    transact = Transact(user.wallet_secret, fee_sol=user.settings.priority_fee)
    quote = await transact.buy(mint, amount, slippage_bps=int(slippage * 100))
    #txs = "66T2yoUUf1QXeYbyULmyZHb8c4EpX9EqU7ukMMzNsFXhN3c6MLD1QA5B3Sh6XjYh4jX99FYG4PvKhGikpsyi2DML"
    txs = await transact.construct_and_send(
        quote, fee=(0.9 if user.referrer else 1) * amount * 0.01  # add transaction fee
    )
    text = (
        (
            f"🚀 <b>Buy order for {amount} SOL sent!</b>\n\n"
            f'Transaction details: <a href="https://solscan.io/tx/{txs}">View on Solscan</a>\n'
            f"Slippage: <b>{slippage}%</b>\n\n"
            f"⏳ <i>Waiting for Tx Confirmation...</i>"
        )
        if txs
        else (
            f"😅 <b>Oops! Failed fetching or sending transaction!</b>\n"
            "Contact support if you believe this to be an issue."
        )
    )
    if message:
        await message.edit_text(text=text, parse_mode="HTML", disable_web_page_preview=True)
    else:
        message = await context.bot.send_message(
            chat_id=user.id, text=text, parse_mode="HTML", disable_web_page_preview=True
        )
    if txs:
        await transaction_db.insert_transaction(user.id, user.wallet_public, message.message_id, str(txs), mint, int(time.time()))
#        await schedule_tx_update(
#            context, message.chat_id, message.message_id, user.id, txs, mint, user.wallet_public
#        )
    return txs


async def process_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, delete=True) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    mint = context.user_data["last_mint"]
    options = context.user_data[f"buy_message_options_{mint}"]

    message = await query.message.reply_text(
        text="🔍 Hunting for the perfect quote... Hang tight!",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    if delete:
        await query.message.delete()

    await execute_buy_order(context, user, mint, options["amount"], options["slippage"], message)

    return BUY_TOKEN


async def confirm_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Process the buy
    return await process_buy(update, context)


async def cancel_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        text="❌ Buy operation cancelled.",
    )
    await query.message.delete()
    return BUY_TOKEN


async def is_token_mint(text):
    try:
        pubkey = Pubkey.from_string(text)
    except Exception as e:
        return False
    # Pubkey is valid
    if await token_db.get_token(text):
        return True
    # Token is not in token_db, check RPC if pubkey is a token mint
    try:
        account_info = await aclient.get_account_info(pubkey, commitment="confirmed")
        if account_info.value is None:
            return False
        if account_info.value.owner == TOKEN_PROGRAM_ID:
            return True
    except Exception as e:
        # No reason to handle specific exceptions
        pass
    return False


async def handle_potential_mint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().split("/")[-1]
    await delete_messages(context, update.effective_chat.id, update.message.message_id)

    if await is_token_mint(text):
        user_id = update.effective_user.id
        user = user_db.get_user(user_id)

        if user.settings.autobuy:
            # Execute autobuy
            amount = user.settings.autobuy_amount
            slippage = user.settings.autobuy_slippage
            message = await update.message.reply_text(
                text="🔍 Autobuy enabled. Hunting for the perfect quote...",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await execute_buy_order(context, user, text, amount, slippage, message)
        # Open buy menu regardless of autobuy
        return await buy_token(update, context, new=True, token_mint=text)
    else:
        await update.message.reply_text(
            text="⚠️ <b>SPL Token Not Found</b> ⚠️\n\n Please check the mint address and try again. 💡",
            parse_mode="HTML",
        )


token_buy_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(buy_token_new, pattern="^" + str(BUY_TOKEN_NEW) + "_"),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_potential_mint),
    ],
    states={
        BUY_TOKEN: [
            CallbackQueryHandler(buy_token, pattern="^" + str(BUY_TOKEN) + "_"),
            CallbackQueryHandler(buy_token_new, pattern="^" + str(BUY_TOKEN_NEW) + "_"),
            CallbackQueryHandler(refresh_token_buy, pattern="^" + str(REFRESH_TOKEN_BUY) + "_"),
            CallbackQueryHandler(close_token_buy, pattern="^" + str(CLOSE_TOKEN_BUY)),
            CallbackQueryHandler(set_amount_to_buy, pattern="^" + str(AMOUNT_TO_BUY) + "_"),
            CallbackQueryHandler(set_custom_amount_to_buy, pattern="^" + str(AMOUNT_TO_BUY_CUSTOM) + "$"),
            CallbackQueryHandler(set_custom_buy_slippage, pattern="^" + str(SET_CUSTOM_BUY_SLIPPAGE) + "$"),
            CallbackQueryHandler(set_base_buy_slippage, pattern="^" + str(SET_BASE_BUY_SLIPPAGE) + "_"),
            CallbackQueryHandler(execute_buy, pattern="^" + str(EXECUTE_BUY) + "$"),
        ],
        CUSTOM_BUY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_buy_amount_input)],
        CUSTOM_BUY_SLIPPAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_buy_slippage_input)],
        CONFIRM_BUY: [
            CallbackQueryHandler(confirm_buy, pattern="^" + str(CONFIRM_BUY) + "$"),
            CallbackQueryHandler(cancel_buy, pattern="^" + str(CANCEL_BUY) + "$"),
        ],
    },
    fallbacks=[
        # CommandHandler("start", start),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_potential_mint),
        CommandHandler(str(BUY_TOKEN), buy_token),
    ],
)
