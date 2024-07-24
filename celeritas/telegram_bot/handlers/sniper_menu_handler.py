import logging

from solders.pubkey import Pubkey
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
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import utc_time_now

logger = logging.getLogger(__name__)

WALLETS_PER_PAGE = 6


async def generate_menu_keyboard(user, wallets, page, last) -> InlineKeyboardMarkup:
    def create_wallet_button(wallet) -> InlineKeyboardButton:
        shortened_wallet = wallet["wallet"][:8] + "..." + wallet["wallet"][-4:] if wallet["wallet"] else "Edit Me"
        return InlineKeyboardButton(shortened_wallet, callback_data=str(SNIPE) + f"_{wallet['wallet']}")

    wallet_buttons = [create_wallet_button(wallet) for wallet in wallets]
    keyboard = [wallet_buttons[i : i + 2] for i in range(0, len(wallets), 2)]  # 2 wallets per row

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=str(PREV_PAGE_SNIPER)))
    if last:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=str(NEXT_PAGE_SNIPER)))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append(
        [
            InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_SNIPER_MENU)),
            InlineKeyboardButton("🔄 Refresh", callback_data=str(REFRESH_SNIPER_MENU)),
            InlineKeyboardButton("➕ Add", callback_data=str(ADD_SNIPER_SETUP)),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


async def get_paginated_wallets(user, page):
    wallets = user.sniping
    start = page * WALLETS_PER_PAGE
    end = start + WALLETS_PER_PAGE
    return wallets[start:end], len(wallets) > end


async def generate_wallet_text(user, wallet):
    shortened_wallet = wallet["wallet"][:8] + "..." + wallet["wallet"][-4:] if wallet["wallet"] else "--"
    return (
        f"💼 <b>Wallet:</b> <code>{shortened_wallet}</code>\n"
        f"💰 <b>Amount:</b> <code>{nfpf(wallet['amount'])} TOKENS</code>\n"
        f"📈 <b>Slippage:</b> <code>{nfpf(wallet['slippage'])}%</code>\n"
        f"⚡ <b>Priority Fee:</b> <code>{nfpf(wallet['priority_fee'])} SOL</code>\n\n"
    )


async def sniper_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, new=False) -> int:
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)
    query = update.callback_query
    if query: await query.answer()

    wallets, has_more = await get_paginated_wallets(user, page)
    reply_markup = await generate_menu_keyboard(user, wallets, page, last=has_more)
    wallets_texts = [await generate_wallet_text(user, w) for w in wallets]

    balance = f"💰 Balance: <code>{nfpf(user.sol_in_wallet)} SOL (${nfpf(user.sol_in_wallet*sol_dollar_value())})</code>\n\n"
    text = (
        f"💊🎯 <b>Pump.fun Sniper Menu</b>\n\n"
        f"{balance}"
        f"{''.join(wallets_texts) if len(wallets_texts) else '➕ Add a sniping setup to get started.\n\n'}"
        f"🕒 <i>{utc_time_now()}</i>"
    )
    message_func = (query.message.reply_text if query else update.message.reply_text) if new else query.edit_message_text
    message = await message_func(
        text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    context.user_data.update({f"sniper_menu_page": page})
    return SNIPER_MENU


async def sniper_menu_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await sniper_menu(update, context, new=True)


async def refresh_sniper_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    page = context.user_data.get("sniper_menu_page", 0)
    return await sniper_menu(update, context, page=page)


async def add_sniper_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sniping = user_db.get_attribute(user_id, "sniping")
    # Check if none is in sniping
    if not any(setup.get("wallet") is None for setup in sniping):
        sniping.append(
            {
                "wallet": None,
                "amount": 100_000,
                "slippage": 200,
                "min_sol_cost": 0.002824121,
                "max_sol_cost": 0.002824121 * 3,
                "priority_fee": 0.01,
            }
        )
    user_db.update_attribute(user_id, "sniping", sniping)
    page = context.user_data.get("sniper_menu_page", 0)
    return await sniper_menu(update, context, page=page)


async def close_sniper_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    await context.bot.delete_message(chat_id, query.message.message_id)
    return SNIPER_MENU


async def sniper_menu_page(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: int) -> int:
    page = context.user_data.get("sniper_menu_page", 0) + direction
    return await sniper_menu(update, context, page=page, new=False)


async def edit_sniper_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    wallet = query.data.split("_")[1]
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    # Find the sniping setup for the given wallet
    if wallet == "None":
        setup_index = next((index for index, setup in enumerate(user.sniping) if setup.get("wallet") is None))
    else:
        setup_index = next(
            (index for index, setup in enumerate(user.sniping) if setup.get("wallet") == wallet),
            None,
        )

    if setup_index is not None:
        setup = user.sniping[setup_index]
        context.user_data["setup_index"] = setup_index
        # Generate the keyboard for editing the setup
        reply_markup = await generate_sniper_setup_keyboard(setup)
        text = await generate_sniper_setup_text(setup)

        # Send or edit the message
        if update.callback_query:
            message = await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        context.user_data["edit_setup_message_id"] = message.message_id

        return SNIPE

    else:
        # Handle the case where the wallet was not found
        await context.bot.send_message(
            chat_id=user_id,
            text=f"No sniping setup found for wallet {wallet}. Please check the wallet address and try again.",
            parse_mode="HTML",
        )
        return SNIPER_MENU


async def generate_sniper_setup_keyboard(setup) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{f"💼 {setup['wallet'][:8]}...{setup['wallet'][-4:]}" if setup['wallet'] else '💼 Wallet: --'} ✏️",
                    callback_data=str(SET_WALLET_SNIPER) + f"_{setup['wallet']}",
                ),
                InlineKeyboardButton(
                    f"💰 {nfpf(setup['amount'])} TOKENS ✏️",
                    callback_data=str(SET_AMOUNT_SNIPER) + f"_{setup['wallet']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"📊 {nfpf(setup['slippage'])}% ✏️",
                    callback_data=str(SET_SLIPPAGE_SNIPER) + f"_{setup['wallet']}",
                ),
                InlineKeyboardButton(
                    f"🚀 {nfpf(setup['priority_fee'])} SOL ✏️",
                    callback_data=str(SET_PRIORITY_FEE_SNIPER) + f"_{setup['wallet']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Remove Wallet", callback_data=str(REMOVE_SNIPER_SETUP) + f"_{setup['wallet']}"
                ),
                InlineKeyboardButton("🔙 Back", callback_data=str(SNIPER_MENU)),
            ],
        ]
    )


