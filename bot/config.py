"""Configuration management using pydantic-settings."""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    BOT_TOKEN: str = Field(..., description="Telegram Bot API token from @BotFather")
    OWNER_ID: int = Field(..., description="Telegram User ID of the owner")
    CARD_NUMBER: str = Field(
        default="0000 0000 0000 0000",
        description="Bank card number to send in replies",
    )
    DELETE_TRIGGER_MESSAGE: bool = Field(
        default=True,
        description="Whether to delete owner's trigger message after auto-reply",
    )
    COOLDOWN_SECONDS: int = Field(
        default=30,
        description="Anti-spam cooldown in seconds per chat per trigger",
    )
    DB_PATH: str = Field(
        default="bot_data.sqlite3",
        description="Path to SQLite database file",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    NOTIFICATION_HOUR: int = Field(
        default=12,
        description="Hour of day (0-23) for daily debtor reminders",
    )
    NOTIFICATION_MINUTE: int = Field(
        default=0,
        description="Minute of hour (0-59) for daily debtor reminders",
    )
    TIMEZONE: str = Field(
        default="Europe/Moscow",
        description="Timezone for scheduled reminders (e.g. Europe/Moscow, Asia/Yekaterinburg)",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Singleton instance
settings = Settings()
