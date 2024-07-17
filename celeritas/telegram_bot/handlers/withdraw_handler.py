import logging

import spl.token.instructions as spl_instructions
from solana.rpc.types import TxOpts
from solders.compute_budget import set_compute_unit_limit
from solders.compute_budget import set_compute_unit_price
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import transfer
from solders.system_program import TransferParams
from solders.transaction import VersionedTransaction
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
from celeritas.constants import LAMPORTS_PER_SOL
from celeritas.db import token_db
from celeritas.db import user_db
from celeritas.telegram_bot.callbacks import *
from celeritas.telegram_bot.utils import delete_messages
from celeritas.telegram_bot.utils import edit_message
from celeritas.telegram_bot.utils import get_blockhash
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import utc_time_now
from celeritas.transact_utils import get_token_account
from celeritas.transact_utils import TOKEN_PROGRAM_ID

logger = logging.getLogger(__name__)


async def generate_withdraw_keyboard(user, options) -> InlineKeyboardMarkup:
    withdraw_amounts = [20, 50, 100]
    percentage_to_withdraw = options["percentage_to_withdraw"]
    wallet = options["wallet"]
    keyboard = [
        [
            InlineKeyboardButton("❌ Close", callback_data=str(CLOSE_WITHDRAW)),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"{REFRESH_WITHDRAW}_{options['mint']}"),
        ],
        [
            InlineKeyboardButton(
                f"{'🔵 ' if p == percentage_to_withdraw else ''}{p}%",
                callback_data=f"{SET_AMOUNT_TO_WITHDRAW}_{p}",
            )
            for p in withdraw_amounts
        ],
        [
            InlineKeyboardButton(
                (
                    f"🔵 {nfpf(percentage_to_withdraw)}%"
                    if percentage_to_withdraw not in withdraw_amounts
                    else "Custom % ✏️"
                ),
                callback_data=str(SET_CUSTOM_AMOUNT_TO_WITHDRAW),
            )
        ]
        + [InlineKeyboardButton(f"X amount ✏️", callback_data=str(SET_WHOLE_AMOUNT_FOR_WITHDRAW))],
        [
            InlineKeyboardButton(
                f"To: {wallet[:10]}...{wallet[-5:]}" if wallet else "Set Withdraw Wallet",
                callback_data=str(SET_WALLET_FOR_WITHDRAW),
            )
        ],
    ]
    if wallet:
        keyboard.append([InlineKeyboardButton("WITHDRAW", callback_data=str(EXECUTE_WITHDRAW))])

    return InlineKeyboardMarkup(keyboard)


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE, new=False, mint=None) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()

    user = user_db.get_user(user_id)

    if not mint:
        mint = query.data.split("_")[1]

    if new:
        options = {
            "percentage_to_withdraw": 50,
            "mint": mint,
            "wallet": None,
        }
        context.user_data[f"withdraw_message_options_{mint}"] = options
    else:
        options = context.user_data[f"withdraw_message_options_{mint}"]

    text = await generate_withdraw_text(user, options)
    reply_markup = await generate_withdraw_keyboard(user, options)

    message_func = query.message.reply_text if new else query.edit_message_text
    message = await message_func(
        text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
    )
    context.user_data[message.message_id] = mint
    context.user_data[f"withdraw_message_{mint}"] = message.message_id

    return WITHDRAW


