from solana.rpc.api import Client
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

RPC_URL = "https://young-young-pallet.solana-mainnet.quiknode.pro/6886f13de49844f1e4fc08ca009e3ae4c1ee89fa/"
SOLANA_WS_URL = "wss://young-young-pallet.solana-mainnet.quiknode.pro/6886f13de49844f1e4fc08ca009e3ae4c1ee89fa/"
PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"
client = Client(RPC_URL)
aclient = AsyncClient(RPC_URL)

LAMPORTS_PER_SOL = 1_000_000_000
SOLANA_MINT = "So11111111111111111111111111111111111111112"
WRAPPED_SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
