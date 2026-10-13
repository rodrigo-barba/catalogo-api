from alembic.config import Config

import app.models
from app.core.config import Settings
from app.db import configure_alembic_url, create_engine_from_settings
from app.models.base import Base


def test_settings_reads_database_url_from_environment_without_dotenv(monkeypatch) -> None:
    database_url = "mysql+pymysql://catalog:placeholder@localhost/catalog"
    monkeypatch.setenv("CATALOGO_DATABASE_URL", database_url)

    settings = Settings()

    assert settings.database_url == database_url
    assert Settings.model_config["env_file"] is None
    assert Settings.model_config["secrets_dir"] is None


def test_database_engine_bootstrap_does_not_connect() -> None:
    database_url = "mysql+pymysql://catalog:placeholder@localhost/catalog"

    engine = create_engine_from_settings(Settings(database_url=database_url))

    assert engine.url.render_as_string(hide_password=False) == database_url


def test_alembic_receives_url_from_application_settings() -> None:
    database_url = "mysql+pymysql://catalog:placeholder@localhost/catalog"
    config = Config()

    configure_alembic_url(config, Settings(database_url=database_url))

    assert config.get_main_option("sqlalchemy.url") == database_url
    assert set(Base.metadata.tables) == {
        "acceso_devhub",
        "acceso_devhub_evento",
        "acceso_devhub_usuario",
        "organigrama",
        "proyecto",
        "componente",
        "componente_entorno",
        "entorno",
        "registro_integracion",
        "setup_evento",
        "sincronizacion_outbox",
        "persona",
        "componente_version",
        "despliegue",
        "despliegue_evento",
    }
