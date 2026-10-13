from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import setups
from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.catalog import (
    Componente,
    ComponenteEntorno,
    Entorno,
    Proyecto,
    RegistroIntegracion,
    SetupEvento,
    SincronizacionOutbox,
)
from tests.test_setup_schemas import valid_payload


def client_with_engine() -> tuple[TestClient, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Entorno(id=1, codigo="DEV", nombre="Development", orden=1),
                Entorno(id=2, codigo="QA", nombre="Quality", orden=2),
            ]
        )
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


def client() -> TestClient:
    return client_with_engine()[0]


def persisted_setup_counts(engine: object) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "projects": session.scalar(select(func.count()).select_from(Proyecto)),
            "components": session.scalar(select(func.count()).select_from(Componente)),
            "component_environments": session.scalar(select(func.count()).select_from(ComponenteEntorno)),
            "events": session.scalar(select(func.count()).select_from(RegistroIntegracion)),
            "setup_events": session.scalar(select(func.count()).select_from(SetupEvento)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        }


def test_create_setup_returns_the_r1_success_contract() -> None:
    response = client().post("/api/v1/setups", json=valid_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["event_id"] == "ocp-setup-001"
    assert body["idempotent_replay"] is False
    assert body["project"]["project_id"] > 0
    assert body["component"]["component_id"] > 0
    assert body["component"]["component_action"] == "created"
    assert body["repartition"] == {"organigrama_id": None, "review_required": True}
    assert body["synchronization"]["synchronization_id"] > 0
    assert body["synchronization"]["status"] == "pending"
    assert body["recorded_at"].endswith("Z")


def test_replay_and_validation_use_the_public_response_contract() -> None:
    test_client = client()
    payload = valid_payload()

    assert test_client.post("/api/v1/setups", json=payload).status_code == 201
    replay = test_client.post("/api/v1/setups", json=payload)
    invalid = test_client.post("/api/v1/setups", json={**payload, "unexpected": "field"})

    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert invalid.status_code == 422
    error = invalid.json()["error"]
    assert error["code"] == "validation_error"
    assert error["retryable"] is False
    assert error["details"]
    assert invalid.json()["correlation_id"]


def test_existing_setup_rejects_all_conflict_dimensions_without_mutation() -> None:
    test_client, engine = client_with_engine()
    assert test_client.post("/api/v1/setups", json=valid_payload()).status_code == 201
    before = persisted_setup_counts(engine)

    conflict_cases = [
        ("project.name", "differs_from_established_setup", lambda payload: payload["project"].update(name=" Other   Catalog ")),
        (
            "component.git_repository_url",
            "differs_from_established_setup",
            lambda payload: payload["component"].update(git_repository_url="https://git.example.invalid/dgisis/other-api"),
        ),
        ("project.platform_project_id", "differs_from_established_identity", lambda payload: payload["project"].update(platform_project_id="platform-2")),
        ("component.component_id", "differs_from_established_identity", lambda payload: payload["component"].update(component_id="dgisis/other-api")),
    ]
    for index, (field, reason, mutate) in enumerate(conflict_cases, start=2):
        payload = valid_payload()
        payload["event_id"] = f"ocp-setup-{index:03d}"
        mutate(payload)

        response = test_client.post("/api/v1/setups", json=payload)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "setup_conflict"
        assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
        assert persisted_setup_counts(engine) == before


def test_new_event_with_changed_ticket_returns_exact_conflict_without_mutation() -> None:
    test_client, engine = client_with_engine()
    assert test_client.post("/api/v1/setups", json=valid_payload()).status_code == 201
    before = persisted_setup_counts(engine)
    changed_ticket = valid_payload()
    changed_ticket["event_id"] = "ocp-setup-changed-ticket"
    changed_ticket["ticket"]["ticket_id"] = "SET-99"

    response = test_client.post("/api/v1/setups", json=changed_ticket)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "setup_conflict"
    assert response.json()["error"]["details"] == [
        {"field": "ticket.ticket_id", "reason": "differs_from_established_setup"}
    ]
    assert persisted_setup_counts(engine) == before


def test_new_event_duplicate_and_changed_same_event_leave_setup_rows_unchanged() -> None:
    test_client, engine = client_with_engine()
    assert test_client.post("/api/v1/setups", json=valid_payload()).status_code == 201
    before = persisted_setup_counts(engine)

    duplicate = valid_payload()
    duplicate["event_id"] = "ocp-setup-duplicate"
    duplicate_response = test_client.post("/api/v1/setups", json=duplicate)
    changed_same_event = valid_payload()
    changed_same_event["ticket"]["ticket_id"] = "SET-99"
    reused_response = test_client.post("/api/v1/setups", json=changed_same_event)

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "setup_duplicate"
    assert reused_response.status_code == 409
    assert reused_response.json()["error"]["code"] == "event_id_reused"
    assert persisted_setup_counts(engine) == before


def test_semantic_replay_returns_original_pending_outbox_without_duplicates() -> None:
    test_client, engine = client_with_engine()
    first_payload = valid_payload()
    first_payload["environments"] = [
        {"environment": "qa", "space": "catalog-qa"},
        {"environment": "dev", "space": "catalog-dev"},
    ]
    first = test_client.post("/api/v1/setups", json=first_payload)
    replay_payload = valid_payload()
    replay_payload["source"]["cluster_code"] = ""
    replay_payload["component"]["git_repository_url"] = " HTTPS://GIT.EXAMPLE.INVALID/DGISIS/CATALOG-API "
    replay_payload["environments"] = list(reversed(first_payload["environments"]))

    replay = test_client.post("/api/v1/setups", json=replay_payload)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["synchronization"] == first.json()["synchronization"]
    assert persisted_setup_counts(engine) == {
        "projects": 1,
        "components": 1,
        "component_environments": 2,
        "events": 1,
        "setup_events": 1,
        "outbox": 1,
    }


def test_explicit_null_uses_the_invalid_null_contract_code_and_identifies_the_field() -> None:
    optional_null = valid_payload()
    optional_null["source"]["cluster_code"] = None
    required_null = valid_payload()
    required_null["source"] = None

    for payload, field in ((optional_null, "source.cluster_code"), (required_null, "source")):
        response = client().post("/api/v1/setups", json=payload)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_null"
        assert response.json()["error"]["retryable"] is False
        assert response.json()["error"]["details"] == [{"field": field, "reason": "string_type" if field.endswith("cluster_code") else "model_attributes_type"}]


@pytest.mark.parametrize(
    "case, request_kwargs",
    [
        ("malformed JSON", {"content": b'{"schema_version":', "headers": {"content-type": "application/json"}}),
        ("unknown field", {"json": valid_payload() | {"unexpected": "field"}}),
        ("unsupported schema version", {"json": valid_payload() | {"schema_version": "2.0"}}),
        ("unsupported source", {"json": valid_payload() | {"source": {"system": "other"}}}),
        ("empty event ID", {"json": valid_payload() | {"event_id": ""}}),
        ("empty project identity", {"json": valid_payload() | {"project": {"platform_project_id": "", "name": "Catalog"}}}),
        ("empty project name", {"json": valid_payload() | {"project": {"platform_project_id": "platform-1", "name": ""}}}),
        ("empty component identity", {"json": valid_payload() | {"component": {**valid_payload()["component"], "component_id": ""}}}),
        ("empty component name", {"json": valid_payload() | {"component": {**valid_payload()["component"], "name": ""}}}),
        ("invalid CUIL", {"json": valid_payload() | {"actor": {"cuil": "20-12345678-9"}}}),
        ("empty environments", {"json": valid_payload() | {"environments": []}}),
        ("unsupported environment", {"json": valid_payload() | {"environments": [{"environment": "uat", "space": "catalog-uat"}]}}),
        ("empty environment space", {"json": valid_payload() | {"environments": [{"environment": "dev", "space": ""}]}}),
        ("invalid route", {"json": valid_payload() | {"repartition": {"route": "not-a-route", "name": "DGISIS"}}}),
        ("invalid repository URL", {"json": valid_payload() | {"component": {**valid_payload()["component"], "git_repository_url": "not-a-url"}}}),
        ("unsupported ticket source", {"json": valid_payload() | {"ticket": {"source_system": "other", "ticket_id": "SET-42"}}}),
        ("empty ticket", {"json": valid_payload() | {"ticket": {"source_system": "invgate", "ticket_id": ""}}}),
        ("explicit null", {"json": valid_payload() | {"source": None}}),
    ],
)
def test_every_invalid_input_class_returns_validation_error_without_writes(case: str, request_kwargs: dict[str, object]) -> None:
    test_client, engine = client_with_engine()

    response = test_client.post("/api/v1/setups", **request_kwargs)

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"validation_error", "invalid_null"}
    assert response.json()["error"]["details"]
    assert persisted_setup_counts(engine) == {
        "projects": 0,
        "components": 0,
        "component_environments": 0,
        "events": 0,
        "setup_events": 0,
        "outbox": 0,
    }


def test_persistence_failure_returns_retryable_503_without_partial_writes(monkeypatch) -> None:
    test_client, engine = client_with_engine()

    def persistence_failure(session, _request):
        session.add(Entorno(id=99, codigo="FAIL", nombre="Failure", orden=99))
        session.flush()
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(setups, "accept_setup", persistence_failure)

    response = test_client.post("/api/v1/setups", json=valid_payload())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "temporarily_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert persisted_setup_counts(engine) == {
        "projects": 0,
        "components": 0,
        "component_environments": 0,
        "events": 0,
        "setup_events": 0,
        "outbox": 0,
    }
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Entorno)) == 2