async def generate_withdraw_text(user, options):
    if options["mint"] == "SOL":
        return await generate_withdraw_text_sol(user, options)

    token = await token_db.get_token(options["mint"])
    t_symbol = token["symbol"].upper().replace("$", "")
    t_mint, t_name = token["mint"], token["name"]
    token_link = f"https://solscan.io/token/{t_mint}"

    return (
        f"<b>Withdraw {t_symbol}</b> - {t_name} "
        f'<a href="{token_link}">🔗</a>\n\n'
        f"💰 Balance: <code>{nfpf(user.holdings.get(t_mint, 0))} {t_symbol}</code>\n"
        f"💵 Price: <code>${nfpf(token['price_dollars'])}</code>\n\n"
        f"🔢 Amount to withdraw:\n"
        f"<code>{nfpf(options['percentage_to_withdraw']/100 * user.holdings[t_mint])} {t_symbol}</code>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )


async def generate_withdraw_text_sol(user, options):
    token_link = f"https://solscan.io/token/So11111111111111111111111111111111111111112"
    return (
        f"<b>Withdraw SOL</b> - Solana's Native Token "
        f'<a href="{token_link}">🔗</a>\n\n'
        f"💰 Balance: <code>{nfpf(user.sol_in_wallet)} SOL</code>\n"
        f"💵 Price: <code>${nfpf(sol_dollar_value())}</code>\n\n"
        f"🔢 Amount to withdraw:\n"
        f"<code>{nfpf(options['percentage_to_withdraw']/100 * user.sol_in_wallet)} SOL</code>\n\n"
        f"🕒 <i>{utc_time_now()}</i>"
    )


async def withdraw_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await withdraw(update, context, new=True)


async def refresh_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_db.update_user_holdings(update.effective_user.id)
    return await withdraw(update, context, new=False)


async def close_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.delete_message(query.message.chat_id, query.message.message_id)
    return WITHDRAW


async def set_amount_to_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data.split("_")
    mint = context.user_data[query.message.message_id]
    percentage = int(data[1])
    context.user_data[f"withdraw_message_options_{mint}"]["percentage_to_withdraw"] = percentage
    return await withdraw(update, context, new=False, mint=mint)


async def set_custom_amount_to_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your custom withdraw %:")
    context.user_data["custom_percentage_to_withdraw_message_id"] = message.message_id
    context.user_data["last_mint"] = context.user_data[query.message.message_id]
    return CUSTOM_PERCENTAGE


async def custom_percentage_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        percentage = float(update.message.text.replace("%", ""))
        percentage = min(100, max(1, percentage))

        user = user_db.get_user(user_id)

        mint = context.user_data["last_mint"]
        context.user_data[f"withdraw_message_options_{mint}"]["percentage_to_withdraw"] = percentage
        options = context.user_data[f"withdraw_message_options_{mint}"]
        reply_markup = await generate_withdraw_keyboard(user, options)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("custom_percentage_to_withdraw_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get(f"withdraw_message_{mint}"),
            await generate_withdraw_text(user, options),
            reply_markup,
        )
        return WITHDRAW
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("custom_percentage_to_withdraw_message_id"),
            text="❗ Invalid input. Please enter a valid percentage.",
        )
        return CUSTOM_PERCENTAGE


async def set_wallet_for_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your withdraw wallet public key.")
    context.user_data["withdraw_wallet_message_id"] = message.message_id
    context.user_data["last_mint"] = context.user_data[query.message.message_id]
    return WALLET_FOR_WITHDRAW_INPUT


async def wallet_for_withdraw_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        wallet = str(Pubkey.from_string(update.message.text))
        user = user_db.get_user(user_id)

        mint = context.user_data["last_mint"]
        context.user_data[f"withdraw_message_options_{mint}"]["wallet"] = wallet
        options = context.user_data[f"withdraw_message_options_{mint}"]
        reply_markup = await generate_withdraw_keyboard(user, options)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("withdraw_wallet_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get(f"withdraw_message_{mint}"),
            await generate_withdraw_text(user, options),
            reply_markup,
        )
        return WITHDRAW
    except ValueError as e:
        await delete_messages(context, chat_id, update.message.message_id)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("withdraw_wallet_message_id"),
            text="❗ Invalid address. Please provide a valid one.",
        )
        return WALLET_FOR_WITHDRAW_INPUT


async def set_whole_amount_for_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message = await query.message.reply_text(text="Please enter your amount X for withdrawal.")
    context.user_data["withdraw_amount_message_id"] = message.message_id
    context.user_data["last_mint"] = context.user_data[query.message.message_id]
    return WHOLE_AMOUNT_FOR_WITHDRAW_INPUT