async def generate_sniper_setup_text(setup):
    return (
        "💊🎯 <b>Pump.fun Sniper Setup</b>\n\n"
        #        "<b>Auto-Purchase New Tokens When Minted</b>\n\n"
        "💼 <b>Wallet</b>\n"
        "• Snipe is executed on new tokens created by this wallet\n"
        "💰 <b>Amount</b>\n"
        "• Tokens to buy per transaction\n"
        "📊 <b>Slippage</b>\n"
        "• Higher = better success chance\n"
        "• Can set high if confident\n"
        "  (bonding curve ~1500% of launch)\n"
        "🚀 <b>Priority Fee</b>\n"
        "• Higher = better chance of sniping\n\n"
        "⚠️ <b>Caution:</b> Auto-executes on\n"
        "new token detection. Verify settings.\n\n"
        f"💱 <b>SOL Cost Range</b>\n"
        f"• Min: <code>{nfpf(setup['min_sol_cost'])} SOL</code>\n"
        f"• Max: <code>{nfpf(setup['max_sol_cost'])} SOL</code>\n\n"
        f"🕒 {utc_time_now()}"
    )


async def remove_sniper_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sniping = user_db.get_attribute(user_id, "sniping")
    del sniping[context.user_data["setup_index"]]
    user_db.update_attribute(user_id, "sniping", sniping)
    return await sniper_menu(update, context, page=context.user_data.get("sniper_menu_page", 0))


async def prompt_custom_input_sniper(
    update: Update, context: ContextTypes.DEFAULT_TYPE, option: str, prompt: str, next_state: int
) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text=prompt)
    context.user_data[f"custom_{option}_message_id"] = message.message_id
    return next_state


async def set_custom_wallet_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input_sniper(
        update, context, "wallet", "Please enter your wallet public key:", CUSTOM_WALLET_SNIPER
    )


async def set_custom_amount_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input_sniper(
        update,
        context,
        "amount",
        "Please enter the number of tokens you wish to snipe:",
        CUSTOM_AMOUNT_SNIPER,
    )


async def set_custom_slippage_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input_sniper(
        update, context, "slippage", "Please enter your custom lippage %:", CUSTOM_SLIPPAGE_SNIPER
    )


async def set_custom_priority_fee_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await prompt_custom_input_sniper(
        update,
        context,
        "priority_fee",
        "Please enter your priority fee in SOL:",
        CUSTOM_PRIORITY_FEE_SNIPER,
    )


