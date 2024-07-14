import asyncio
import json
import logging
import time
import signal
import websockets

from aiolimiter import AsyncLimiter
from solders.signature import Signature
from telegram.ext import Application
from telegram.ext import CallbackContext

from celeritas.config import config
from celeritas.constants import SOLANA_WS_URL
from celeritas.db import user_db
from celeritas.telegram_bot.fetch_tx_update_msg import schedule_tx_update
from celeritas.telegram_bot.utils import nice_float_price_format as nfpf
from celeritas.transact import Transact

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Pump.fun mint authority public key
MINT_AUTHORITY_PUBKEY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"

application = Application.builder().token(config.telegram_bot_token).build()

# Initialize a local wallet cache
watched_wallets = set()

# Create a rate limiter to limit concurrent tasks
# allow for MAX_TASKS_PER_SECOND entries within a 1 second window
MAX_TASKS_PER_SECOND = 3
rate_limit = AsyncLimiter(MAX_TASKS_PER_SECOND, 1)


async def refresh_wallet_cache():
    global watched_wallets
    while True:
        watched_wallets = set()  # Clear the cache

        # Fetch all watched wallets from the database
        users = user_db.users.find({}, {"sniping.wallet": 1})
        for user in users:
            for setup in user.get("sniping", []):
                if setup.get("wallet"):
                    watched_wallets.add(setup["wallet"])

        logger.info(f"Refreshed wallet cache with {len(watched_wallets)} wallets.")

        # Wait for 60 seconds before the next refresh
        await asyncio.sleep(60)


def parse_block(block):
    coins = []
    for tr in block["params"]["result"]["value"]["block"]["transactions"]:
        instructions = tr["transaction"]["message"]["instructions"]
        for instruction in instructions:
            accounts = instruction.get("accounts", [])
            if len(accounts) == 14:
                if not tr["meta"]["err"]:
                    coins.append(
                        (
                            accounts[0],
                            accounts[7],
                            time.time() - block["params"]["result"]["value"]["block"]["blockTime"],
                            accounts[2],
                            accounts[3],
                        )
                    )
                break
    return coins


async def snipe_for_user(user, sniping_setup, mint, bonding_curve, associated_bonding_curve, time_diff):
    try:
        transact = Transact(user["wallet_secret"], fee_sol=sniping_setup["priority_fee"])

        output_amount = sniping_setup["amount"]
        min_sol_cost, max_sol_cost = sniping_setup["min_sol_cost"], sniping_setup["max_sol_cost"]
        # Execute the snipe
        quote = await transact.snipe_pump_fun(
            mint, output_amount, max_sol_cost, bonding_curve, associated_bonding_curve
        )

        # Send the transaction
        async with rate_limit:
            txs = Signature.from_string(
                "3SriJZqAYe1jGbUGGCEwGkriJJBVsf5PdaGwtwTm3jtTdF1AVfBGWL2dgodKrySKWcSBZShcNetyar7GfmvCSy7S"
            )
            fee = (0.9 if user["referrer"] else 1) * (min_sol_cost + max_sol_cost) / 2
            # txs = await transact.construct_and_send(
            #    quote,
            #    fee=fee # add transaction fee
            # )

        if txs:
            text = (
                f"🚀 <b>Snipe order for {nfpf(output_amount)} sent!</b>\n\n"
                f'<b>Transaction details</b>:\n• <a href="https://solscan.io/tx/{txs}">View on Solscan</a>\n'
                f"<b>SOL Cost Range</b>\n"
                f"• Min: <code>{nfpf(sniping_setup['min_sol_cost'])} SOL</code>\n"
                f"• Max: <code>{nfpf(sniping_setup['max_sol_cost'])} SOL</code>\n"
                f"<b>Slippage</b>\n• <code>{sniping_setup['slippage']}%</code>\n"
                f"<b>Response Time</b>\n• <code>{nfpf(time_diff)} sec</code>\n\n"
                f"<i>Waiting for Tx Confirmation...</i>"
            )

            # Send message to user
            message = await application.bot.send_message(
                chat_id=user["_id"], text=text, parse_mode="HTML", disable_web_page_preview=True
            )

            # Schedule transaction update
            await schedule_tx_update(
                CallbackContext(application, message.chat_id, user["_id"]),
                message.chat_id,
                message.message_id,
                user["_id"],
                txs,
                mint,
                user["wallet_public"],
            )

            return f"Success {user['_id']}: {txs}"
        else:
            text = (
                f"<b>Failed to execute snipe for {output_amount}!</b>\n"
                "The transaction could not be sent. Please check your settings and try again."
            )

            # Send failure message to user
            await application.bot.send_message(chat_id=user["_id"], text=text, parse_mode="HTML")

            return f"Failure {user['_id']}"

    except Exception as e:
        logger.error(f'Error in snipe_for_user() for user_id "{user['_id']}": {str(e)}')
        return f"Error sniping for user {user['_id']}: {str(e)}"


async def snipe_concurrently(wallet, mint, bonding_curve, associated_bonding_curve, time_diff):
    users = user_db.users.find({"sniping.wallet": wallet})
    sniping_tasks = []

    for user in users:
        for sniping_setup in user["sniping"]:
            if wallet == sniping_setup["wallet"]:
                sniping_tasks.append(
                    snipe_for_user(
                        user,
                        sniping_setup,
                        mint,
                        bonding_curve,
                        associated_bonding_curve,
                        time_diff,
                    )
                )

    results = await asyncio.gather(*sniping_tasks)
    return results


async def unsubscribe():
    logger.info("Starting unscibringinffdkjfodsjfojds")
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
                {"mentionsAccountOrProgram": MINT_AUTHORITY_PUBKEY},
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

        try:
            while True:
                response = await websocket.recv()
                data = json.loads(response)
                if "params" in data:
                    data = parse_block(data)
                    for mint, wallet, time_diff, bonding_curve, associated_bonding_curve in data:
                        logger.info(f'Received mint "{mint}" with time delta of {time_diff:.2f}')
                        wallet = "EiKviBF8WYxqYEoS1QuyoNobs7qTr6GvYftUNzZhakeE"  # testing
                        if wallet not in watched_wallets:
                            continue
                        # Run sniping concurrently for all users with this wallet
                        results = await snipe_concurrently(
                            wallet, mint, bonding_curve, associated_bonding_curve, time_diff
                        )
                        logger.info(f"Sniping results: {results}")

                if "result" in data and "id" in data and data["id"] == 1:
                    subscription_id = data["result"]

        except Exception as e:
            logger.error("An exception has occurred in subscribe_blocks():", e)


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

    refresh_task = asyncio.create_task(refresh_wallet_cache())
    
    try:
        await application._job_queue.start()
        await subscribe_blocks()
    except: 
        pass
    finally:
        refresh_task.cancel()  # Cancel the cache refresh task
        logger.info("Shutdown complete.")