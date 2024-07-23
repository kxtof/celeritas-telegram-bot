import logging

from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import Update
from telegram.ext import CallbackQueryHandler
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes
from telegram.ext import ConversationHandler

from celeritas.db import token_db
from celeritas.db import user_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.handlers.sell_handler import token_sell_conv_handler
from celeritas.telegram_bot.handlers.withdraw_handler import withdraw_conv_handler
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import utc_time_now

logger = logging.getLogger(__name__)


TOKENS_PER_PAGE = 9


async def generate_menu_keyboard(user, tokens, page, last, action_type) -> InlineKeyboardMarkup:
    def create_token_button(token) -> InlineKeyboardButton:
        if action_type == "sell":
            callback_data = f"{SELL_TOKEN_NEW}_{token['mint']}"
        else:  # withdraw
            callback_data = f"{WITHDRAW_NEW}_{token['mint']}"
        return InlineKeyboardButton(token["symbol"], callback_data=callback_data)

    token_buttons = [create_token_button(token) for token in tokens.values()]
    keyboard = [token_buttons[i : i + 3] for i in range(0, len(token_buttons), 3)]

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("Previous", callback_data=str(PREV_PAGE)))
    if not last:
        nav_buttons.append(InlineKeyboardButton("Next", callback_data=str(NEXT_PAGE)))
    if nav_buttons:
        keyboard.append(nav_buttons)

    close_callback = CLOSE_SELL_MENU if action_type == "sell" else CLOSE_WITHDRAW_MENU
    refresh_callback = REFRESH_SELL_MENU if action_type == "sell" else REFRESH_WITHDRAW_MENU
    keyboard.append(
        [
            InlineKeyboardButton("❌ Close", callback_data=str(close_callback)),
            InlineKeyboardButton("🔄 Refresh", callback_data=str(refresh_callback)),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


async def get_paginated_tokens(user, page, tokens_per_page, action_type):
    token_prices = await token_db.get_prices(list(user.holdings.keys()))
    # Sort based on dollar value of holding
    tokens_by_value = [
        (t, h*token_prices.get(t, 0))
        for t, h in sorted(user.holdings.items(), key=lambda x: -x[1] * token_prices.get(x[0], 0))
    ]
    if action_type == "withdraw":
        tokens_by_value.insert(0, "SOL")
    start, end = page * tokens_per_page, (page + 1) * tokens_per_page
    # filter out tokens with insufficient holding value
    return [t for t, h in tokens_by_value if h > user.settings.min_pos_value][start:end], len(tokens_by_value) <= end


async def generate_token_text(user, token, token_info, sol=True):
    token_url = (
        f"https://pump.fun/{token}"
        if token_info["is_pump_fun"] and not token_info["pump_fun_data"]["bonding_curve_complete"]
        else f"https://dexscreener.com/solana/{token}?maker={user.wallet_public}"
    )
    is_not_sol = token_info["mint"] != "SOL"
    return (
        f'<a href="{token_url}">🅲</a> '
        f'${token_info["symbol"].upper().replace("$", "")}: '
        f"<code>"
        f"{nfpf(user.holdings[token] if is_not_sol else user.sol_in_wallet)} "
        f"(${nfpf(user.holdings[token]*token_info['price_dollars'] if is_not_sol else user.sol_in_wallet*token_info['price_dollars'])})"
        f"</code>"
    )


async def get_tokens(tokens):
    sol = (
        {"mint": "SOL", "symbol": "SOL", "price_dollars": sol_dollar_value(), "is_pump_fun": False}
        if "SOL" in tokens
        else None
    )
    tokens_info = {"SOL": sol} if sol else {}
    tokens_info.update(await token_db.get_tokens([token for token in tokens if token != "SOL"]))
    return tokens_info


async def token_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, new=False, action_type="sell"
) -> int:
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)
    query = update.callback_query
    if query: await query.answer()

    tokens, last = await get_paginated_tokens(user, page, TOKENS_PER_PAGE, action_type)
    tokens_info = await get_tokens(tokens)
    reply_markup = await generate_menu_keyboard(
        user, tokens_info, page, last=last, action_type=action_type
    )
    token_texts = [await generate_token_text(user, t, tokens_info[t]) for t in tokens]

    action_text = "sell" if action_type == "sell" else "withdraw"
    balance = (
        f"Balance: <code>{nfpf(user.sol_in_wallet)} SOL (${nfpf(user.sol_in_wallet*sol_dollar_value())})</code>\n\n"
        if action_type == "sell"
        else "\n"
    )
    text = (
        f"<b>To {action_text} a certain token, click on the corresponding button.</b>\n"
        f"{balance}"
        f"{'\n'.join(token_texts) if len(token_texts) else f"<i>You don't have any tokens to {action_text} yet. You can refresh your balance or buy some tokens by clicking the '🔄 Refresh' button.</i>"}\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )
    message_func = (query.message.reply_text if query else update.message.reply_text) if new else query.edit_message_text
    message = await message_func(
        text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    context.user_data.update({f"{action_type}_menu_page": page})
    return SELL_MENU if action_type == "sell" else WITHDRAW_MENU


async def sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, new=False) -> int:
    return await token_menu(update, context, page=page, new=new, action_type="sell")


async def withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, new=False) -> int:
    return await token_menu(update, context, page=page, new=new, action_type="withdraw")


