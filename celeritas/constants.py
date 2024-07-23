from solana.rpc.api import Client
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from celeritas.config import config
import aiolimiter
import asyncio

RPC_URL = config.solana_rpc_url
SOLANA_WS_URL = config.solana_ws_url
PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"

rate_limiter = aiolimiter.AsyncLimiter(config.max_requests_per_second, 1)

class RateLimitedAsyncClient:
    def __init__(self, url, **kwargs):
        self._client = AsyncClient(url, **kwargs)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._client.__aexit__(exc_type, exc, tb)

    async def close(self):
        await self._client.close()

    async def is_connected(self):
        async with rate_limiter:
            return await self._client.is_connected()

    def __getattr__(self, name):
        original_attr = getattr(self._client, name)
        if callable(original_attr):
            async def wrapper(*args, **kwargs):
                async with rate_limiter:
                    return await original_attr(*args, **kwargs)
            return wrapper
        return original_attr

client = Client(RPC_URL)
aclient = RateLimitedAsyncClient(RPC_URL)

LAMPORTS_PER_SOL = 1_000_000_000
SOLANA_MINT = "So11111111111111111111111111111111111111112"
WRAPPED_SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")