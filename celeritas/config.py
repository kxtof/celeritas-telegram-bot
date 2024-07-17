import logging
import os

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


class Config:
    def __init__(self):
        pass

    def get(self, key, default=None):
        # First, try to get the value from an environment variable
        env_value = os.environ.get(key.upper())
        if env_value is not None:
            return env_value
        logger.info(f"Missing env value: {key.upper()}")
        return env_value

    @property
    def telegram_bot_token(self):
        return self.get("telegram_bot_token")

    @property
    def solana_rpc_url(self):
        return self.get("solana_rpc_url")

    @property
    def solana_ws_url(self):
        return self.get("solana_ws_url")

    @property
    def platform_fee_pubkey(self):
        return self.get("platform_fee_pubkey")

    @property
    def admin_telegram_account_id(self):
        return int(self.get("admin_telegram_account_id"))

    @property
    def webhook_url(self):
        return self.get("webhook_url")

    @property
    def webhook_port(self):
        return self.get("webhook_port")

    @property
    def mongodb_url(self):
        url = self.get("mongodb_url")
        if url:
            return url
        username, password, port = (
            self.get("mongo_username"),
            self.get("mongo_password"),
            self.get("mongo_port"),
        )
        return f"mongodb://{username}:{password}@mongodb:{port}"


config = Config()
