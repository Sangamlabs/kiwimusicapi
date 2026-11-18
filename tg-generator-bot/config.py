import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    BOT_TOKEN: str = Field(
        default="123456789:ABCDEF_mock_token_change_in_production",
        description="Telegram Bot Token from @BotFather"
    )
    OWNER_ID: int = Field(
        default=0,
        description="Telegram User ID of the administrator/owner"
    )
    BACKEND_API_URL: str = Field(
        default="http://localhost:8000",
        description="Base URL of Kiwi Music API backend"
    )
    BACKEND_INTERNAL_SECRET: str = Field(
        default="kiwi_secret_change_me_in_production",
        description="Internal management authentication secret"
    )

bot_settings = BotSettings()

