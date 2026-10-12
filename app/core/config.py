from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded only from process environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CATALOGO_",
        env_file=None,
        secrets_dir=None,
    )

    database_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