async def whole_amount_for_withdraw_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        amount = float(update.message.text)
        user = user_db.get_user(user_id)

        mint = context.user_data["last_mint"]
        percentage = min(
            100,
            max(1, 100 * amount / (user.holdings[mint] if mint != "SOL" else user.sol_in_wallet)),
        )
        context.user_data[f"withdraw_message_options_{mint}"]["percentage_to_withdraw"] = percentage
        options = context.user_data[f"withdraw_message_options_{mint}"]
        reply_markup = await generate_withdraw_keyboard(user, options)
        await delete_messages(
            context,
            chat_id,
            context.user_data.get("withdraw_amount_message_id"),
            update.message.message_id,
        )
        await edit_message(
            context,
            chat_id,
            context.user_data.get(f"withdraw_message_{mint}"),
            await generate_withdraw_text(user, options),
            reply_markup,
        )
        return WITHDRAW
    except ValueError:
        await delete_messages(context, chat_id, update.message.message_id)
        await update.message.reply_text()
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=context.user_data.get("withdraw_amount_message_id"),
            text="❗ Invalid input. Please enter a valid amount.",
        )
        return WHOLE_AMOUNT_FOR_WITHDRAW_INPUT


async def execute_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    mint = context.user_data[query.message.message_id]
    t_symbol = "SOL" if mint == "SOL" else (await token_db.get_token(mint))["symbol"]
    options = context.user_data[f"withdraw_message_options_{mint}"]
    context.user_data["last_mint"] = mint

    # Return if user doesn't have any of the token.
    if (mint != "SOL" and not user.holdings[mint]) or (mint == "SOL" and not user.sol_in_wallet):
        text = (
            f"⚠️ <b>Insufficient Balance</b>\n\n"
            f"You don't have any {t_symbol} in your wallet.\n"
            "If you believe this is an error, please contact support."
        )
        await query.message.reply_text(text=text, parse_mode="HTML", disable_web_page_preview=True)
        return WITHDRAW

    if user.settings.confirm_trades:
        # Calculate the withdrawal amount
        withdrawal_amount = (
            options["percentage_to_withdraw"]
            / 100
            * (user.sol_in_wallet if mint == "SOL" else user.holdings[mint])
        )
        # Ask for confirmation
        confirmation_text = (
            f"🔔 <b>Confirm Your Withdrawal</b>\n\n"
            f"You are about to withdraw:\n\n"
            f"🔢 Amount: <code>{nfpf(withdrawal_amount)} {t_symbol}</code>\n"
            f"📊 Percentage: <code>{nfpf(options['percentage_to_withdraw'])}%</code> of your {t_symbol}\n\n"
            f"Do you want to proceed?"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=str(CONFIRM_WITHDRAW)),
                InlineKeyboardButton("❌ Cancel", callback_data=str(CANCEL_WITHDRAW)),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(text=confirmation_text, reply_markup=reply_markup, parse_mode="HTML")
        return CONFIRM_WITHDRAW

    # If no confirmation is needed, proceed with the withdrawal
    return await process_withdraw(update, context, delete=False)


async def process_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE, delete=True) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    user = user_db.get_user(user_id)

    mint = context.user_data["last_mint"]
    options = context.user_data[f"withdraw_message_options_{mint}"]

    message = await query.message.reply_text(
        text="🔍 Processing your withdrawal... Hang tight!",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    if delete:
        await query.message.delete()

    # Handle 100% sol withdrawals
    withdrawal_amount = min(
        user.sol_in_wallet * options["percentage_to_withdraw"]/100 - 0.000005, # fee associated with a sol withdrawal
        user.sol_in_wallet * options["percentage_to_withdraw"]/100
    ) if mint == "SOL" else (
        user.holdings[mint] * options["percentage_to_withdraw"] / 100
    )

    txs = await send_withdrawal(
        user.wallet_secret,
        options["wallet"],
        options["mint"],
        (withdrawal_amount),
    )

    if txs:
        text = (
            f"🎉 <b>Woohoo! Your {nfpf(options['percentage_to_withdraw'])}% withdrawal is zooming through cyberspace!</b>\n\n"
            f"🔍 Tx details: <a href='https://solscan.io/tx/{txs}'>View on Solscan</a>"
        )
    else:
        text = (
            f"⚠️ <b>Withdrawal Error</b>\n\n"
            f"We encountered an issue processing your withdrawal request. "
            f"Please try again later or contact our support team for assistance."
        )

    await message.edit_text(text=text, parse_mode="HTML", disable_web_page_preview=True)

    # if txs:
    #    await schedule_tx_update(context, message.chat_id, message.message_id, user_id, txs, "SOL", user.wallet_public)

    return WITHDRAW


async def send_withdrawal(sender_secret, receiver, mint, amount):
    try:
        keypair = Keypair.from_base58_string(sender_secret)
        receiver = Pubkey.from_string(receiver)

        if str(mint) == "SOL":
            ixs = await sol_withdrawal_ixs(keypair, receiver, amount)
        else:
            mint_pubkey = Pubkey.from_string(mint)
            ixs = await spl_token_withdrawal_ixs(keypair, receiver, mint_pubkey, amount)

        message = MessageV0.try_compile(keypair.pubkey(), ixs, [], get_blockhash())
        tx = VersionedTransaction(message, [keypair])
        txs = await aclient.send_transaction(
            tx, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed")
        )
        return txs.value
    except:
        return None


async def sol_withdrawal_ixs(keypair, receiver, amount):
    return [
        #set_compute_unit_limit(1000),
        #set_compute_unit_price(4_000_000),
        transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=receiver,
                lamports=int(amount * LAMPORTS_PER_SOL),
            )
        ),
    ]


