import os
from dataclasses import dataclass

@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN")
    db_url: str = os.getenv("DATABASE_URL")
    webhook_host: str = os.getenv("WEBHOOK_HOST")
    webhook_path: str = os.getenv("WEBHOOK_PATH", "/webhook")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "super_secret_string_123")
    channel_id: str = os.getenv("CHANNEL_ID")
    pandascore_token: str = os.getenv("PANDASCORE_TOKEN") # <--- НОВАЯ СТРОКА

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_host}{self.webhook_path}"

config = Config()
