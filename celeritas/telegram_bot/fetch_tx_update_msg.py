import os
import asyncio
from datetime import datetime
from telegram.ext import ContextTypes
from solders.signature import Signature
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from celeritas.config import config
from celeritas.constants import LAMPORTS_PER_SOL, RPC_URL
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.db import UserDB

try:
    PLATFORM_FEE_PUBKEY = Pubkey.from_string(config.platform_fee_pubkey)
except:
    raise Exception("Missing platform fee pubkey!")

db = UserDB()

async def fetch_transaction(tx_signature: Signature) -> dict:
    async with AsyncClient(RPC_URL) as client:
        return await client.get_transaction(
            tx_signature, commitment="confirmed", max_supported_transaction_version=0
        )


def parse_transaction_data(tx: dict, user_pubkey: Pubkey, mint: str) -> dict:
    account_keys = tx.value.transaction.transaction.message.account_keys
    sol_balance_change = {
        key: (
            tx.value.transaction.meta.pre_balances[ix],
            tx.value.transaction.meta.post_balances[ix],
        )
        for ix, key in enumerate(account_keys)
    }

    pre_token_balance = next(
        (
            b.ui_token_amount.ui_amount
            for b in tx.value.transaction.meta.pre_token_balances
            if b.owner == user_pubkey and str(b.mint) == mint
        ),
        0,
    )
    post_token_balance = next(
        (
            b.ui_token_amount.ui_amount
            for b in tx.value.transaction.meta.post_token_balances
            if b.owner == user_pubkey and str(b.mint) == mint
        ),
        0,
    )

    pre_sol_balance, post_sol_balance = sol_balance_change.get(user_pubkey, (0, 0))
    fee_paid = (lambda x: x[1] - x[0])(
        sol_balance_change.get(PLATFORM_FEE_PUBKEY, (0, 0))
    )

    return {
        "timestamp": tx.value.block_time,
        "mint": mint,
        "pre_sol_balance": pre_sol_balance/LAMPORTS_PER_SOL,
        "post_sol_balance": post_sol_balance/LAMPORTS_PER_SOL,
        "pre_token_balance": pre_token_balance,
        "post_token_balance": post_token_balance,
        "sol_dollar_value": sol_dollar_value(),
        "fee_paid": fee_paid/LAMPORTS_PER_SOL,
    }


def update_fees(user_id, base_fee, depth):
    if not user_id or depth > 4: return
    user = db.get_user(user_id)
    fee_for_trade = (user.referral_share[depth]*base_fee)
    db.update_attribute(user_id, "trading_fees_earned", user.trading_fees_earned + fee_for_trade)
    update_fees(user.referrer, base_fee, depth+1)


def generate_success_message(tx_signature: str, tx_data: dict) -> str:
    token_amount = abs(tx_data["post_token_balance"] - tx_data["pre_token_balance"])
    sol_amount = abs(tx_data["post_sol_balance"] - tx_data["pre_sol_balance"])
    transaction_type = "Buy" if tx_data["post_token_balance"] > tx_data["pre_token_balance"] else "Sell"
    return (
        f"✅ <b>{transaction_type} Transaction Successful!</b>\n\n"
        f"🔄 <code>{nfpf(token_amount)}</code> <code>{tx_data['mint']}</code>\n"
        f"💰 <code>{nfpf(sol_amount)} SOL</code>\n"
        f"🕒 <code>{datetime.fromtimestamp(tx_data['timestamp']).strftime('%Y-%m-%d %H:%M:%S UTC')}</code>\n\n"
        f"🔍 Tx details: <a href='https://solscan.io/tx/{tx_signature}'>View on Solscan</a>"
    )


def generate_failure_message(tx_signature: str) -> str:
    return (
        "❗ <b>Transaction Status Update</b>\n\n"
        "We couldn't confirm the success of your transaction. "
        "Consider using a higher Priority Fees.\n\n"
        f"Tx details: <a href='https://solscan.io/tx/{tx_signature}'>View on Solscan</a>\n\n"
    )


async def check_transaction(context: ContextTypes.DEFAULT_TYPE, attempt: int, chat_id, message_id, user_id, tx_signature, mint, user_pubkey) -> bool:
    user_pubkey = Pubkey.from_string(user_pubkey)
    tx = await fetch_transaction(tx_signature)

    if tx.value and not tx.value.transaction.meta.err:
        # Add transaction to UserDB only if it hasn't been added before
        user = db.get_user(user_id)
        tx_data = parse_transaction_data(tx, user_pubkey, mint)
        
        # Check if this transaction is already in the user's transactions
        if not any(t['timestamp'] == tx.value.block_time for t in user.transactions):
            user.transactions.append(tx_data)
            user.revenue += tx_data["fee_paid"]
            db.update_attribute(user_id, "revenue", user.revenue)
            db.update_attribute(user_id, "transactions", user.transactions)
            update_fees(user.referrer, tx_data["fee_paid"], 0)

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=generate_success_message(str(tx_signature), tx_data),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    elif attempt < 4:
        return False
    else:  # If it's the last attempt and still not successful, send a failure message
        new_text = generate_failure_message(str(tx_signature))
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=new_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True

async def update_transaction_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    for attempt, delay in enumerate([10, 20, 30, 60], start=1):
        await asyncio.sleep(delay)
        chat_id, message_id, user_id, tx_signature, mint, user_pubkey = context.job.data
        success = await check_transaction(context, attempt, chat_id, message_id, user_id, tx_signature, mint, user_pubkey)
        if success:
            break

async def schedule_tx_update(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    user_id: int,
    tx_signature: str,
    mint: str,
    user_pubkey: str,
) -> None:
    context.job_queue.run_once(
        update_transaction_message,
        when=1,  # Start almost immediately
        data=(chat_id, message_id, user_id, tx_signature, mint, user_pubkey),
        name=f"tx_update_{tx_signature}",
    )