async def spl_token_withdrawal_ixs(keypair, receiver, mint, amount):
    wallet_token_account, _ = await get_token_account(keypair.pubkey(), mint)
    receiver_token_account, receiver_token_account_ix = await get_token_account(
        receiver, mint, payer=keypair.pubkey()
    )

    ixs = [
        set_compute_unit_limit(50_000 if receiver_token_account_ix else 6_000),
        set_compute_unit_price(4_000_000),
    ]

    if receiver_token_account_ix:
        ixs.append(receiver_token_account_ix)

    decimals = await token_db.get_token_decimals(str(mint))
    ixs.append(
        spl_instructions.transfer(
            spl_instructions.TransferParams(
                amount=int(amount * (10**decimals)),
                dest=receiver_token_account,
                owner=keypair.pubkey(),
                program_id=TOKEN_PROGRAM_ID,
                source=wallet_token_account,
            )
        )
    )
    return ixs


async def confirm_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Process the withdrawal
    return await process_withdraw(update, context)


async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        text="❌ Withdrawal operation cancelled.",
    )
    await query.message.delete()
    return WITHDRAW


withdraw_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(withdraw_new, pattern="^" + str(WITHDRAW_NEW) + "_")],
    states={
        WITHDRAW: [
            CallbackQueryHandler(withdraw, pattern="^" + str(WITHDRAW) + "_"),
            CallbackQueryHandler(withdraw_new, pattern="^" + str(WITHDRAW_NEW) + "_"),
            CallbackQueryHandler(refresh_withdraw, pattern="^" + str(REFRESH_WITHDRAW)),
            CallbackQueryHandler(close_withdraw, pattern="^" + str(CLOSE_WITHDRAW)),
            CallbackQueryHandler(set_amount_to_withdraw, pattern="^" + str(SET_AMOUNT_TO_WITHDRAW) + "_"),
            CallbackQueryHandler(
                set_custom_amount_to_withdraw,
                pattern="^" + str(SET_CUSTOM_AMOUNT_TO_WITHDRAW) + "$",
            ),
            CallbackQueryHandler(execute_withdraw, pattern="^" + str(EXECUTE_WITHDRAW) + "$"),
            CallbackQueryHandler(set_wallet_for_withdraw, pattern="^" + str(SET_WALLET_FOR_WITHDRAW) + "$"),
            CallbackQueryHandler(
                set_whole_amount_for_withdraw,
                pattern="^" + str(SET_WHOLE_AMOUNT_FOR_WITHDRAW) + "$",
            ),
        ],
        CUSTOM_PERCENTAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_percentage_input)],
        WALLET_FOR_WITHDRAW_INPUT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_for_withdraw_input)
        ],
        WHOLE_AMOUNT_FOR_WITHDRAW_INPUT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, whole_amount_for_withdraw_input)
        ],
        CONFIRM_WITHDRAW: [
            CallbackQueryHandler(confirm_withdraw, pattern="^" + str(CONFIRM_WITHDRAW) + "$"),
            CallbackQueryHandler(cancel_withdraw, pattern="^" + str(CANCEL_WITHDRAW) + "$"),
        ],
    },
    fallbacks=[CommandHandler(str(WITHDRAW), withdraw)],
)
