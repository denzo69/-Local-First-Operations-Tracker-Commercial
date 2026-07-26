from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_UNSAFE_SECRET_KEYS = {
    "change-me-local-development-secret",
    "change-me-before-real-use",
    "change-this-before-use",
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    app_name: str = "JEronAI Operations"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/app.sqlite"
    backup_dir: str = "./backups"
    host: str = "127.0.0.1"
    port: int = 8000
    secret_key: str = "change-me-local-development-secret"
    password_iterations: int = 260_000
    backup_scheduler_enabled: bool = True
    backup_scheduler_interval_minutes: int = 60 * 24
    backup_retention_count: int = 50

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def reject_unsafe_production_secret(self) -> "Settings":
        """Prevent production startup with a known placeholder signing secret."""

        if self.app_env.strip().lower() == "production" and self.secret_key in _UNSAFE_SECRET_KEYS:
            raise ValueError(
                "Unsafe default SECRET_KEY detected for APP_ENV=production. "
                "Configure a unique, high-entropy SECRET_KEY before starting the application."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
