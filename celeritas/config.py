import json
import os
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

class Config:
    def __init__(self, config_file='config.json', default_config_file='config.default.json'):
        self.config_file = config_file
        self.default_config_file = default_config_file
        self.config = self._load_config()

    def _load_config(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return json.load(f)
        elif os.path.exists(self.default_config_file):
            logger.info(f"Warning: Using default configuration file '{self.default_config_file}'")
            with open(self.default_config_file, 'r') as f:
                return json.load(f)
        else:
            raise FileNotFoundError(f"Neither '{self.config_file}' nor '{self.default_config_file}' found.")

    def get(self, key, default=None):
        return self.config.get(key, default)

    @property
    def telegram_bot_token(self):
        return self.get('telegram_bot_token')

    @property
    def solana_rpc_url(self):
        return self.get('solana_rpc_url')

    @property
    def solana_ws_url(self):
        return self.get('solana_ws_url')

    @property
    def platform_fee_pubkey(self):
        return self.get('platform_fee_pubkey')

config = Config()