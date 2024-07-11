import json
import os
import struct
import time

import aiohttp
from construct import BitsInteger
from construct import BitsSwapped
from construct import BitStruct
from construct import Bytes
from construct import BytesInteger
from construct import Const
from construct import Flag
from construct import Int64ul
from construct import Int8ul
from construct import Padding
from construct import Struct
from solana.rpc.types import MemcmpOpts
from solana.rpc.types import TokenAccountOpts
from solders.instruction import AccountMeta
from solders.instruction import Instruction
from solders.pubkey import Pubkey
from spl.token.instructions import create_associated_token_account
from spl.token.instructions import get_associated_token_address

from celeritas.constants import aclient
from celeritas.constants import LAMPORTS_PER_SOL


def get_offset(struct, field):
    offset = 0
    for item in struct.subcons:
        if item.name == field:
            return offset
        else:
            offset += item.sizeof()
    return None


AMM_INFO_LAYOUT_V4_1 = Struct(
    "status" / Int64ul,
    "nonce" / Int64ul,
    "orderNum" / Int64ul,
    "depth" / Int64ul,
    "coinDecimals" / Int64ul,
    "pcDecimals" / Int64ul,
    "state" / Int64ul,
    "resetFlag" / Int64ul,
    "minSize" / Int64ul,
    "volMaxCutRatio" / Int64ul,
    "amountWaveRatio" / Int64ul,
    "coinLotSize" / Int64ul,
    "pcLotSize" / Int64ul,
    "minPriceMultiplier" / Int64ul,
    "maxPriceMultiplier" / Int64ul,
    "systemDecimalsValue" / Int64ul,
    #   // Fees
    "minSeparateNumerator" / Int64ul,
    "minSeparateDenominator" / Int64ul,
    "tradeFeeNumerator" / Int64ul,
    "tradeFeeDenominator" / Int64ul,
    "pnlNumerator" / Int64ul,
    "pnlDenominator" / Int64ul,
    "swapFeeNumerator" / Int64ul,
    "swapFeeDenominator" / Int64ul,
    #   // OutPutData
    "needTakePnlCoin" / Int64ul,
    "needTakePnlPc" / Int64ul,
    "totalPnlPc" / Int64ul,
    "totalPnlCoin" / Int64ul,
    "poolOpenTime" / Int64ul,
    "punishPcAmount" / Int64ul,
    "punishCoinAmount" / Int64ul,
    "orderbookToInitTime" / Int64ul,
    "swapCoinInAmount" / BytesInteger(16, signed=False, swapped=True),
    "swapPcOutAmount" / BytesInteger(16, signed=False, swapped=True),
    "swapCoin2PcFee" / Int64ul,
    "swapPcInAmount" / BytesInteger(16, signed=False, swapped=True),
    "swapCoinOutAmount" / BytesInteger(16, signed=False, swapped=True),
    "swapPc2CoinFee" / Int64ul,
    "poolCoinTokenAccount" / Bytes(32),
    "poolPcTokenAccount" / Bytes(32),
    "coinMintAddress" / Bytes(32),
    "pcMintAddress" / Bytes(32),
    "lpMintAddress" / Bytes(32),
    "ammOpenOrders" / Bytes(32),
    "serumMarket" / Bytes(32),
    "serumProgramId" / Bytes(32),
    "ammTargetOrders" / Bytes(32),
    "poolWithdrawQueue" / Bytes(32),
    "poolTempLpTokenAccount" / Bytes(32),
    "ammOwner" / Bytes(32),
    "pnlOwner" / Bytes(32),
)

ACCOUNT_FLAGS_LAYOUT = BitsSwapped(
    BitStruct(
        "initialized" / Flag,
        "market" / Flag,
        "open_orders" / Flag,
        "request_queue" / Flag,
        "event_queue" / Flag,
        "bids" / Flag,
        "asks" / Flag,
        Const(0, BitsInteger(57)),  # Padding
    )
)


