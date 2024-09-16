## TurboTendies - Solana Trading Bot

This repository contains the source code for a Solana trading bot built with Telegram integration.

**Project Structure:**

* **celeritas:** Main directory containing the core bot logic.
* **celeritas/telegram_bot:** Directory containing the Telegram bot handler logic.
* **celeritas/db:** Directory containing the database interactions logic.
* **celeritas/transact:** Directory containing the transaction logic.
* **celeritas/pump_fun_sniper:** Directory containing the Pump.fun sniper logic.
* **data:** Directory to store data like price caches and database data.

**Setup:**
1. **Setup Docker in your preferred environment**
2. **Environment Variables:**
* Rename the `.env.example` file to `.env` and set the following environment variables:
* **TELEGRAM_BOT_TOKEN:** Your Telegram bot token.
* **SOLANA_RPC_URL:** Your Solana RPC endpoint.
* **SOLANA_WS_URL:** Your Solana WebSocket endpoint.
* **PLATFORM_FEE_PUBKEY:** The public key of your platform fee recipient.
* **ADMIN_TELEGRAM_ACCOUNT_ID:** The Telegram account ID of your admin.
* **MAX_REQUESTS_PER_SECOND:** The maximum number of requests to Solana per second.
* **MONGO_USERNAME:** The MongoDB username.
* **MONGO_PASSWORD:** The MongoDB password.
* **MONGO_PORT:** The MongoDB port (default: 27017).
* **WEBHOOK_URL:** The URL of your server running the bot.
* **WEBHOOK_PORT:** The port on which the bot server listens (default: 443).
3. **Docker Compose:**
* Run the bot using the following command: `docker compose --profile no_transaction_listener up --build`

**Note:**

The project was developed with the intention of going live, but it was never fully deployed. It may require some adjustments and updates to function correctly.
