from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    app_name: str = "Local-First Operations Tracker"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/app.sqlite"
    backup_dir: str = "./backups"
    host: str = "127.0.0.1"
    port: int = 8000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
