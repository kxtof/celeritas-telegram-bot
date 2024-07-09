import httpx
import base64
import os
from celeritas.telegram_bot.utils import get_blockhash
from celeritas.config import config
from celeritas.db import TokenDB
from celeritas.constants import (
    client,
    aclient,
    WRAPPED_SOL,
    SOLANA_MINT,
    LAMPORTS_PER_SOL,
)
from celeritas.transact_utils import (
    make_swap_instruction,
    get_pool_id_by_mint,
    get_transaction_keys,
    get_token_account,
    TOKEN_PROGRAM_ID,
    get_quote_info_from_pool,
    make_pump_fun_buy_instruction,
    make_pump_fun_sell_instruction,
    make_pump_fun_snipe_instruction,
    is_jupiter_token,
)
from jupiter_python_sdk.jupiter import Jupiter
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.transaction import VersionedTransaction
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solana.rpc.types import TokenAccountOpts, TxOpts
from solana.rpc.commitment import Commitment
from spl.token.core import _TokenCore
from spl.token.instructions import (
    create_associated_token_account,
    get_associated_token_address,
    close_account,
    CloseAccountParams,
    close_account, 
    CloseAccountParams
)
from typing import List
 

tokendb = TokenDB()


class Transact:
    """
    methods 'buy', 'sell', 'swap_jupiter' and 'swap_raydium_amm' return a quote with:
      current_price,
      price_inpact,
      token_amount_out,
      instructions,
      keypairs
    this is all that is need for the user to decide if he wants to go ahead with the transaction,
    and also everything needed to actually sign and send the transaction
    {
        "quote": {
            "current_price": float,    # how many out-tokens do i get for each in-token
            "price_inpact": float,     # [0; ∞)
            "token_amount_out": float,
            "min_token_amount_out": float,
        }
        "instructions": List[Instruction],
        "keypairs": List[Keypair]
    }
    """

    def __init__(
        self,
        wallet_secret: str,
        platform_fee_pubkey: str=None,
        platform_fee_bps: int=50,
        fee_sol: float=0.00007,
    ):
        self.compute_unit_limit = 200_000
        self.compute_unit_price = int(
            fee_sol * 10**15 / self.compute_unit_limit
        )  # unit price is in microlamports, sol is 10**9 lamports, lamport is 10**6 microlamports
        self.keypair = Keypair.from_base58_string(wallet_secret)
        self.platform_fee_bps = platform_fee_bps
        self.jupiter = Jupiter(
            async_client=aclient,
            keypair=self.keypair,
        )
        if platform_fee_pubkey:
            self.platform_fee_pubkey = Pubkey.from_string(platform_fee_pubkey)
        try:
            self.platform_fee_pubkey = Pubkey.from_string(config.platform_fee_pubkey)
        except:
            raise Exception("Missing platform fee pubkey!")

    async def _get_jupiter_quote(
        self, input_mint, output_mint, amount, slippage_bps=50, max_accounts=None
    ):
        try:
            return await self.jupiter.quote(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=amount,
                slippage_bps=slippage_bps,
                max_accounts=max_accounts,
            )
        except:
            # raise Exception("Failed retrieving Jupiter quote.")
            return None

    async def _create_transaction(
        self, quote, fee=None
    ):
        ixs = [
            set_compute_unit_price(self.compute_unit_price),
            set_compute_unit_limit(self.compute_unit_limit),
        ] + list(quote['instructions'])
        # Add platform transaction fee
        if self.platform_fee_pubkey and fee:
            ixs.append(
                transfer(
                    TransferParams(
                        from_pubkey=self.keypair.pubkey(),
                        to_pubkey=self.platform_fee_pubkey,
                        lamports=int(fee*LAMPORTS_PER_SOL)
                    )
                )
            )
        recent_blockhash = get_blockhash()
        compiled_message = MessageV0.try_compile(
            self.keypair.pubkey(),
            ixs,
            [],
            recent_blockhash,
        )
        return VersionedTransaction(compiled_message, quote['keypairs'])

    async def _get_token_account(self, mint: Pubkey):
        try:
            return (
                (
                    await aclient.get_token_accounts_by_owner(
                        self.keypair.pubkey(), TokenAccountOpts(mint)
                    )
                )
                .value[0]
                .pubkey
            )
        except Exception as e:
            raise Exception(f"Failed retrieving token account for mint: {mint}")

    def _close_account_ix(self, acc: Pubkey) -> Instruction:
        return close_account(
            CloseAccountParams(
                account=acc,
                dest=self.keypair.pubkey(),
                owner=self.keypair.pubkey(),
                program_id=TOKEN_PROGRAM_ID,
            )
        )

    async def _create_jupiter_instructions(self, quote) -> List[Instruction]:
        transaction_parameters = {
            "quoteResponse": quote,
            "userPublicKey": self.keypair.pubkey().__str__(),
            "wrapAndUnwrapSol": True,
        }
        ix_data = httpx.post(
            url="https://quote-api.jup.ag/v6/swap-instructions",
            json=transaction_parameters,
            timeout=10,
        ).json()
        instructions = []
        for ix in (
            ix_data["setupInstructions"]
            + [ix_data["swapInstruction"]]
            + [ix_data["cleanupInstruction"]]
        ):
            meta_accounts = [
                AccountMeta(
                    pubkey=Pubkey.from_string(account["pubkey"]),
                    is_signer=account["isSigner"],
                    is_writable=account["isWritable"],
                )
                for account in ix["accounts"]
            ]
            instruction = Instruction(
                Pubkey.from_string(ix["programId"]),
                base64.b64decode(ix["data"]),
                meta_accounts,
            )
            instructions.append(instruction)
        return instructions

    async def _prepare_jupiter_trade(self, quote, mint_decimals, buy=True):
        instructions = await self._create_jupiter_instructions(quote)
        if buy:
            current_price = (int(quote["outAmount"]) / (10**mint_decimals)) / (
                int(quote["inAmount"]) / LAMPORTS_PER_SOL
            )
            token_amount_out = int(quote["outAmount"]) / (10**mint_decimals)
            min_token_amount_out = int(quote["otherAmountThreshold"]) / (10**mint_decimals)
        else:
            current_price = (int(quote["outAmount"]) / LAMPORTS_PER_SOL) / (
                int(quote["inAmount"]) / (10**mint_decimals)
            )
            token_amount_out = int(quote['outAmount']) / LAMPORTS_PER_SOL
            min_token_amount_out = int(quote["otherAmountThreshold"]) / LAMPORTS_PER_SOL
        return {
            "quote": {
                "current_price": current_price,
                "price_inpact": quote["priceImpactPct"],
                "token_amount_out": int(quote["outAmount"]) / LAMPORTS_PER_SOL,
                "min_token_amount_out": min_token_amount_out,
            },
            "instructions": instructions,
            "keypairs": [self.keypair],
        }

    async def swap_jupiter(self, input_mint, output_mint, amount, slippage_bps=50):
        """
        input_mint: input SPL token mint
        output_mint: output SPL token mint
        amount: amount of input SPL token, can be float
        slippage_bps: each basis point is a hundreth of a %, i.e. 50 is 0.5% slippage
        """
        out_token = await tokendb.add_token(output_mint.__str__())
        out_decimals = out_token["decimals"]
        in_token = await tokendb.add_token(input_mint.__str__())
        in_decimals = in_token["decimals"]
        amount = int(amount * (10**in_decimals))
        quote = await self._get_jupiter_quote(
            input_mint, output_mint, amount, slippage_bps, max_accounts=30
        )
        instructions = await self._create_jupiter_instructions(quote)
        current_price = (int(quote["outAmount"]) / (10**out_decimals)) / (
            int(quote["inAmount"]) / (10**in_decimals)
        )
        return {
            "quote": {
                "current_price": current_price,
                "price_inpact": quote["priceImpactPct"],
                "token_amount_out": int(quote["outAmount"]) / (10**out_decimals),
            },
            "instructions": instructions,
            "keypairs": [self.keypair],
        }

    async def _get_raydium_parameters(
        self, input_mint, output_mint, amount, slippage_bps
    ):
        amm = await get_pool_id_by_mint(input_mint, output_mint)
        if not amm:
            return None
            # raise Exception("Failed fetching AMM pool.")
        else:
            transaction_keys = await get_transaction_keys(amm)
        quote = await get_quote_info_from_pool(input_mint, amount, amm)
        quote["min_token_amount_out"] = quote["token_amount_out"] / (
            1 + (slippage_bps / 10000)
        )
        amount = int(
            amount * (10 ** await tokendb.get_token_decimals(input_mint.__str__()))
        )
        min_amount_out = int(
            quote["min_token_amount_out"]
            * (10 ** await tokendb.get_token_decimals(output_mint.__str__()))
        )
        swap_token_account, swap_token_account_ix = await get_token_account(
            self.keypair.pubkey(), output_mint
        )
        return (
            amount,
            min_amount_out,
            quote,
            swap_token_account,
            swap_token_account_ix,
            transaction_keys,
        )

    async def buy_raydium_amm(self, output_mint, amount, slippage_bps=50):
        output_mint = Pubkey.from_string(output_mint)
        (
            amount,
            min_amount_out,
            quote,
            swap_token_account,
            swap_token_account_ix,
            transaction_keys,
        ) = await self._get_raydium_parameters(
            WRAPPED_SOL, output_mint, amount, slippage_bps
        )
        wsol_token_account, swap_tx, payer, wsol_account_keypair, opts = (
            _TokenCore._create_wrapped_native_account_args(
                TOKEN_PROGRAM_ID,
                self.keypair.pubkey(),
                self.keypair,
                amount,
                False,
                2039280,  # Balance needed to keep a rent exempt account
                Commitment("confirmed"),
            )
        )
        close_acc_ix = self._close_account_ix(wsol_token_account)
        swap_ix = make_swap_instruction(
            amount,
            wsol_token_account,
            swap_token_account,
            transaction_keys,
            self.keypair,
            min_amount_out=min_amount_out,
        )
        # Construct tx
        if swap_token_account_ix:
            swap_tx.add(swap_token_account_ix)
        swap_tx.add(swap_ix)
        swap_tx.add(close_acc_ix)
        return {
            "quote": quote,
            "instructions": swap_tx.instructions,
            "keypairs": [self.keypair, wsol_account_keypair],
        }

    async def sell_raydium_amm(self, input_mint, amount, slippage_bps=50):
        input_mint = Pubkey.from_string(input_mint)
        (
            amount,
            min_amount_out,
            quote,
            swap_token_account,
            swap_token_account_ix,
            transaction_keys,
        ) = await self._get_raydium_parameters(
            input_mint, WRAPPED_SOL, amount, slippage_bps
        )
        input_token_account = await self._get_token_account(input_mint)
        swap_ix = make_swap_instruction(
            amount,
            input_token_account,
            swap_token_account,
            transaction_keys,
            self.keypair,
            min_amount_out=min_amount_out,
        )
        close_acc_ix = self._close_account_ix(swap_token_account)
        # Construct tx
        ixs = []
        if swap_token_account_ix:
            ixs.append(swap_token_account_ix)
        ixs.append(swap_ix)
        # Unwrap output SOL after swap
        ixs.append(close_acc_ix)
        return {"quote": quote, "instructions": ixs, "keypairs": [self.keypair]}

    async def buy_pump_fun(self, output_mint, amount, slippage_bps, token):
        output_mint = Pubkey.from_string(output_mint)
        # the funnction has to check for token account existence, create it if not existent
        swap_token_account, swap_token_account_ix = await get_token_account(
            self.keypair.pubkey(), output_mint
        )
        ixs = []
        if swap_token_account_ix:
            ixs.append(swap_token_account_ix)
        ix, quote = await make_pump_fun_buy_instruction(
            amount,
            slippage_bps,
            output_mint,
            self.keypair.pubkey(),
            swap_token_account,
            Pubkey.from_string(token["pump_fun_data"]["bonding_curve"]),
            Pubkey.from_string(token["pump_fun_data"]["associated_bonding_curve"]),
        )
        ixs.append(ix)
        return {"quote": quote, "instructions": ixs, "keypairs": [self.keypair]}

    async def snipe_pump_fun(self, output_mint, output_amount, max_sol_cost, bonding_curve, associated_bonding_curve):
        """ Create a pump.fun buy instruction without fetching anything through RPC """
        output_mint = Pubkey.from_string(output_mint)
        swap_token_account = get_associated_token_address(self.keypair.pubkey(), output_mint)
        swap_token_account_ix = create_associated_token_account(
            self.keypair.pubkey(),
            self.keypair.pubkey(),
            output_mint
        )
        ixs = []
        ixs.append(swap_token_account_ix)
        ix, quote = await make_pump_fun_snipe_instruction(
            max_sol_cost,
            output_amount,
            output_mint,
            self.keypair.pubkey(),
            swap_token_account,
            Pubkey.from_string(bonding_curve),
            Pubkey.from_string(associated_bonding_curve),
        )
        ixs.append(ix)
        return {"quote": quote, "instructions": ixs, "keypairs": [self.keypair]}

    async def sell_pump_fun(self, input_mint, amount, slippage_bps, token):
        input_mint = Pubkey.from_string(input_mint)
        swap_token_account, _ = await get_token_account(
            self.keypair.pubkey(), input_mint
        )
        ixs = []
        ix, quote = await make_pump_fun_sell_instruction(
            amount,
            slippage_bps,
            input_mint,
            self.keypair.pubkey(),
            swap_token_account,
            Pubkey.from_string(token["pump_fun_data"]["bonding_curve"]),
            Pubkey.from_string(token["pump_fun_data"]["associated_bonding_curve"]),
        )
        ixs.append(ix)
        return {"quote": quote, "instructions": ixs, "keypairs": [self.keypair]}

    async def buy(self, mint, amount, slippage_bps=50):
        token = await tokendb.add_token(mint)
        
        # Check if it's a pump.fun token
        if token["is_pump_fun"]:
            if not token["pump_fun_data"]["bonding_curve_complete"]:
                return await self.buy_pump_fun(mint, amount, slippage_bps, token)
        # Check if it's a Jupiter-supported token
        if await is_jupiter_token(mint):
            mint_decimals = token["decimals"]
            quote = await self._get_jupiter_quote(
                SOLANA_MINT,
                mint,
                int(amount * LAMPORTS_PER_SOL),
                slippage_bps,
                max_accounts=30,
            )
            if quote:
                return await self._prepare_jupiter_trade(
                    quote, mint_decimals, buy=True
                )
        # If not Jupiter-supported or Jupiter quote failed, go through Raydium
        return await self.buy_raydium_amm(mint, amount, slippage_bps=slippage_bps)

    async def sell(self, mint, amount, slippage_bps=50):
        token = await tokendb.add_token(mint)
        
        # Check if it's a pump.fun token
        if token["is_pump_fun"]:
            if not token["pump_fun_data"]["bonding_curve_complete"]:
                return await self.sell_pump_fun(mint, amount, slippage_bps, token)
        # Try to get a Jupiter quote
        if await is_jupiter_token(mint):  
            mint_decimals = token["decimals"]
            quote = await self._get_jupiter_quote(
                mint,
                SOLANA_MINT,
                int(amount * 10**mint_decimals),
                slippage_bps,
                max_accounts=30,
            )
            if quote:
                return await self._prepare_jupiter_trade(
                    quote, mint_decimals, buy=False
                )
        # If not Jupiter-supported or Jupiter quote failed, go through Raydium    
        return await self.sell_raydium_amm(mint, amount, slippage_bps=slippage_bps)

    async def sell_percentage(self, mint, percentage, slippage_bps=50):
        """
        mint: str
        owner: str
        percentage: float ∈ [1; 100]
        slippage_bps: float
        """
        percentage = max(1, percentage)
        percentage = min(100, percentage)
        q = client.get_token_accounts_by_owner_json_parsed(
            self.keypair.pubkey(),
            TokenAccountOpts(
                program_id=Pubkey.from_string(
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                )
            ),
        )
        holdings = {}
        for a in q.value:
            if a.account.data.parsed["info"]["mint"] != mint:
                continue
            quote = await self.sell(
                mint,
                (percentage / 100)
                * a.account.data.parsed["info"]["tokenAmount"]["uiAmount"],
                slippage_bps=slippage_bps,
            )
            if percentage == 100:
                quote["instructions"].append(self._close_account_ix(a.pubkey))
            return quote
        return None
    
    async def construct_and_send(self, quote, fee):
        try:
            tx = await self._create_transaction(
                quote,
                fee=fee # add transaction fee in sol
            )
            txs = await aclient.send_transaction(tx, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
            return txs.value
        except Exception as e:
            print(e)
            return None