MARKET_LAYOUT = Struct(
    Padding(5),
    "account_flags" / ACCOUNT_FLAGS_LAYOUT,
    "own_address" / Bytes(32),
    "vault_signer_nonce" / Int64ul,
    "base_mint" / Bytes(32),
    "quote_mint" / Bytes(32),
    "base_vault" / Bytes(32),
    "base_deposits_total" / Int64ul,
    "base_fees_accrued" / Int64ul,
    "quote_vault" / Bytes(32),
    "quote_deposits_total" / Int64ul,
    "quote_fees_accrued" / Int64ul,
    "quote_dust_threshold" / Int64ul,
    "request_queue" / Bytes(32),
    "event_queue" / Bytes(32),
    "bids" / Bytes(32),
    "asks" / Bytes(32),
    "base_lot_size" / Int64ul,
    "quote_lot_size" / Int64ul,
    "fee_rate_bps" / Int64ul,
    "referrer_rebate_accrued" / Int64ul,
    Padding(7),
)

SWAP_LAYOUT = Struct("instruction" / Int8ul, "amount_in" / Int64ul, "min_amount_out" / Int64ul)

PUMP_FUN_BONDING_CURVE_LAYOUT = Struct(
    Padding(8),
    "virtualTokenReserves" / Int64ul,
    "virtualSolReserves" / Int64ul,
    "realTokenReserves" / Int64ul,
    "realSolReserves" / Int64ul,
    "tokenTotalSupply" / Int64ul,
    "complete" / Flag,
)

RAY_V4 = Pubkey.from_string("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")
RAY_AUTHORITY_V4 = Pubkey.from_string("5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1")
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SERUM_PROGRAM_ID = Pubkey.from_string("srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX")
offset_base_mint = get_offset(AMM_INFO_LAYOUT_V4_1, "coinMintAddress")
offset_quote_mint = get_offset(AMM_INFO_LAYOUT_V4_1, "pcMintAddress")
POOL_ID_CACHE_FILE = "data/pool_ids.json"
POOL_KEYS_CACHE_FILE = "data/pool_keys.json"
JUPITER_TOKENS_CACHE_FILE = "data/jupiter_tokens_cache.json"


async def get_pool_id_by_mint(input_mint, output_mint):
    if os.path.exists(POOL_ID_CACHE_FILE):
        with open(POOL_ID_CACHE_FILE, "r") as f:
            pool_id_cache = json.load(f)
    else:
        pool_id_cache = {}
    cache_key = "".join(sorted([input_mint.__str__(), output_mint.__str__()]))
    if cache_key in pool_id_cache:
        return Pubkey.from_string(pool_id_cache[cache_key])
    inout = [
        MemcmpOpts(offset=offset_base_mint, bytes=bytes(input_mint)),
        MemcmpOpts(offset=offset_quote_mint, bytes=bytes(output_mint)),
    ]
    outin = [
        MemcmpOpts(offset=offset_base_mint, bytes=bytes(output_mint)),
        MemcmpOpts(offset=offset_quote_mint, bytes=bytes(input_mint)),
    ]
    poolids = (
        await aclient.get_program_accounts(
            pubkey=RAY_V4,
            commitment="confirmed",
            encoding="jsonParsed",
            filters=inout,
        )
    ).value
    poolids += (
        await aclient.get_program_accounts(
            pubkey=RAY_V4,
            commitment="confirmed",
            encoding="jsonParsed",
            filters=outin,
        )
    ).value
    if len(poolids):
        pool_id = poolids[0].pubkey
        pool_id_cache[cache_key] = pool_id.__str__()
        with open(POOL_ID_CACHE_FILE, "w") as f:
            json.dump(pool_id_cache, f)
    else:
        pool_id = None

    return pool_id


async def get_bonding_curve(bonding_curve):
    data = (await aclient.get_account_info_json_parsed(bonding_curve, commitment="confirmed")).value.data
    return PUMP_FUN_BONDING_CURVE_LAYOUT.parse(data)


