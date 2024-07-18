import time
from typing import List

import aiohttp
import pymongo
from solana.rpc.types import TokenAccountOpts
from solders.pubkey import Pubkey

from celeritas.config import config
from celeritas.constants import client
from celeritas.constants import LAMPORTS_PER_SOL
from celeritas.constants import WRAPPED_SOL
from celeritas.get_token_metadata import get_metadata
from celeritas.get_token_metadata import get_token_supply
from celeritas.telegram_bot.utils import sol_dollar_value
from celeritas.transact_utils import get_bonding_curve
from celeritas.transact_utils import get_pool_id_by_mint
from celeritas.transact_utils import get_quote_info_from_pool
from celeritas.user import User
from celeritas.user_settings import User_settings


class UserDB:
    def __init__(self, host: str = config.mongodb_url):
        self.client = pymongo.MongoClient(host)
        self.users = self.client["celeritas"]["users"]
        self.users.create_index([("sniping.wallet", pymongo.ASCENDING)])

    def user_exists(self, id: int) -> bool:
        return self.users.find_one({"_id": id}) is not None

    def get_user(self, id: int) -> User:
        user_data = self.users.find_one({"_id": id})
        if user_data is None:
            # user = User(id=id)
            # self.users.insert_one(user.to_dict())
            # user_data = user.to_dict()
            pass
        return User.from_dict(user_data) if user_data else None

    def update_attribute(self, id: int, attribute: str, new_value) -> None:
        if not self.user_exists(id):
            raise ValueError(f"User {id} does not exist in db.")
        if attribute not in User().to_dict():
            raise ValueError(f"Attribute {attribute} not in celeritas.user.User.")
        self.users.update_one({"_id": id}, {"$set": {attribute: new_value}})

    def get_attribute(self, id: int, attribute: str) -> None:
        if not self.user_exists(id):
            raise ValueError(f"User {id} does not exist in db.")
        user_data = self.get_user(id).to_dict()
        if attribute not in user_data:
            raise ValueError(f"Attribute {attribute} not in celeritas.user.User.")
        return user_data[attribute]

    def get_user_settings(
        self,
        id: int,
    ) -> User_settings:
        if not self.user_exists(id):
            raise ValueError(f"User {id} does not exist in db.")
        user_settings = self.get_user(id).settings
        return user_settings

    def update_user_settings(self, id: int, attribute: str, value) -> User_settings:
        if not self.user_exists(id):
            raise ValueError(f"User {id} does not exist in db.")
        if attribute not in User().settings.to_dict():
            print(attribute, User().settings.to_dict())
            raise ValueError(f"Attribute {attribute} not in celeritas.user.User.settings")
        self.users.update_one({"_id": id}, {"$set": {f"settings.{attribute}": value}})
        return self.get_user_settings(id)

    def add_user(self, user: User, override: bool = False) -> None:
        if self.user_exists(user.id):
            if not override:
                raise Exception(f'User already in db. To add anyways pass "override=True".')
            else:
                self.delete_user(user.id)
        self.users.insert_one(user.to_dict())

    def delete_user(self, id: int) -> None:
        # Check if the user exists
        # if not self.user_exists(id):
        #    raise ValueError(f"User {id} does not exist in db.")
        self.users.delete_one({"_id": id})

    def update_user_holdings(self, id: int) -> None:
        """Download and update holdings of user"""
        pk = self.get_attribute(id, "wallet_public")
        opts = TokenAccountOpts(
            program_id=(Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"))
        )
        q = client.get_token_accounts_by_owner_json_parsed(Pubkey.from_string(pk), opts)
        holdings = {}
        for a in q.value:
            amount = a.account.data.parsed["info"]["tokenAmount"]["uiAmount"]
            token = a.account.data.parsed["info"]["mint"]
            if amount:
                holdings[token] = amount
        self.update_attribute(id, "holdings", holdings)
        self.update_sol_balance(id)
        return holdings

    def update_user_positions(self, id: int, update_holdings=False, get_prices=False) -> dict:
        holdings = self.update_user_holdings(id) if update_holdings else self.get_attribute(id, "holdings")
        transactions = self.get_attribute(id, "transactions")
        current_prices = {}

        if get_prices:
            tokens = self.client["celeritas"]["tokens"].find(
                {"_id": {"$in": list(holdings.keys())}}, {"price_dollars": 1}
            )
            current_prices = {token["_id"]: token.get("price_dollars") for token in tokens}

        positions = {}
        for token, balance in holdings.items():
            position = {
                k: 0
                for k in [
                    "balance",
                    "avg_entry_sol",
                    "avg_entry_usd",
                    "n_buys",
                    "n_sells",
                    "realized_pnl_usd",
                    "realized_pnl_sol",
                    "realized_pnl_percentage_usd",
                    "realized_pnl_percentage_sol",
                    "unrealized_pnl_usd",
                    "unrealized_pnl_sol",
                    "unrealized_pnl_percentage_usd",
                    "unrealized_pnl_percentage_sol",
                ]
            }
            position["balance"] = balance

            total_bought = {"sol": 0, "usd": 0, "tokens": 0}
            total_sold = {"sol": 0, "usd": 0, "tokens": 0}
            current_token_balance = 0

            for tx in sorted(
                filter(lambda x: x["mint"] == token, transactions), key=lambda x: x["timestamp"]
            ):
                token_delta = tx["post_token_balance"] - tx["pre_token_balance"]
                sol_delta = tx["post_sol_balance"] - tx["pre_sol_balance"]
                usd_delta = sol_delta * tx["sol_dollar_value"]

                if token_delta > 0:  # Buy
                    position["n_buys"] += 1
                    total_bought["sol"] -= sol_delta
                    total_bought["usd"] -= usd_delta
                    total_bought["tokens"] += token_delta
                    current_token_balance += token_delta
                elif token_delta < 0:  # Sell
                    position["n_sells"] += 1
                    total_sold["sol"] += sol_delta
                    total_sold["usd"] += usd_delta
                    sold_tokens = abs(token_delta)
                    total_sold["tokens"] += sold_tokens
                    current_token_balance -= sold_tokens

                    if current_token_balance > 0:
                        sold_ratio = sold_tokens / (current_token_balance + sold_tokens)
                        for key in total_bought:
                            total_bought[key] -= total_bought[key] * sold_ratio
                    else:
                        total_bought = {key: 0 for key in total_bought}

            if total_bought["tokens"] > 0:
                position["avg_entry_sol"] = total_bought["sol"] / total_bought["tokens"]
                position["avg_entry_usd"] = total_bought["usd"] / total_bought["tokens"]

            if total_sold["tokens"] > 0:
                avg_sell_price = {
                    "sol": total_sold["sol"] / total_sold["tokens"],
                    "usd": total_sold["usd"] / total_sold["tokens"],
                }
                for currency in ["sol", "usd"]:
                    position[f"realized_pnl_{currency}"] = total_sold[currency] - (
                        total_sold["tokens"] * position[f"avg_entry_{currency}"]
                    )
                    position[f"realized_pnl_percentage_{currency}"] = (
                        (avg_sell_price[currency] / position[f"avg_entry_{currency}"] - 1) * 100
                        if position[f"avg_entry_{currency}"]
                        else 0
                    )
            if get_prices and token in current_prices:
                current_price_usd = current_prices[token]
                current_price_sol = current_price_usd / sol_dollar_value()
                for currency, price in [("usd", current_price_usd), ("sol", current_price_sol)]:
                    position[f"unrealized_pnl_{currency}"] = (
                        price - position[f"avg_entry_{currency}"]
                    ) * current_token_balance
                    position[f"unrealized_pnl_percentage_{currency}"] = (
                        ((price / position[f"avg_entry_{currency}"]) - 1) * 100
                        if position[f"avg_entry_{currency}"]
                        else 0
                    )

            positions[token] = position

        self.update_attribute(id, "positions", positions)
        return positions

    def update_sol_balance(self, id: int) -> float:
        if not self.user_exists:
            raise ValueError(f"User {id} does not exist in db.")
        pk = self.get_attribute(id, "wallet_public")
        balance = client.get_balance(Pubkey.from_string(pk)).value
        self.users.update_one({"_id": id}, {"$set": {"sol_in_wallet": balance / LAMPORTS_PER_SOL}})
        return balance


class TokenDB:
    token_prototype = {
        "mint": None,
        "is_pump_fun": True,
        "pump_fun_data": {  # None if is_pump_fun is False
            "bonding_curve": None,  # pubkey of bonding curve
            "associated_bonding_curve": None,  # associated bonding curve for buy/sell
            "bonding_curve_progress": None,  # float percentage of bonding curve
            "bonding_curve_price_sol": None,  # pump.fun prices natively in SOL
            "bonding_curve_complete": False,  # When True, pump_fun_data stops being relevant
        },
        "price_dollars": None,
        "market_cap_dollars": None,
        "decimals": None,
        "name": None,
        "symbol": None,
        "description": None,
        "metadata_uri": None,
        "is_mutable": None,
        "supply": None,  # Without decimals, in base form (int)
        "refresh_timestamp": None,
        "price_history": [],  # List of {timestamp: int, price: float}
        "price_change": {"5m": 0.0, "30m": 0.0, "24h": 0.0},
    }

    def __init__(self, host: str = config.mongodb_url):
        self.client = pymongo.MongoClient(host)
        self.tokens = self.client["celeritas"]["tokens"]
        token = {
            "_id": "So11111111111111111111111111111111111111112",
            "mint": "So11111111111111111111111111111111111111112",
            "is_pump_fun": False,
            "pump_fun_data": {
                "bonding_curve": None,
                "associated_bonding_curve": None,
                "bonding_curve_progress": None,
                "bonding_curve_price_sol": None,
                "bonding_curve_complete": False,
            },
            "price_dollars": 100.0,
            "market_cap_dollars": 0.0,
            "decimals": 9,
            "name": "Wrapped SOL",
            "symbol": "SOL",
            "description": None,
            "metadata_uri": "",
            "is_mutable": True,
            "supply": "0",
            "refresh_timestamp": 1718822443,
            "price_history": [],
            "price_change": {"5m": 0.0, "30m": 0.0, "24h": 0.0},
        }
        if not self.tokens.find_one({"_id": token["mint"]}):
            self.tokens.insert_one(token)

    async def token_in_db(self, mint: str) -> bool:
        # Returns True if user exists in db, else False
        return self.tokens.find_one({"_id": mint}) is not None

    async def get_token(self, mint: str) -> dict:
        token = self.tokens.find_one({"_id": mint})
        if token:
            token["supply"] = int(token["supply"])
        return token

    async def get_tokens(self, mints: List[str]) -> List[dict]:
        tokens = {}
        for mint in mints:
            token = await self.get_token(mint)
            if token:
                tokens[mint] = token
            else:
                tokens[mint] = await self.add_token(mint)
        return tokens

    async def get_token_decimals(self, mint: str) -> int:
        token = self.tokens.find_one({"_id": mint}, {"decimals": 1})
        return token["decimals"]

    async def insert_token_to_db(self, token: dict):
        """Used by update_token if token is not yet in db"""
        token["_id"] = token["mint"]
        token["supply"] = str(token["supply"])
        self.tokens.insert_one(token)

    async def update_token(self, token: dict):
        """Update existing token or insert a new one"""
        # hacky way to support large ints
        token["supply"] = str(token["supply"])
        if not await self.token_in_db(token["mint"]):
            await self.insert_token_to_db(token)
        else:
            self.tokens.update_one({"_id": token["mint"]}, {"$set": token})

    async def get_prices(self, mints: List[str], add_missing=False) -> dict:
        """Returns prices for all specified mints in {mint: usd_price} format if they are in db"""
        prices = {}
        tokens = self.tokens.find({"_id": {"$in": mints}}, {"price_dollars": 1, "refresh_timestamp": 1})
        for token in tokens:
            prices[token["_id"]] = token.get("price_dollars", None)
        # Filter out mints that were not found in the database
        missing_mints = set(mints) - set(prices.keys())
        # if missing_mints:
        #    raise ValueError(f"Some tokens not in DB: {missing_mints}")
        return prices

    async def update_pump_fun_token(self, mint: str, token=None):
        """Updates pump.fun token price from bonding curve"""
        if not token:
            token = await self.get_token(mint)
        # Bonding curve info
        bc = await get_bonding_curve(
            Pubkey.from_string(token["pump_fun_data"]["bonding_curve"]),
        )
        token["pump_fun_data"]["bonding_curve_complete"] = bool(bc.complete)
        token["refresh_timestamp"] = int(time.time())
        # If bonding curve is complete, price should be fetched from raydium
        if bc.complete:
            token["pump_fun_data"]["bonding_curve_progress"] = 1
            return await self.update_price(token)
        token["pump_fun_data"]["bonding_curve_price_sol"] = (
            10**-3 * bc.virtualSolReserves / bc.virtualTokenReserves
        )
        token["pump_fun_data"]["bonding_curve_progress"] = (
            800_000_000 - bc.realTokenReserves * 10**-6
        ) / 800_000_000
        token["price_dollars"] = token["pump_fun_data"]["bonding_curve_price_sol"] * sol_dollar_value()
        token["market_cap_dollars"] = token["supply"] * token["price_dollars"] / 10 ** token["decimals"]
        token = await self.update_price_history(token, token["price_dollars"])
        token = await self.calculate_price_change(token)
        await self.update_token(token)
        return token

    async def update_price_history(self, token: dict, new_price: float):
        current_time = int(time.time())
        token["price_history"].append({"timestamp": current_time, "price": new_price})

        # Keep only the last 25 hours of price history
        token["price_history"] = [
            entry for entry in token["price_history"] if entry["timestamp"] > current_time - 25 * 60 * 60
        ]
        return token

    async def calculate_price_change(self, token: dict):
        current_time = int(time.time())
        current_price = token["price_dollars"]

        for period, minutes in [("5m", 5), ("30m", 30), ("24h", 24 * 60)]:
            target_time = current_time - minutes * 60
            closest_entry = min(token["price_history"], key=lambda x: abs(x["timestamp"] - target_time))
            old_price = closest_entry["price"]

            if old_price != 0:
                change = (current_price - old_price) / old_price
                token["price_change"][period] = change * 100  # Percentage change
            else:
                token["price_change"][period] = 0.0
        return token

    async def _edit_price_fetch_supply(self, token, price):
        token["price_dollars"] = price
        token["refresh_timestamp"] = int(time.time())
        # fetch and update market cap
        supply = await get_token_supply(token["mint"])
        token["supply"] = supply
        token["market_cap_dollars"] = (token["price_dollars"] / 10 ** token["decimals"]) * supply
        # Update price history and calculate price change
        token = await self.update_price_history(token, price)
        token = await self.calculate_price_change(token)
        return token

    async def fetch_price_data(self, mint_list):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://price.jup.ag/v6/price?ids={mint_list}") as response:
                response.raise_for_status()
                return await response.json()

    async def update_price(self, mints):
        if isinstance(mints, str):
            mints = [mints]

        tokens = list((await self.get_tokens(mints)).values())

        indexes_to_update = []
        for ix, token in enumerate(tokens):
            if time.time() - token["refresh_timestamp"] >= 60:
                if token["is_pump_fun"] and not token["pump_fun_data"]["bonding_curve_complete"]:
                    tokens[ix] = await self.update_pump_fun_token(token["mint"], token=token)
                else:
                    indexes_to_update.append(ix)

        if not indexes_to_update:
            if len(mints) == 1:
                return tokens[0]
            return

        mint_list = ",".join(tokens[ix]["mint"] for ix in indexes_to_update)
        price_info = (await self.fetch_price_data(mint_list))["data"]

        for ix in indexes_to_update:
            token = tokens[ix]
            mint = token["mint"]
            if mint not in price_info:  # Fetch price info by internally creating a quote
                amm = await get_pool_id_by_mint(WRAPPED_SOL, Pubkey.from_string(mint))
                quote = await get_quote_info_from_pool(WRAPPED_SOL, 0.000001, amm)
                price = sol_dollar_value() / quote["current_price"]
            else:
                price = price_info[mint]["price"]

            token = await self._edit_price_fetch_supply(token, price)
            await self.update_token(token)

        if len(mints) == 1:
            return tokens[0]

    async def fetch_pump_fun_data(self, mint: str):
        async with aiohttp.ClientSession() as session:
            pump_fun_url = f"https://frontend-api.pump.fun/coins/{mint}"
            async with session.get(pump_fun_url) as response:
                if response.status != 200:
                    return None
                return await response.json()

    async def add_token(self, mint: str):
        token = await self.get_token(mint)
        if token:
            return token

        pump_fun_data = await self.fetch_pump_fun_data(mint)
        token = self.token_prototype.copy()

        if not pump_fun_data:
            token["is_pump_fun"] = False
            token_metadata = await get_metadata(mint)
            token.update(
                {
                    "mint": mint,
                    "name": token_metadata["data"]["name"],
                    "symbol": token_metadata["data"]["symbol"],
                    "metadata_uri": token_metadata["data"]["uri"],
                    "decimals": token_metadata["decimals"],
                    "supply": token_metadata["supply"],
                    "is_mutable": token_metadata["is_mutable"],
                    "refresh_timestamp": int(time.time()) - 100,
                }
            )
            await self.update_token(token)
            return await self.update_price(mint)

        token.update(
            {
                "mint": mint,
                "is_pump_fun": True,
                "pump_fun_data": {
                    "bonding_curve": pump_fun_data["bonding_curve"],
                    "associated_bonding_curve": pump_fun_data["associated_bonding_curve"],
                },
                "name": pump_fun_data["name"],
                "symbol": pump_fun_data["symbol"],
                "metadata_uri": pump_fun_data["metadata_uri"],
                "description": pump_fun_data["description"],
                "refresh_timestamp": int(time.time()) - 100,
                "decimals": 6,
                "supply": 1_000_000_000 * 10**6,
            }
        )

        if pump_fun_data["raydium_pool"]:
            token["pump_fun_data"].update(
                {
                    "bonding_curve_progress": 1.0,
                    "bonding_curve_complete": True,
                    "bonding_curve_price_sol": None,
                }
            )
            await self.update_token(token)
            return await self.update_price(mint)
        else:
            return await self.update_pump_fun_token(mint, token=token)


class TransactionDB:
    def __init__(self, host: str = config.mongodb_url):
        self.client = pymongo.MongoClient(host)
        self.transactions = self.client["celeritas"]["transactions"]
        self.transactions.create_index([("timestamp", pymongo.ASCENDING)], expireAfterSeconds=180)

    async def insert_transaction(self, user_id: int, message_id: int, tx_signature: str, mint: str, timestamp: float):
        """Inserts a new transaction into the database."""
        self.transactions.insert_one(
            {
                "user_id": user_id,
                "message_id": message_id,
                "tx_signature": tx_signature,
                "mint": mint,
                "timestamp": timestamp
            }
        )

    async def fetch_transaction(self, tx_signature: Signature) -> dict:
        """Fetches a transaction from the database."""
        transaction = self.transactions.find_one({"tx_signature": tx_signature})
        return transaction

    async def delete_transaction(self, tx_signature: Signature):
        """Deletes a transaction from the database."""
        self.transactions.delete_one({"tx_signature": tx_signature})


user_db = UserDB()
token_db = TokenDB()
transaction_db = TransactionDB()