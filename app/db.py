from collections.abc import Generator

from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def get_database_url(settings: Settings) -> str:
    if settings.database_url is None:
        raise RuntimeError("CATALOGO_DATABASE_URL must be configured for database operations")
    return settings.database_url


def create_engine_from_settings(settings: Settings) -> Engine:
    """Create an engine without opening a database connection."""

    return create_engine(get_database_url(settings), pool_pre_ping=True)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    """Create a session factory without opening a database connection."""

    return sessionmaker(bind=create_engine_from_settings(settings), expire_on_commit=False)


def get_session_for_settings(settings: Settings) -> Generator[Session, None, None]:
    """Yield one session and commit or roll back its transaction."""

    session = create_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that manages one transaction per request."""

    yield from get_session_for_settings(get_settings())


def configure_alembic_url(config: Config, settings: Settings) -> None:
    """Provide Alembic with the database URL from the application boundary."""

    config.set_main_option("sqlalchemy.url", get_database_url(settings))
