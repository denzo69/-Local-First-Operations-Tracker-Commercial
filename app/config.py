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
    secret_key: str = "change-me-local-development-secret"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_max_age_seconds: int = 60 * 60 * 12
    csrf_cookie_name: str = "ops_tracker_csrf"
    login_throttle_max_attempts: int = 5
    login_throttle_window_seconds: int = 60 * 5
    password_iterations: int = 260_000
    backup_scheduler_enabled: bool = True
    backup_scheduler_interval_minutes: int = 60 * 24
    backup_retention_count: int = 50

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


DEFAULT_DEVELOPMENT_SECRET_KEYS = {
    "change-me-local-development-secret",
    "change-me-before-real-use",
    "change-this-before-use",
}


def validate_runtime_configuration(settings: Settings | None = None) -> None:
    """Fail clearly when non-development configuration still uses local defaults."""
    active_settings = settings or get_settings()
    if active_settings.app_env.lower() in {"development", "test", "testing", "local"}:
        return
    if not active_settings.secret_key or active_settings.secret_key in DEFAULT_DEVELOPMENT_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY must be set to a unique non-default value when APP_ENV is not development."
        )
