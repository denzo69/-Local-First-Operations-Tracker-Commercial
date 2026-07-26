import pytest
from pydantic import ValidationError

from app.config import Settings


def test_development_allows_placeholder_secret_for_local_setup():
    settings = Settings(app_env="development", secret_key="change-me-local-development-secret")

    assert settings.secret_key == "change-me-local-development-secret"


@pytest.mark.parametrize(
    "placeholder",
    [
        "change-me-local-development-secret",
        "change-me-before-real-use",
        "change-this-before-use",
    ],
)
def test_production_rejects_known_placeholder_secrets(placeholder):
    with pytest.raises(ValidationError, match="Unsafe default SECRET_KEY"):
        Settings(app_env="production", secret_key=placeholder)


def test_production_accepts_unique_secret():
    settings = Settings(
        app_env="production",
        secret_key="bW5Q2H35YFQ7YVd6L4XpK2mD8qJ9zR1nV3cT5sA7",
    )

    assert settings.app_env == "production"
