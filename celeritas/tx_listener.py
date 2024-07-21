import asyncio
import json
import logging
import signal
import websockets

from solders.signature import Signature
from solders.pubkey import Pubkey

from telegram.ext import Application

from celeritas.db import transaction_db
from celeritas.db import user_db
from celeritas.config import config
from celeritas.constants import SOLANA_MINT, SOLANA_WS_URL, LAMPORTS_PER_SOL
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.telegram_bot.fetch_tx_update_msg import (
    update_fees,
    parse_transaction_data,
    generate_success_message,
    generate_failure_message,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

try:
    PLATFORM_FEE_PUBKEY = str(Pubkey.from_string(config.platform_fee_pubkey))
except:
    raise Exception("Invalid or missing platform fee pubkey!")

application = Application.builder().token(config.telegram_bot_token).build()

def parse_transaction_data(tx: dict, user_pubkey: str, mint: str, block_time) -> dict:
    account_keys = [key['pubkey'] for key in  tx['transaction']['message']['accountKeys']]
    sol_balance_change = {
        key: (
            tx['meta']['preBalances'][ix],
            tx['meta']['postBalances'][ix],
        )
        for ix, key in enumerate(account_keys)
    }

    pre_token_balance = next(
        (
            b['uiTokenAmount']['uiAmount']
            for b in tx['meta']['preTokenBalances']
            if b['owner'] == user_pubkey and b['mint'] == mint
        ),
        0,
    )
    post_token_balance = next(
        (
            b['uiTokenAmount']['uiAmount']
            for b in tx['meta']['postTokenBalances']
            if b['owner'] == user_pubkey and b['mint'] == mint
        ),
        0,
    )

    pre_sol_balance, post_sol_balance = sol_balance_change.get(user_pubkey, (0, 0))
    fee_paid = (lambda x: x[1] - x[0])(sol_balance_change.get(PLATFORM_FEE_PUBKEY, (0, 0)))

    return {
        "timestamp": block_time,
        "mint": mint,
        "pre_sol_balance": pre_sol_balance / LAMPORTS_PER_SOL,
        "post_sol_balance": post_sol_balance / LAMPORTS_PER_SOL,
        "pre_token_balance": pre_token_balance,
        "post_token_balance": post_token_balance,
        "sol_dollar_value": sol_dollar_value(),
        "fee_paid": fee_paid / LAMPORTS_PER_SOL,
    }

async def update_message(message_info, tx, block_time):
    if tx['meta']['err']:
        await application.bot.edit_message_text(
            chat_id=message_info['user_id'],
            message_id=message_info['message_id'],
            text=f"😅 <b>Oops! Your transaction seems to have failed...</b>\n\n🔎 Tx details: <a href='https://solscan.io/tx/{message_info['tx_signature']}'>View on Solscan</a>",
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        return
    parsed_tx_data = parse_transaction_data(
        tx,
        message_info['user_wallet'],
        message_info['mint'],
        block_time
    )
    await application.bot.edit_message_text(
        chat_id=message_info['user_id'],
        message_id=message_info['message_id'],
        text=generate_success_message(message_info['tx_signature'], parsed_tx_data),
        parse_mode='HTML',
        disable_web_page_preview=True,
    )

async def unsubscribe():
    if websocket_connection and subscription_id:
        unsubscribe_params = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "logsUnsubscribe",
            "params": [subscription_id],
        }
        await websocket_connection.send(json.dumps(unsubscribe_params))
        logger.info("Unsubscribed from logs")

websocket_connection = None
subscription_id = None

async def subscribe_blocks():
    global websocket_connection
    global subscription_id

    async with websockets.connect(SOLANA_WS_URL) as websocket:
        websocket_connection = websocket
        # Construct the subscription request payload
        params = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "blockSubscribe",
            "params": [
                {"mentionsAccountOrProgram": PLATFORM_FEE_PUBKEY},
                {
                    "commitment": "confirmed",  # The level of commitment required
                    "transactionDetails": "full",  # The level of transaction detail to return
                    "showRewards": False,  # Whether to populate the 'rewards' array
                    "encoding": "jsonParsed",  # Encoding format for account data
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        await websocket.send(json.dumps(params))
        logger.info("Tx Listener started.")

        while True:
            response = await websocket.recv()
            data = json.loads(response)
            if "params" in data:
                for tx in data['params']['result']['value']['block']['transactions']:
                    sig = tx['transaction']['signatures'][0]

                    message_info = await transaction_db.fetch_transaction(sig)
                    if not message_info: continue
                    await update_message(
                        message_info,
                        tx,
                        data['params']['result']['value']['block']['blockTime']
                    )

                    await transaction_db.delete_transaction(sig)

            if "result" in data and "id" in data and data["id"] == 1:
                subscription_id = data["result"]


async def shutdown(signal, loop):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received exit signal {signal.name}...")

    await unsubscribe()
    if websocket_connection:
        await websocket_connection.close()

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    [task.cancel() for task in tasks]

    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


async def main():
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(s, loop))
        )

    try:
        await subscribe_blocks()
    except Exception as e: 
        logger.error("An exception has occurred in subscribe_blocks() of tx_listener:", e)
    finally:
        logger.info("Shutdown complete.")