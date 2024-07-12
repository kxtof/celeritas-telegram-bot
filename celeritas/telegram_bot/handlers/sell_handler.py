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

from celeritas.db import token_db
from celeritas.db import user_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.fetch_tx_update_msg import schedule_tx_update
from celeritas.telegram_bot.utils import center_arrow
from celeritas.telegram_bot.utils import delete_messages
from celeritas.telegram_bot.utils import edit_message
from celeritas.telegram_bot.utils import nice_float_price_format
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import utc_time_now
from celeritas.transact import Transact

logger = logging.getLogger(__name__)


async def generate_token_sell_keyboard(user, token, options) -> InlineKeyboardMarkup:
    sell_amounts = user.settings.sell_amounts
    percentage_to_sell = options["percentage_to_sell"]
    slippage = options["slippage"]
    base_slippage = user.settings.sell_slippage

    keyboard = [
        [
            InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_TOKEN_SELL)),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"{REFRESH_TOKEN_SELL}_{token['mint']}"),
        ],
        # [InlineKeyboardButton("--- Percentage to sell ---", callback_data="none")],
        [
            InlineKeyboardButton(
                f"{'🔵 ' if p == percentage_to_sell else ''}{p}%",
                callback_data=f"{AMOUNT_TO_SELL}_{p}",
            )
            for p in sell_amounts
        ],
        [
            InlineKeyboardButton(
                (f"🔵 {percentage_to_sell}%" if percentage_to_sell not in sell_amounts else "Custom % ✏️"),
                callback_data=str(AMOUNT_TO_SELL_CUSTOM),
            )
        ],
        # [InlineKeyboardButton("--- Slippage to use ---", callback_data="none")],
        [
            InlineKeyboardButton(
                f"{'🔵 ' if base_slippage == slippage else ''}{base_slippage}%",
                callback_data=f"{SET_BASE_SLIPPAGE}_{base_slippage}",
            ),
            InlineKeyboardButton(
                f"🔵 {slippage}%" if slippage != base_slippage else "Custom % ✏️",
                callback_data=str(SET_CUSTOM_SLIPPAGE),
            ),
        ],
        [InlineKeyboardButton("SELL", callback_data=str(EXECUTE_SELL))],
    ]

    return InlineKeyboardMarkup(keyboard)


async def sell_token(update: Update, context: ContextTypes.DEFAULT_TYPE, new=False, token_mint=None) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()

    user = user_db.get_user(user_id)
    if not token_mint:
        token_mint = query.data.split("_")[1]
    token = await token_db.add_token(token_mint)

    # The user shouldn't get here if there are no tokens of this kind in his wallet.
    position = user.positions.get(
        token_mint,
        {
            "balance": 0,
            "avg_entry": None,
            "n_buys": None,
            "n_sells": None,
            "pnl_usd": None,
            "pnl_sol": None,
        },
    )

    if new:
        options = {
            "percentage_to_sell": user.settings.sell_amounts[0],
            "slippage": user.settings.sell_slippage,
            "symbol": token["symbol"],
        }
        context.user_data[f"sell_message_options_{token_mint}"] = options
    else:
        options = context.user_data.get(
            f"sell_message_options_{token_mint}",
            {
                "percentage_to_sell": user.settings.sell_amounts[0],
                "slippage": user.settings.sell_slippage,
                "symbol": token["symbol"],
            },
        )

    text = await generate_token_sell_text(user, token, position, options)
    reply_markup = await generate_token_sell_keyboard(user, token, options)
    message_func = query.message.reply_text if new else query.edit_message_text
    message = await message_func(
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=not user.settings.chart_previews,
    )
    context.user_data[message.message_id] = token_mint
    context.user_data[f"sell_message_{token_mint}"] = message.message_id

    return SELL_TOKEN