async def process_custom_input_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE, option: str) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        value = (
            float(update.message.text.replace("%", ""))
            if option != "wallet"
            else str(Pubkey.from_string(update.message.text))
        )
        if option == "slippage":
            value = int(max(50, value))
        elif option == "amount":
            value = max(0.002, value)
        elif option == "priority_fee":
            value = max(0.001, value)

        setup_index = context.user_data["setup_index"]
        user = user_db.get_user(user_id)
        # check if a sniping setup isn't already present for the wallet
        if option == "wallet" and any(s["wallet"] == value for s in user.sniping):
            await update.message.reply_text("You can't have more than one sniping setup for a wallet.")
        else:
            user.sniping[setup_index][option] = value
            # Update the max_sol_cost
            slippage = user.sniping[setup_index]["slippage"]
            amount = user.sniping[setup_index]["amount"]
            user.sniping[setup_index]["min_sol_cost"] = (
                -(amount * 1e6 * 30000000000) / (amount * 1e6 - 1073000000000000) / 1e9
            )  # crazy constants taken from pump.fun bonding curve data
            user.sniping[setup_index]["max_sol_cost"] = (1 + slippage / 100) * user.sniping[setup_index][
                "min_sol_cost"
            ]
            # Update the database
            user_db.update_attribute(user_id, "sniping", user.sniping)

        # Update the message
        reply_markup = await generate_sniper_setup_keyboard(user.sniping[setup_index])
        text = await generate_sniper_setup_text(user.sniping[setup_index])
        await delete_messages(
            context,
            chat_id,
            context.user_data.get(f"custom_{option}_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get("edit_setup_message_id"),
            text,
            reply_markup,
        )
        return SNIPE
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get(f"custom_{option}_message_id"),
            text="Invalid input. Please enter a valid value.",
        )
        return (
            CUSTOM_WALLET_SNIPER
            if option == "wallet"
            else (
                CUSTOM_AMOUNT_SNIPER
                if option == "amount"
                else CUSTOM_SLIPPAGE_SNIPER if option == "slippage" else CUSTOM_PRIORITY_FEE_SNIPER
            )
        )


async def custom_wallet_sniper_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input_sniper(update, context, "wallet")


async def custom_amount_sniper_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input_sniper(update, context, "amount")


async def custom_slippage_sniper_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input_sniper(update, context, "slippage")


async def custom_priority_fee_sniper_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_custom_input_sniper(update, context, "priority_fee")


"""
sniper_menu_new, NEW_SNIPER_MENU - open the sniper menu
sniper_menu, SNIPER_MENU - general handler for the sniper menu
close_sniper_menu, CLOSE_SNIPER_MENU - self-explanatory
refresh_sniper_menu, REFRESH_SNIPER_MENU - might not actually be needed
SNIPE_{wallet} - open a sniping setup for a certain wallet
"""

sniper_menu_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(sniper_menu_new, pattern="^" + str(NEW_SNIPER_MENU) + "$"),
        CommandHandler("sniper", sniper_menu_new),
    ],
    states={
        SNIPER_MENU: [
            CallbackQueryHandler(sniper_menu, pattern="^" + str(SNIPER_MENU) + "$"),
            CallbackQueryHandler(sniper_menu_new, pattern="^" + str(NEW_SNIPER_MENU) + "$"),
            CallbackQueryHandler(close_sniper_menu, pattern="^" + str(CLOSE_SNIPER_MENU) + "$"),
            CallbackQueryHandler(refresh_sniper_menu, pattern="^" + str(REFRESH_SNIPER_MENU) + "$"),
            CallbackQueryHandler(add_sniper_setup, pattern="^" + str(ADD_SNIPER_SETUP) + "$"),
            CallbackQueryHandler(
                lambda update, context: sniper_menu_page(update, context, direction=1),
                pattern="^" + str(NEXT_PAGE_SNIPER) + "$",
            ),
            CallbackQueryHandler(
                lambda update, context: sniper_menu_page(update, context, direction=-1),
                pattern="^" + str(PREV_PAGE_SNIPER) + "$",
            ),
            CallbackQueryHandler(edit_sniper_setup, pattern="^" + str(SNIPE) + "_"),
        ],
        SNIPE: [
            CallbackQueryHandler(sniper_menu, pattern="^" + str(SNIPER_MENU) + "$"),
            CallbackQueryHandler(set_custom_wallet_sniper, pattern="^" + str(SET_WALLET_SNIPER) + "_"),
            CallbackQueryHandler(set_custom_amount_sniper, pattern="^" + str(SET_AMOUNT_SNIPER) + "_"),
            CallbackQueryHandler(set_custom_slippage_sniper, pattern="^" + str(SET_SLIPPAGE_SNIPER) + "_"),
            CallbackQueryHandler(
                set_custom_priority_fee_sniper, pattern="^" + str(SET_PRIORITY_FEE_SNIPER) + "_"
            ),
            CallbackQueryHandler(remove_sniper_setup, pattern="^" + str(REMOVE_SNIPER_SETUP) + "_"),
        ],
        CUSTOM_WALLET_SNIPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_wallet_sniper_input)],
        CUSTOM_AMOUNT_SNIPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_amount_sniper_input)],
        CUSTOM_SLIPPAGE_SNIPER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_slippage_sniper_input)
        ],
        CUSTOM_PRIORITY_FEE_SNIPER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_priority_fee_sniper_input)
        ],
    },
    fallbacks=[CommandHandler("sniper", sniper_menu_new)],
)