async def sell_menu_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await sell_menu(update, context, new=True)


async def withdraw_menu_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await withdraw_menu(update, context, new=True)


async def refresh_sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_db.update_user_holdings(user_id)
    user = user_db.get_user(user_id)
    page = context.user_data.get("sell_menu_page", 0)
        
    token_prices = await token_db.get_prices(list(user.holdings.keys()))
    # Sort based on dollar value of holding
    tokens_by_value = [
        t for t, h in sorted(user.holdings.items(), key=lambda x: -x[1] * token_prices.get(x[0], 0))
    ]
    #tokens_by_amount = [t[0] for t in sorted(user.holdings.items(), key=lambda x: -x[1])]
    
    start = page * TOKENS_PER_PAGE
    end = start + TOKENS_PER_PAGE
    await token_db.update_price(tokens_by_value[start:end])
    return await sell_menu(update, context, page=page, new=False)


async def refresh_withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_db.update_user_holdings(user_id)
    user = user_db.get_user(user_id)
    page = context.user_data.get("withdraw_menu_page", 0)
    tokens_by_amount = [t[0] for t in sorted(user.holdings.items(), key=lambda x: -x[1])]
    start = page * TOKENS_PER_PAGE
    end = start + TOKENS_PER_PAGE
    await token_db.update_price(tokens_by_amount[start:end])
    return await withdraw_menu(update, context, page=page, new=False)


async def close_sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Delete the settings message
    chat_id = query.message.chat_id
    await context.bot.delete_message(chat_id, query.message.message_id)
    return SELL_MENU


async def sell_menu_page(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: int) -> int:
    page = context.user_data.get("sell_menu_page", 0) + direction
    return await sell_menu(update, context, page=page, new=False)


async def close_withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    await context.bot.delete_message(chat_id, query.message.message_id)
    return WITHDRAW_MENU


async def withdraw_menu_page(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: int) -> int:
    page = context.user_data.get("withdraw_menu_page", 0) + direction
    return await withdraw_menu(update, context, page=page, new=False)


sell_menu_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(sell_menu_new, pattern="^" + str(NEW_SELL_MENU) + "$"),
        CommandHandler("sell", sell_menu_new),
    ],
    states={
        SELL_MENU: [
            CallbackQueryHandler(sell_menu, pattern="^" + str(SELL_MENU) + "$"),
            CallbackQueryHandler(sell_menu_new, pattern="^" + str(NEW_SELL_MENU) + "$"),
            CallbackQueryHandler(close_sell_menu, pattern="^" + str(CLOSE_SELL_MENU) + "$"),
            CallbackQueryHandler(refresh_sell_menu, pattern="^" + str(REFRESH_SELL_MENU) + "$"),
            CallbackQueryHandler(
                lambda update, context: sell_menu_page(update, context, direction=1),
                pattern="^" + str(NEXT_PAGE) + "$",
            ),
            CallbackQueryHandler(
                lambda update, context: sell_menu_page(update, context, direction=-1),
                pattern="^" + str(PREV_PAGE) + "$",
            ),
            token_sell_conv_handler,
        ],
    },
    fallbacks=[CommandHandler("sell", sell_menu_new)],
)

withdraw_menu_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(withdraw_menu_new, pattern="^" + str(NEW_WITHDRAW_MENU) + "$"),
        CommandHandler("withdraw", withdraw_menu_new),
    ],
    states={
        WITHDRAW_MENU: [
            CallbackQueryHandler(withdraw_menu, pattern="^" + str(WITHDRAW_MENU) + "$"),
            CallbackQueryHandler(withdraw_menu_new, pattern="^" + str(NEW_WITHDRAW_MENU) + "$"),
            CallbackQueryHandler(close_withdraw_menu, pattern="^" + str(CLOSE_WITHDRAW_MENU) + "$"),
            CallbackQueryHandler(refresh_withdraw_menu, pattern="^" + str(REFRESH_WITHDRAW_MENU) + "$"),
            CallbackQueryHandler(
                lambda update, context: withdraw_menu_page(update, context, direction=1),
                pattern="^" + str(NEXT_PAGE) + "$",
            ),
            CallbackQueryHandler(
                lambda update, context: withdraw_menu_page(update, context, direction=-1),
                pattern="^" + str(PREV_PAGE) + "$",
            ),
            withdraw_conv_handler,
        ],
    },
    fallbacks=[CommandHandler("withdraw", withdraw_menu_new)],
)
