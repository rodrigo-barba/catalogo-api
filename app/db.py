from alembic.config import Config
from sqlalchemy import Engine, create_engine

from app.core.config import Settings


def get_database_url(settings: Settings) -> str:
    if settings.database_url is None:
        raise RuntimeError("CATALOGO_DATABASE_URL must be configured for database operations")
    return settings.database_url


def create_engine_from_settings(settings: Settings) -> Engine:
    """Create an engine without opening a database connection."""

    return create_engine(get_database_url(settings), pool_pre_ping=True)


def configure_alembic_url(config: Config, settings: Settings) -> None:
    """Provide Alembic with the database URL from the application boundary."""

    config.set_main_option("sqlalchemy.url", get_database_url(settings))
