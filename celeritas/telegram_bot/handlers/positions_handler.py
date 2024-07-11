from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import Update
from telegram.ext import CallbackQueryHandler
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes
from telegram.ext import ConversationHandler
from telegram.helpers import create_deep_linked_url

from celeritas.db import token_db
from celeritas.db import user_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import utc_time_now

"""
shows all current positions, the amount of tokens, their value in sol/usd
allows the user to sell each one
sell_menu, SELL_MENU - returns a new maneu
"""

TOKENS_PER_PAGE = 3


async def sell_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    if query:
        await query.answer()
    user_db.get_attribute(user_id, "wallet_public")
    token = context.args[0].split("_")[1]
    print("can create sell menu for", token, user_id)
    # TODO: implement individual token sell menu


def generate_token_link(bot_username: str, token: str, page: int) -> str:
    return create_deep_linked_url(bot_username, payload=f"{SELL}_{token}")


async def generate_sell_keyboard(user, page):
    keyboard = [
        [
            InlineKeyboardButton(
                "-- Select a token to sell --",
                callback_data="none",
            )
        ],
    ]
    tokens_by_amount = [t[0] for t in sorted(user.holdings.items(), key=lambda x: -x[1])]
    start = page * TOKENS_PER_PAGE
    end = start + TOKENS_PER_PAGE
    tokens = token_db.get_tokens(tokens_by_amount[start:end])
    for i in range(start, min(end, len(tokens_by_amount)), 3):
        keyboard.append(
            [
                InlineKeyboardButton(
                    tokens[token]["symbol"],
                    callback_data=str(SELL) + "_" + tokens[token]["mint"],
                )
                for token in tokens_by_amount[i : i + min(3, TOKENS_PER_PAGE)]
            ]
        )

    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton("Previous", callback_data=str(PREV_PAGE)))
    if end < len(tokens_by_amount):
        navigation_buttons.append(InlineKeyboardButton("Next", callback_data=str(NEXT_PAGE)))

    if navigation_buttons:
        keyboard.append(navigation_buttons)

    keyboard.append(
        [
            InlineKeyboardButton("Close", callback_data=str(CLOSE_SELL_MENU)),
            InlineKeyboardButton("↻ Refresh", callback_data=str(REFRESH_SELL_MENU)),
        ],
    )
    return InlineKeyboardMarkup(keyboard)


async def sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, new=False) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    if query:
        await query.answer()
    user = user_db.get_user(user_id)
    reply_markup = await generate_sell_keyboard(user, page)
    tokens_by_amount = [t[0] for t in sorted(user.holdings.items(), key=lambda x: -x[1])]
    start = page * TOKENS_PER_PAGE
    end = start + TOKENS_PER_PAGE
    tokens = token_db.get_tokens(tokens_by_amount[start:end])
    token_texts = [
        (
            f'<a href="https://dexscreener.com/solana/{t}?maker={user.wallet_public}">📈</a> '
            f'<a href="{generate_token_link(context.bot.username, t, page)}">{tokens[t]['symbol']}</a>: '
            f"{round(user.holdings[t]*tokens[t]['price_dollars']/sol_dollar_value(), 4)} SOL "
            f"(${round(user.holdings[t]*tokens[t]['price_dollars'], 3)})"
        )
        for t in tokens_by_amount[start:end]
    ]
    text = (
        f"<b>Select a token to sell</b>\n"
        f"Balance: <code>{user.sol_in_wallet} SOL</code>\n\n"
        f"{'\n'.join(token_texts)}\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )
    if new:
        message = await query.message.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    # is an update from an inline button
    elif query:
        message = await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    # is an update from a start link
    else:
        message = await context.bot.edit_message_text(
            chat_id=update.message.chat_id,
            message_id=context.user_data["sell_menu_message_id"],
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    context.user_data["sell_menu_message_id"] = message.message_id
    context.user_data["sell_menu_page"] = page

    return SELL_MENU


async def refresh_sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_db.update_user_holdings(user_id)
    user = user_db.get_user(user_id)
    page = context.user_data.get("sell_menu_page", 0)
    tokens_by_amount = [t[0] for t in sorted(user.holdings.items(), key=lambda x: -x[1])]
    start = page * TOKENS_PER_PAGE
    end = start + TOKENS_PER_PAGE
    token_db.update_price(tokens_by_amount[start:end])
    return await sell_menu(update, context, page=page)


async def sell_menu_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await sell_menu(update, context, page=0, new=True)


async def close_sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Delete the sell menu message
    chat_id = query.message.chat_id
    sell_menu_message_id = context.user_data.get("sell_menu_message_id")
    if sell_menu_message_id:
        await context.bot.delete_message(chat_id, sell_menu_message_id)
    return ConversationHandler.END


async def sell_menu_page(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: int) -> int:
    page = context.user_data.get("sell_menu_page", 0) + direction
    return await sell_menu(update, context, page=page, new=False)


positions_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(sell_menu_new, pattern="^" + str(SELL_MENU) + "$")],
    states={
        SELL_MENU: [
            CallbackQueryHandler(refresh_sell_menu, pattern="^" + str(REFRESH_SELL_MENU) + "$"),
            CallbackQueryHandler(close_sell_menu, pattern="^" + str(CLOSE_SELL_MENU) + "$"),
            CallbackQueryHandler(
                lambda update, context: sell_menu_page(update, context, direction=1),
                pattern="^" + str(NEXT_PAGE) + "$",
            ),
            CallbackQueryHandler(
                lambda update, context: sell_menu_page(update, context, direction=-1),
                pattern="^" + str(PREV_PAGE) + "$",
            ),
            CallbackQueryHandler(sell_token, pattern="^" + str(SELL) + "_"),
        ]
    },
    fallbacks=[CommandHandler(str(SELL), sell_menu)],
)