async def get_transaction_keys(amm):
    # Load the cache if it exists
    if os.path.exists(POOL_KEYS_CACHE_FILE):
        with open(POOL_KEYS_CACHE_FILE, "r") as f:
            transaction_keys_cache = json.load(f)
    else:
        transaction_keys_cache = {}

    # Check if the keys for the given AMM are already cached
    if amm.__str__() in transaction_keys_cache:
        cached_keys = transaction_keys_cache[amm.__str__()]
        # Convert string representations back to Pubkey objects
        for key in cached_keys:
            if key not in ["base_decimals", "quote_decimals"]:
                cached_keys[key] = Pubkey.from_string(cached_keys[key])
        return cached_keys

    # Fetch and decode the data from the blockchain
    amm_data = (await aclient.get_account_info_json_parsed(amm)).value.data
    amm_data_decoded = AMM_INFO_LAYOUT_V4_1.parse(amm_data)
    OPEN_BOOK_PROGRAM = Pubkey.from_bytes(amm_data_decoded.serumProgramId)
    marketId = Pubkey.from_bytes(amm_data_decoded.serumMarket)
    market_data = (await aclient.get_account_info_json_parsed(marketId)).value.data
    market_decoded = MARKET_LAYOUT.parse(market_data)
    pool_keys = {
        "amm_id": amm,
        "base_mint": Pubkey.from_bytes(market_decoded.base_mint),
        "quote_mint": Pubkey.from_bytes(market_decoded.quote_mint),
        "lp_mint": Pubkey.from_bytes(amm_data_decoded.lpMintAddress),
        "version": 4,
        "base_decimals": amm_data_decoded.coinDecimals,
        "quote_decimals": amm_data_decoded.pcDecimals,
        "lpDecimals": amm_data_decoded.coinDecimals,
        "programId": RAY_V4,
        "authority": RAY_AUTHORITY_V4,
        "open_orders": Pubkey.from_bytes(amm_data_decoded.ammOpenOrders),
        "target_orders": Pubkey.from_bytes(amm_data_decoded.ammTargetOrders),
        "base_vault": Pubkey.from_bytes(amm_data_decoded.poolCoinTokenAccount),
        "quote_vault": Pubkey.from_bytes(amm_data_decoded.poolPcTokenAccount),
        "withdrawQueue": Pubkey.from_bytes(amm_data_decoded.poolWithdrawQueue),
        "lpVault": Pubkey.from_bytes(amm_data_decoded.poolTempLpTokenAccount),
        "marketProgramId": OPEN_BOOK_PROGRAM,
        "market_id": marketId,
        "market_authority": Pubkey.create_program_address(
            [bytes(marketId)] + [bytes([market_decoded.vault_signer_nonce])] + [bytes(7)],
            OPEN_BOOK_PROGRAM,
        ),
        "market_base_vault": Pubkey.from_bytes(market_decoded.base_vault),
        "market_quote_vault": Pubkey.from_bytes(market_decoded.quote_vault),
        "bids": Pubkey.from_bytes(market_decoded.bids),
        "asks": Pubkey.from_bytes(market_decoded.asks),
        "event_queue": Pubkey.from_bytes(market_decoded.event_queue),
        "pool_open_time": amm_data_decoded.poolOpenTime,
    }

    Buy_keys = [
        "amm_id",
        "authority",
        "base_mint",
        "base_decimals",
        "quote_mint",
        "quote_decimals",
        "lp_mint",
        "open_orders",
        "target_orders",
        "base_vault",
        "quote_vault",
        "market_id",
        "market_base_vault",
        "market_quote_vault",
        "market_authority",
        "bids",
        "asks",
        "event_queue",
    ]

    transactionkeys = {key: pool_keys[key] for key in Buy_keys}
    serializable_keys = {}
    for key, value in transactionkeys.items():
        if isinstance(value, Pubkey):
            serializable_keys[key] = str(value)
        else:
            serializable_keys[key] = value

    # Cache the fetched data
    transaction_keys_cache[amm.__str__()] = serializable_keys
    with open(POOL_KEYS_CACHE_FILE, "w") as f:
        json.dump(transaction_keys_cache, f)

    return transactionkeys


async def is_jupiter_token(mint: str) -> bool:
    # Load the cache if it exists
    if os.path.exists(JUPITER_TOKENS_CACHE_FILE):
        with open(JUPITER_TOKENS_CACHE_FILE, "r") as f:
            cache = json.load(f)
        # Check if the cache is less than 24 hours old
        if (time.time() - cache["timestamp"]) < 3600:  # 3600 seconds = 1 hours
            return mint in cache["tokens"]

    # Fetch the list of tokens from Jupiter API
    async with aiohttp.ClientSession() as session:
        async with session.get("https://token.jup.ag/all") as response:
            if response.status == 200:
                tokens = await response.json()
            else:
                raise Exception(f"Failed to fetch Jupiter tokens: HTTP {response.status}")

    # Extract mint addresses
    token_mints = {token["address"] for token in tokens}

    # Update the cache
    cache = {"timestamp": time.time(), "tokens": list(token_mints)}
    with open(JUPITER_TOKENS_CACHE_FILE, "w") as f:
        json.dump(cache, f)

    # Check if the given mint is in the list
    return mint in token_mints


