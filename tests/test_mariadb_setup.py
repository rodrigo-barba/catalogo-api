from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pymysql.constants import CLIENT
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas.setup import SetupRequest
from app.services.setup_acceptance import accept_setup
from tests.test_setup_schemas import valid_payload


TEST_DATABASE = "catalogo_r1_test"
CREATION_SCRIPT = Path("/home/rbarba/Downloads/01_creacion_modelo_mariadb.sql")
ACTIVE_PROTECTED_TARGET = re.compile(
    r"^\s*(?:CREATE\s+DATABASE|USE|DROP\s+DATABASE).*`?catalogo_proyectos`?",
    re.IGNORECASE | re.MULTILINE,
)


def disposable_database_url() -> URL:
    return URL.create(
        "mysql+pymysql",
        username="root",
        password="root",
        host="127.0.0.1",
        port=3308,
        database=TEST_DATABASE,
    )


def target_checked_schema_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    transformed = CREATION_SCRIPT.read_text().replace("`catalogo_proyectos`", f"`{TEST_DATABASE}`")
    assert not ACTIVE_PROTECTED_TARGET.search(transformed)

    transformed_path = tmp_path_factory.mktemp("mariadb") / "schema.sql"
    transformed_path.write_text(transformed)
    target_checked = transformed_path.read_text()
    assert f"`{TEST_DATABASE}`" in target_checked
    assert not ACTIVE_PROTECTED_TARGET.search(target_checked)
    return transformed_path


def test_disposable_database_url_targets_mariadb_on_port_3308(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATALOGO_DATABASE_URL", raising=False)

    database_url = disposable_database_url()

    assert database_url.host == "127.0.0.1"
    assert database_url.port == 3308
    assert database_url.database == TEST_DATABASE


@pytest.fixture(scope="module")
def mariadb_database(tmp_path_factory: pytest.TempPathFactory) -> URL:
    database_url = disposable_database_url()
    original_database_url = os.environ.get("CATALOGO_DATABASE_URL")
    os.environ["CATALOGO_DATABASE_URL"] = database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    server_url = URL.create(
        database_url.drivername,
        username=database_url.username,
        password=database_url.password,
        host=database_url.host,
        port=database_url.port,
    )
    transformed_path = target_checked_schema_path(tmp_path_factory)

    server_engine = create_engine(server_url)
    schema_engine = create_engine(database_url, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    try:
        with server_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{TEST_DATABASE}`"))
            connection.execute(text(f"CREATE DATABASE `{TEST_DATABASE}` CHARACTER SET utf8mb4"))
        raw_connection = schema_engine.raw_connection()
        try:
            with raw_connection.cursor() as cursor:
                cursor.execute(transformed_path.read_text())
                while cursor.nextset():
                    pass
            raw_connection.commit()
        finally:
            raw_connection.close()
        alembic = Config("alembic.ini")
        command.upgrade(alembic, "head")
        with schema_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO entorno (id, codigo, nombre, orden) VALUES "
                    "(1, 'DEV', 'Development', 1), (2, 'QA', 'Quality', 2), "
                    "(3, 'HML', 'Homologation', 3), (4, 'PROD', 'Production', 4)"
                )
            )
        yield database_url
    finally:
        schema_engine.dispose()
        with server_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{TEST_DATABASE}`"))
        server_engine.dispose()
        if original_database_url is None:
            os.environ.pop("CATALOGO_DATABASE_URL", None)
        else:
            os.environ["CATALOGO_DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def test_migration_preserves_component_triggers_and_global_event_uniqueness(mariadb_database: URL) -> None:
    engine = create_engine(mariadb_database)
    try:
        with engine.begin() as connection:
            triggers = connection.execute(
                text("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = DATABASE()")
            ).scalars().all()
            connection.execute(
                text(
                    "INSERT INTO registro_integracion "
                    "(tipo_registro, herramienta_origen, id_evento_origen, fecha_evento, payload) "
                    "VALUES ('R1_ALTA', 'OCP', 'event-unique', NOW(6), JSON_OBJECT())"
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO registro_integracion "
                        "(tipo_registro, herramienta_origen, id_evento_origen, fecha_evento, payload) "
                        "VALUES ('R1_ALTA', 'DEVHUB', 'event-unique', NOW(6), JSON_OBJECT())"
                    )
                )
        assert {"trg_componente_alta", "trg_componente_modificacion", "trg_componente_eliminacion"} <= set(triggers)
    finally:
        engine.dispose()


def test_concurrent_same_event_creates_one_durable_setup(mariadb_database: URL) -> None:
    engine = create_engine(mariadb_database, isolation_level="READ COMMITTED")
    payload = SetupRequest.model_validate(valid_payload())

    def submit() -> str:
        with Session(engine) as session:
            accepted = accept_setup(session, payload)
            session.commit()
            return "replay" if accepted.idempotent_replay else "created"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: submit(), range(2)))
        with engine.connect() as connection:
            event_count = connection.scalar(text("SELECT COUNT(*) FROM registro_integracion WHERE id_evento_origen = 'ocp-setup-001'"))
            outbox_count = connection.scalar(text("SELECT COUNT(*) FROM sincronizacion_outbox"))
        assert sorted(outcomes) == ["created", "replay"]
        assert event_count == 1
        assert outbox_count == 1
    finally:
        engine.dispose()


def test_r1_creates_no_persona_or_deployment_state(mariadb_database: URL) -> None:
    engine = create_engine(mariadb_database)
    payload = valid_payload()
    payload["event_id"] = "ocp-setup-no-side-effects"
    payload["project"]["platform_project_id"] = "platform-no-side-effects"
    payload["project"]["name"] = "No Side Effects Catalog"
    payload["component"]["component_id"] = "dgisis/no-side-effects-api"

    try:
        with Session(engine) as session:
            accept_setup(session, SetupRequest.model_validate(payload))
            session.commit()
        with engine.connect() as connection:
            persona_count = connection.scalar(text("SELECT COUNT(*) FROM persona"))
            deployment_count = connection.scalar(text("SELECT COUNT(*) FROM despliegue"))
            current_state = connection.execute(
                text(
                    "SELECT version_vigente_id, despliegue_vigente_id "
                    "FROM componente_entorno ce "
                    "JOIN componente c ON c.id = ce.componente_id "
                    "WHERE c.gitlab_repositorio_id = 'dgisis/no-side-effects-api'"
                )
            ).one()
        assert persona_count == 0
        assert deployment_count == 0
        assert current_state == (None, None)
    finally:
        engine.dispose()
