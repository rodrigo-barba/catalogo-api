from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import deployments
from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.catalog import (
    ComponenteEntorno,
    ComponenteVersion,
    Despliegue,
    DespliegueEvento,
    Entorno,
    Persona,
    RegistroIntegracion,
    SincronizacionOutbox,
)
from tests.test_deployment_schemas import valid_failure_payload, valid_success_payload
from tests.test_deployment_service import seed_catalog
from tests.test_setup_schemas import valid_payload


EMPTY_DEPLOYMENT_COUNTS = {
    "deployments": 0,
    "deployment_events": 0,
    "versions": 0,
    "outbox": 0,
    "r3_events": 0,
}


def client_with_engine(
    *,
    estado: str = "ACTIVO",
    entorno_codes: tuple[str, ...] = ("DEV",),
    ce_codes: tuple[str, ...] = ("DEV",),
    espacio: str = "default-space",
    personas: tuple[Persona, ...] = (),
) -> tuple[TestClient, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_catalog(
            session,
            estado=estado,
            entorno_codes=entorno_codes,
            ce_codes=ce_codes,
            espacio=espacio,
        )
        if personas:
            session.add_all(list(personas))
        session.commit()

    def get_test_session():
        session = Session(engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = get_test_session
    return TestClient(app), engine


def persisted_deployment_counts(engine: object) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "deployment_events": session.scalar(select(func.count()).select_from(DespliegueEvento)),
            "versions": session.scalar(select(func.count()).select_from(ComponenteVersion)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
            "r3_events": session.scalar(
                select(func.count())
                .select_from(RegistroIntegracion)
                .where(RegistroIntegracion.tipo_registro == "R3_DESPLIEGUE")
            ),
        }


def test_create_deployment_returns_the_r3_success_contract() -> None:
    response = client_with_engine()[0].post("/api/v1/deployments", json=valid_success_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["deployment_id"] > 0
    assert body["event_id"] == "ocp-deployment-01J8EXAMPLE000001"
    assert body["component"]["component_id"] > 0
    assert body["component_environment"]["component_environment_id"] > 0
    assert body["actor"] == {"cuil": "20123456789", "username": "ocp-pipeline", "persona_id": None}
    assert body["component_action"] == "component.resolved"
    assert body["recorded_at"].endswith("Z")
    assert body["synchronization"]["synchronization_id"] > 0
    assert body["synchronization"]["status"] == "pending"
    assert body["idempotent_replay"] is False


def test_accepted_deployment_records_history_and_one_pending_outbox() -> None:
    test_client, engine = client_with_engine()

    response = test_client.post("/api/v1/deployments", json=valid_success_payload())

    assert response.status_code == 201
    assert persisted_deployment_counts(engine) == {
        "deployments": 1,
        "deployment_events": 1,
        "versions": 1,
        "outbox": 1,
        "r3_events": 1,
    }
    with Session(engine) as session:
        outbox = session.scalar(select(SincronizacionOutbox))
        assert outbox.estado == "pending"
        component_environment = session.scalar(select(ComponenteEntorno))
        assert component_environment.despliegue_vigente_id == response.json()["deployment_id"]


def test_failed_deployment_is_accepted_as_history_without_moving_pointers() -> None:
    test_client, engine = client_with_engine()
    assert test_client.post("/api/v1/deployments", json=valid_success_payload()).status_code == 201
    with Session(engine) as session:
        component_environment = session.scalar(select(ComponenteEntorno))
        version_before = component_environment.version_vigente_id
        deployment_before = component_environment.despliegue_vigente_id

    response = test_client.post("/api/v1/deployments", json=valid_failure_payload())

    assert response.status_code == 201
    with Session(engine) as session:
        failure = session.scalar(
            select(Despliegue).where(Despliegue.id == response.json()["deployment_id"])
        )
        assert failure.resultado == "FALLIDO"
        assert failure.mensaje_resultado == "Image pull failed."
        component_environment = session.scalar(select(ComponenteEntorno))
        assert component_environment.version_vigente_id == version_before
        assert component_environment.despliegue_vigente_id == deployment_before


@pytest.mark.parametrize(
    "case, mutate, expected_status, expected_code",
    [
        ("unsupported schema version", lambda p: p.update(schema_version="2.0"), 400, "unsupported_schema_version"),
        ("unsupported environment", lambda p: p["deployment"].update(environment="staging"), 422, "invalid_environment"),
        ("unknown outcome", lambda p: p["deployment"].update(outcome="partial"), 422, "invalid_outcome"),
        ("missing tag", lambda p: p["deployment"].update(tag=""), 422, "tag_required_for_deployment"),
        ("invalid commit sha", lambda p: p["deployment"].update(commit_sha="not-a-sha"), 422, "invalid_commit_sha"),
        ("invalid cuil", lambda p: p["actor"].update(cuil="20-12345678-9"), 422, "invalid_cuil"),
        ("empty username", lambda p: p["actor"].update(username=""), 422, "invalid_username"),
        ("success without url", lambda p: p["deployment"].pop("url"), 422, "url_required_for_success"),
        ("invalid url", lambda p: p["deployment"].update(url="not-a-url"), 422, "invalid_url"),
        ("missing deployment", lambda p: p.pop("deployment"), 422, "deployment_required"),
        ("empty component identity", lambda p: p["component"].update(component_id=""), 422, "component_id_required"),
        ("unsupported ticket source", lambda p: p["ticket"].update(source_system="jira"), 422, "invalid_ticket"),
        (
            "disordered attempt time",
            lambda p: p.update(started_at="2026-10-06T14:25:30Z", finished_at="2026-10-06T14:24:10Z"),
            422,
            "invalid_attempt_time",
        ),
    ],
)
def test_validation_failures_translate_to_the_contract_codes(
    case: str,
    mutate,
    expected_status: int,
    expected_code: str,
) -> None:
    test_client, engine = client_with_engine()
    payload = valid_success_payload()
    mutate(payload)

    response = test_client.post("/api/v1/deployments", json=payload)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["retryable"] is False
    assert response.json()["correlation_id"]
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_failure_without_result_summary_returns_the_contract_code() -> None:
    payload = valid_success_payload()
    payload["deployment"].update(outcome="failed")
    payload["deployment"].pop("url")

    response = client_with_engine()[0].post("/api/v1/deployments", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "result_summary_required_for_failure"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["deployment"].update(commit_sha=None),
        lambda p: p["deployment"].update(space=None),
        lambda p: p.update(source=None),
    ],
)
def test_explicit_null_returns_the_invalid_null_code(mutate) -> None:
    test_client, engine = client_with_engine()
    payload = valid_success_payload()
    mutate(payload)

    response = test_client.post("/api/v1/deployments", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_null"
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_unknown_field_returns_400_invalid_request_without_writes() -> None:
    test_client, engine = client_with_engine()

    response = test_client.post(
        "/api/v1/deployments", json=valid_success_payload() | {"unexpected": "field"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["details"]
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_malformed_json_returns_400_invalid_request() -> None:
    response = client_with_engine()[0].post(
        "/api/v1/deployments",
        content=b'{"schema_version":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_unknown_component_returns_422_component_not_found_without_writes() -> None:
    test_client, engine = client_with_engine()
    payload = valid_success_payload()
    payload["component"]["component_id"] = "dgisis/missing"

    response = test_client.post("/api/v1/deployments", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "component_not_found"
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_inactive_component_returns_422_component_inactive_without_writes() -> None:
    test_client, engine = client_with_engine(estado="INACTIVO")

    response = test_client.post("/api/v1/deployments", json=valid_success_payload())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "component_inactive"
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_component_without_the_requested_environment_returns_422_without_writes() -> None:
    test_client, engine = client_with_engine(entorno_codes=("DEV", "QA"), ce_codes=("DEV",))
    payload = valid_success_payload()
    payload["deployment"]["environment"] = "qa"

    response = test_client.post("/api/v1/deployments", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "deployment_environment_not_found"
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_dual_identity_returns_409_identity_conflict_without_writes() -> None:
    test_client, engine = client_with_engine(
        personas=(
            Persona(cuil="20123456789", usuario_ad="someone-else"),
            Persona(cuil="20999999999", usuario_ad="ocp-pipeline"),
        )
    )

    response = test_client.post("/api/v1/deployments", json=valid_success_payload())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "identity_conflict"
    assert response.json()["error"]["retryable"] is False
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS


def test_validation_code_translation_is_path_scoped() -> None:
    test_client = client_with_engine()[0]

    deployment = test_client.post(
        "/api/v1/deployments", json=valid_success_payload() | {"unexpected": "field"}
    )
    setup = test_client.post("/api/v1/setups", json=valid_payload() | {"unexpected": "field"})

    assert deployment.status_code == 400
    assert deployment.json()["error"]["code"] == "invalid_request"
    assert setup.status_code == 422
    assert setup.json()["error"]["code"] == "validation_error"


def test_r1_setups_keep_the_invalid_null_contract_code() -> None:
    payload = valid_payload()
    payload["source"] = None

    response = client_with_engine()[0].post("/api/v1/setups", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_null"


def test_persistence_failure_returns_retryable_503_without_partial_writes(monkeypatch) -> None:
    test_client, engine = client_with_engine()

    def persistence_failure(session, _request):
        session.add(Entorno(id=99, codigo="FAIL", nombre="Failure", orden=99))
        session.flush()
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(deployments, "accept_deployment", persistence_failure)

    response = test_client.post("/api/v1/deployments", json=valid_success_payload())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "temporarily_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert persisted_deployment_counts(engine) == EMPTY_DEPLOYMENT_COUNTS
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Entorno)) == 1