async def get_token_account(
    owner: Pubkey.from_string, mint: Pubkey.from_string, payer: Pubkey.from_string = None
):
    try:
        account_data = await aclient.get_token_accounts_by_owner(owner, TokenAccountOpts(mint))
        return account_data.value[0].pubkey, None
    except:
        swap_associated_token_address = get_associated_token_address(owner, mint)
        swap_token_account_Instructions = create_associated_token_account(
            (payer if payer else owner), owner, mint  # payer  # owner
        )
        return swap_associated_token_address, swap_token_account_Instructions


async def get_quote_info_from_pool(token_in, token_in_amount, amm):
    """
    args:
      `token_in`: Pubkey of token to buy
      `token_in_amount`: Amount of token_in, divided by decimals, to be bought
      `amm`: Pubkey of AMM
    returs:
      {"current_price": float (SOL), "price_inpact": float, "token_amount_out": float}
    """
    # base_vault, quote_vault
    keys = await get_transaction_keys(amm)
    if token_in == keys["base_mint"]:
        in_vault, out_vault = keys["base_vault"], keys["quote_vault"]
    else:
        in_vault, out_vault = keys["quote_vault"], keys["base_vault"]
    in_vault_balance = await aclient.get_token_account_balance(in_vault)
    out_vault_balance = await aclient.get_token_account_balance(out_vault)

    # Number of tokens in each vault, divided by decimals
    in_balance = float(in_vault_balance.value.ui_amount_string)
    out_balance = float(out_vault_balance.value.ui_amount_string)
    # Current price, meaning how many of ouput token do I get for one input token
    current_price = out_balance / in_balance
    token_amount_out = -(in_balance * out_balance) / (in_balance + token_in_amount) + out_balance

    price_inpact = round(1 - (token_amount_out / (token_in_amount * current_price)), 4)
    return {
        "current_price": current_price,
        "price_inpact": price_inpact,
        "token_amount_out": token_amount_out,
    }