async def generate_token_sell_text(user, token, position, options):
    t_symbol = token["symbol"].upper().replace("$", "")
    t_mint, t_name = token["mint"], token["name"]

    bd_progress = get_bonding_curve_progress(token)
    bonding_curve_text = generate_bonding_curve_text(bd_progress, t_mint) if bd_progress is not None else ""
    token_link = (
        f"https://pump.fun/{t_mint}"
        if token["is_pump_fun"] and not token["pump_fun_data"]["bonding_curve_complete"]
        else f"https://dexscreener.com/solana/{t_mint}?maker={user.wallet_public}"
    )

    tokens_to_sell = options["percentage_to_sell"] / 100 * position["balance"]
    tokens_to_sell_dollar_value = tokens_to_sell * token["price_dollars"]
    price_impact_estimate = tokens_to_sell_dollar_value / token["market_cap_dollars"]
    sol_out_dollar_value_estimate = tokens_to_sell_dollar_value * max(1 - price_impact_estimate, 0.05)
    sol_out_estimate = sol_out_dollar_value_estimate / sol_dollar_value()

    nfpf = nice_float_price_format
    top_line = f"{nfpf(tokens_to_sell)} {t_symbol} | ${nfpf(tokens_to_sell_dollar_value, underline=True)}"
    bottom_line = f"{nfpf(sol_out_estimate)} SOL | ${nfpf(sol_out_dollar_value_estimate, underline=True)}"
    transaction_display = center_arrow(top_line, bottom_line)

    avg_entry_usd = position.get("avg_entry_usd", None)
    unrealized_pnl_usd = position.get("unrealized_pnl_usd", 0)
    unrealized_pnl_sol = position.get("unrealized_pnl_sol", 0)
    unrealized_pnl_percentage_usd = position.get("unrealized_pnl_percentage_usd", 0)
    unrealized_pnl_percentage_sol = position.get("unrealized_pnl_percentage_sol", 0)
    return (
        f"<b>Sell ${t_symbol} - ({t_name})</b>"
        f'<a href="{token_link}"> 📈</a>\n'
        f"<code>{t_mint}</code>\n\n"
        f"Balance: <b>{nfpf(user.holdings.get(t_mint, 0))} {t_symbol}</b> ✅\n"
        f"Price: <b>${nfpf(token['price_dollars'], underline=True)}</b>\n"
        f"MkCap: <b>${nfpf(token['market_cap_dollars'])}</b>\n"
        f"{' | '.join([f"{delta}: <b>{change:.1f}%</b>" for delta, change in token['price_change'].items()])}\n"
        f"Renounced: {'❌' if token['is_mutable'] else '✅'}\n\n"
        f"{bonding_curve_text}"
        f"Avg Entry Price: <code>{f"${nfpf(avg_entry_usd)}" if avg_entry_usd else 'N/A'}</code>\n"
        f"PNL USD: <code>{(lambda pnl, pnl_p: f'{pnl_p:.1f}% (${nfpf(pnl)})' if pnl else 'N/A')(unrealized_pnl_usd, unrealized_pnl_percentage_usd)}</code> {'🟩' if unrealized_pnl_usd is None or unrealized_pnl_usd >= 0 else '🟥'}\n"
        f"PNL SOL: <code>{(lambda pnl, pnl_p: f'{pnl_p:.1f}% ({nfpf(pnl)} SOL)' if pnl else 'N/A')(unrealized_pnl_sol, unrealized_pnl_percentage_sol)}</code> {'🟩' if unrealized_pnl_sol is None or unrealized_pnl_sol >= 0 else '🟥'}\n\n"
        # f"<u>Transaction</u>:\n"
        f"<pre>{transaction_display}</pre>\n\n"
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


async def sell_token_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_db.update_user_positions(update.effective_user.id, get_prices=True)
    return await sell_token(update, context, new=True)


async def refresh_token_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await token_db.update_price(update.callback_query.data.split("_")[1])
    user_db.update_user_positions(update.effective_user.id, update_holdings=True, get_prices=True)
    return await sell_token(update, context, new=False)


async def close_token_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.delete_message(query.message.chat_id, query.message.message_id)
    return SELL_TOKEN


async def set_option(update: Update, context: ContextTypes.DEFAULT_TYPE, option: str) -> int:
    query = update.callback_query
    data = query.data.split("_")
    mint = context.user_data[query.message.message_id]
    percentage = int(data[1])
    context.user_data[f"sell_message_options_{mint}"][option] = percentage
    return await sell_token(update, context, new=False, token_mint=mint)


async def set_amount_to_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await set_option(update, context, "percentage_to_sell")


async def set_base_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def set_custom_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input(
        update, context, "slippage", "Please enter your custom slippage %:", CUSTOM_SLIPPAGE
    )


async def set_custom_amount_to_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input(
        update, context, "percentage_to_sell", "Please enter your custom %:", CUSTOM_PERCENTAGE
    )


async def process_custom_input(update: Update, context: ContextTypes.DEFAULT_TYPE, option: str) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        percentage = float(update.message.text.replace("%", ""))
        percentage = int(max(1, percentage))
        if option != "slippage":
            percentage = min(100, percentage)

        token_mint = context.user_data["last_mint"]
        user = user_db.get_user(user_id)
        position = user.positions[token_mint]
        token = await token_db.get_token(token_mint)

        context.user_data[f"sell_message_options_{token_mint}"][option] = percentage
        options = context.user_data[f"sell_message_options_{token_mint}"]
        reply_markup = await generate_token_sell_keyboard(user, token, options)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get(f"custom_{option}_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get(f"sell_message_{token_mint}"),
            await generate_token_sell_text(user, token, position, options),
            reply_markup,
        )
        return SELL_TOKEN
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get(f"custom_{option}_message_id"),
            text="Invalid input. Please enter a valid percentage.",
        )
        return CUSTOM_SLIPPAGE if option == "slippage" else CUSTOM_PERCENTAGE


async def custom_slippage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input(update, context, "slippage")


