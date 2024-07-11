from solana.rpc.api import Client
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

from celeritas.config import config

RPC_URL = config.solana_rpc_url
SOLANA_WS_URL = config.solana_ws_url
PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"
client = Client(RPC_URL)
aclient = AsyncClient(RPC_URL)

LAMPORTS_PER_SOL = 1_000_000_000
SOLANA_MINT = "So11111111111111111111111111111111111111112"
WRAPPED_SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