def make_swap_instruction(
    amount_in: int,
    token_account_in: Pubkey.from_string,
    token_account_out: Pubkey.from_string,
    accounts: dict,
    owner,
    min_amount_out=0,
) -> Instruction:
    keys = [
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accounts["amm_id"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["authority"], is_signer=False, is_writable=False),
        AccountMeta(pubkey=accounts["open_orders"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["target_orders"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["base_vault"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["quote_vault"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=SERUM_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=accounts["market_id"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["bids"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["asks"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["event_queue"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["market_base_vault"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["market_quote_vault"], is_signer=False, is_writable=True),
        AccountMeta(pubkey=accounts["market_authority"], is_signer=False, is_writable=False),
        AccountMeta(pubkey=token_account_in, is_signer=False, is_writable=True),  # UserSourceTokenAccount
        AccountMeta(pubkey=token_account_out, is_signer=False, is_writable=True),  # UserDestTokenAccount
        AccountMeta(pubkey=owner.pubkey(), is_signer=True, is_writable=False),  # UserOwner
    ]

    data = SWAP_LAYOUT.build(dict(instruction=9, amount_in=int(amount_in), min_amount_out=min_amount_out))
    return Instruction(RAY_V4, data, keys)


GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
FEE_RECIPIENT = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOC_TOKEN_ACC_PROG = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
RENT = Pubkey.from_string("SysvarRent111111111111111111111111111111111")
PUMP_FUN_ACCOUNT = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
PUMP_FUN_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")


async def make_pump_fun_buy_instruction(
    amount_in,
    slippage_bps,
    output_mint,
    owner_pubkey,
    owner_token_account,
    bonding_curve,
    associated_bonding_curve,
):
    slippage_bps = max(300, slippage_bps)
    sol_in_lamports = int(amount_in * LAMPORTS_PER_SOL)
    bc = await get_bonding_curve(bonding_curve)
    K = bc.virtualSolReserves * bc.virtualTokenReserves
    token_out = -K / (sol_in_lamports + bc.virtualSolReserves) + bc.virtualTokenReserves
    # Build account key list
    keys = [
        AccountMeta(pubkey=GLOBAL, is_signer=False, is_writable=False),
        AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
        AccountMeta(pubkey=output_mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner_pubkey, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FUN_ACCOUNT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False),
    ]
    token_out_with_slippage = int(token_out / (1 + (slippage_bps / 10000)))
    # Define integer values
    buy = 16927863322537952870
    integers = [buy, token_out_with_slippage, sol_in_lamports]  # adding slippage
    # Pack integers into binary segments
    binary_segments = [struct.pack("<Q", integer) for integer in integers]
    data = b"".join(binary_segments)

    current_price = (bc.virtualTokenReserves / (10**6)) / (bc.virtualSolReserves / (LAMPORTS_PER_SOL))
    return Instruction(PUMP_FUN_PROGRAM, data, keys), {
        "current_price": current_price,
        "price_inpact": round(((amount_in * current_price) / (token_out / 10**6) - 1), 4),
        "token_amount_out": token_out_with_slippage / 10**6,
        "min_token_amount_out": token_out_with_slippage / 10**6,
    }


async def make_pump_fun_snipe_instruction(
    max_sol_cost,
    output_amount,
    output_mint,
    owner_pubkey,
    owner_token_account,
    bonding_curve,
    associated_bonding_curve,
):
    # Build account key list
    keys = [
        AccountMeta(pubkey=GLOBAL, is_signer=False, is_writable=False),
        AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
        AccountMeta(pubkey=output_mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner_pubkey, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FUN_ACCOUNT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False),
    ]
    # Define integer values
    buy = 16927863322537952870
    tokens_out = int(output_amount * (10**6))
    sol_in_lamports = int(max_sol_cost * LAMPORTS_PER_SOL)
    integers = [buy, tokens_out, sol_in_lamports]
    # Pack integers into binary segments
    binary_segments = [struct.pack("<Q", integer) for integer in integers]
    data = b"".join(binary_segments)

    return Instruction(PUMP_FUN_PROGRAM, data, keys), {
        "current_price": 0.00000003,
        "price_inpact": None,
        "token_amount_out": output_amount,
        "min_token_amount_out": output_amount,
    }


async def make_pump_fun_sell_instruction(
    amount_in,
    slippage_bps,
    input_mint,
    owner_pubkey,
    owner_token_account,
    bonding_curve,
    associated_bonding_curve,
):
    slippage_bps = max(300, slippage_bps)  # duy to problems with calculating bonding curve
    tokens_in = int(amount_in * (10**6))
    bc = await get_bonding_curve(bonding_curve)
    K = bc.virtualSolReserves * bc.virtualTokenReserves
    min_lamports_out = -K / (tokens_in + bc.virtualTokenReserves) + bc.virtualSolReserves
    # Build account key list
    keys = [
        AccountMeta(pubkey=GLOBAL, is_signer=False, is_writable=False),
        AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
        AccountMeta(pubkey=input_mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner_pubkey, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=ASSOC_TOKEN_ACC_PROG, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FUN_ACCOUNT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False),
    ]
    min_lamports_out_with_slippage = int(min_lamports_out / (1 + (slippage_bps / 10000)))
    # magic number as sell instruction
    sell = 12502976635542562355
    integers = [sell, tokens_in, min_lamports_out_with_slippage]  # adding slippage
    # Pack integers into binary segments
    binary_segments = [struct.pack("<Q", integer) for integer in integers]
    data = b"".join(binary_segments)

    current_price = (bc.virtualSolReserves / (LAMPORTS_PER_SOL)) / (bc.virtualTokenReserves / (10**6))
    return Instruction(PUMP_FUN_PROGRAM, data, keys), {
        "current_price": current_price,
        "price_inpact": ((amount_in * current_price) / (min_lamports_out / LAMPORTS_PER_SOL) - 1),
        "token_amount_out": min_lamports_out / LAMPORTS_PER_SOL,
        "min_token_amount_out": min_lamports_out_with_slippage / LAMPORTS_PER_SOL,
    }