async def custom_percentage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input(update, context, "percentage_to_sell")


async def execute_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    mint = context.user_data[query.message.message_id]
    options = context.user_data[f"sell_message_options_{mint}"]
    context.user_data["last_mint"] = mint

    # Return if user doesn't have any tokens.
    if mint not in user.holdings:
        text = (
            "<b>You don't have any tokens of this mint in your wallet!</b>\n"
            "Contact support if you think there is an issue on our side."
        )
        await query.message.reply_text(text=text, parse_mode="HTML", disable_web_page_preview=True)
        return SELL_TOKEN

    if user.settings.confirm_trades:
        # Ask for confirmation
        confirmation_text = (
            f"🔔 <b>Confirm Your Sale</b>\n\n"
            f"You are about to sell:\n"
            f"Amount: <b>{options['percentage_to_sell']}%</b> of your {options['symbol']} tokens\n"
            f"Slippage: <b>{options['slippage']}%</b>\n\n"
            f"Do you want to proceed?"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=str(CONFIRM_SELL)),
                InlineKeyboardButton("❌ Cancel", callback_data=str(CANCEL_SELL)),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(text=confirmation_text, reply_markup=reply_markup, parse_mode="HTML")
        return CONFIRM_SELL

    # If no confirmation is needed, proceed with the sale
    return await process_sell(update, context, delete=False)


async def process_sell(update: Update, context: ContextTypes.DEFAULT_TYPE, delete=True) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    mint = context.user_data["last_mint"]
    options = context.user_data[f"sell_message_options_{mint}"]

    message = await query.message.reply_text(
        text="🔍 Hunting for the perfect quote... Hang tight! 💼💨",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    if delete:
        await query.message.delete()

    transact = Transact(user.wallet_secret, fee_sol=user.settings.priority_fee)
    quote = await transact.sell_percentage(
        mint, options["percentage_to_sell"], slippage_bps=int(options["slippage"] * 100)
    )
    txs = await transact.construct_and_send(
        quote,
        fee=(0.9 if user.referrer else 1) * quote["quote"]["token_amount_out"] / 100,  # add transaction fee
    )
    text = (
        (
            f"🚀 <b>Sell order for {options['percentage_to_sell']}% sent!</b>\n\n"
            f'Transaction details: <a href="https://solscan.io/tx/{txs}">View on Solscan</a>\n'
            f"Slippage: <b>{options['slippage']}%</b>\n\n"
            f"<i>Waiting for Tx Confirmation...</i>"
        )
        if txs
        else (
            f"<b>Failed fetching or sending transaction!</b>\n"
            "Contact support if you believe this to be an issue."
        )
    )

    await message.edit_text(text=text, parse_mode="HTML", disable_web_page_preview=True)
    if txs:
        await schedule_tx_update(
            context, message.chat_id, message.message_id, user_id, txs, mint, user.wallet_public
        )

    return SELL_TOKEN


async def confirm_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Process the sell
    return await process_sell(update, context)


async def cancel_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        text="❌ Sell operation cancelled.",
    )
    await query.message.delete()
    return SELL_TOKEN


token_sell_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(sell_token_new, pattern="^" + str(SELL_TOKEN_NEW) + "_")],
    states={
        SELL_TOKEN: [
            CallbackQueryHandler(sell_token, pattern="^" + str(SELL_TOKEN) + "_"),
            CallbackQueryHandler(sell_token_new, pattern="^" + str(SELL_TOKEN_NEW) + "_"),
            CallbackQueryHandler(refresh_token_sell, pattern="^" + str(REFRESH_TOKEN_SELL) + "_"),
            CallbackQueryHandler(close_token_sell, pattern="^" + str(CLOSE_TOKEN_SELL)),
            CallbackQueryHandler(set_amount_to_sell, pattern="^" + str(AMOUNT_TO_SELL) + "_"),
            CallbackQueryHandler(set_custom_amount_to_sell, pattern="^" + str(AMOUNT_TO_SELL_CUSTOM) + "$"),
            CallbackQueryHandler(set_custom_slippage, pattern="^" + str(SET_CUSTOM_SLIPPAGE) + "$"),
            CallbackQueryHandler(set_base_slippage, pattern="^" + str(SET_BASE_SLIPPAGE) + "_"),
            CallbackQueryHandler(execute_sell, pattern="^" + str(EXECUTE_SELL) + "$"),
        ],
        CUSTOM_PERCENTAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_percentage_input)],
        CUSTOM_SLIPPAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_slippage_input)],
        CONFIRM_SELL: [
            CallbackQueryHandler(confirm_sell, pattern="^" + str(CONFIRM_SELL) + "$"),
            CallbackQueryHandler(cancel_sell, pattern="^" + str(CANCEL_SELL) + "$"),
        ],
    },
    fallbacks=[CommandHandler(str(SELL_TOKEN), sell_token)],
)
