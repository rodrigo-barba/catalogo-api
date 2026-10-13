from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.catalog import AccesoDevhub, AccesoDevhubEvento, AccesoDevhubUsuario, Persona, RegistroIntegracion
from tests.test_devhub_access_schemas import valid_payload


def client_with_engine() -> tuple[TestClient, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

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


def persisted_counts(engine: object) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "accesses": session.scalar(select(func.count()).select_from(AccesoDevhub)),
            "access_events": session.scalar(select(func.count()).select_from(AccesoDevhubEvento)),
            "access_users": session.scalar(select(func.count()).select_from(AccesoDevhubUsuario)),
            "events": session.scalar(select(func.count()).select_from(RegistroIntegracion)),
            "people": session.scalar(select(func.count()).select_from(Persona)),
        }


def test_new_access_returns_the_r2_contract_with_numeric_persisted_id_and_deduplicated_users() -> None:
    test_client, engine = client_with_engine()
    payload = valid_payload()
    payload["authorized_users"] = [
        {"cuil": "27345678901"},
        {"cuil": "20456789012"},
        {"cuil": "27345678901"},
    ]

    response = test_client.post("/api/v1/devhub-accesses", json=payload)

    assert response.status_code == 201
    assert response.json() == {
        "event_id": "devhub-access-001",
        "devhub_access_id": 1,
        "repartition": {"route": "/JGM/DGISIS", "organigrama_id": None, "review_required": True},
        "authorized_users": [{"cuil": "20456789012"}, {"cuil": "27345678901"}],
        "recorded_at": response.json()["recorded_at"],
    }
    assert response.json()["recorded_at"].endswith("Z")
    assert persisted_counts(engine) == {
        "accesses": 1,
        "access_events": 1,
        "access_users": 2,
        "events": 1,
        "people": 2,
    }


def test_semantic_replay_returns_original_r2_facts_with_replay_marker() -> None:
    test_client, _engine = client_with_engine()
    original = valid_payload()
    original["authorized_users"] = [{"cuil": "27345678901"}, {"cuil": "20456789012"}]
    replay = valid_payload()
    replay["authorized_users"] = [
        {"cuil": "20456789012"},
        {"cuil": "27345678901"},
        {"cuil": "20456789012"},
    ]

    created = test_client.post("/api/v1/devhub-accesses", json=original)
    response = test_client.post("/api/v1/devhub-accesses", json=replay)

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.json() == {**created.json(), "idempotent_replay": True}


def test_reused_event_id_returns_r2_conflict_code() -> None:
    test_client, engine = client_with_engine()
    assert test_client.post("/api/v1/devhub-accesses", json=valid_payload()).status_code == 201
    changed = valid_payload()
    changed["ticket"]["ticket_id"] = "INC-9999"

    response = test_client.post("/api/v1/devhub-accesses", json=changed)

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "event_id_reused",
        "message": "event id reused",
        "details": [],
        "retryable": False,
        "correlation_id": response.json()["error"]["correlation_id"],
    }
    assert persisted_counts(engine)["accesses"] == 1


def test_persistence_rollback_returns_nested_retryable_r2_error(monkeypatch) -> None:
    from app.api import devhub_accesses
    from app.services.devhub_access_acceptance import TemporarilyUnavailable

    test_client, engine = client_with_engine()
    monkeypatch.setattr(devhub_accesses, "accept_devhub_access", lambda *_args: (_ for _ in ()).throw(TemporarilyUnavailable()))

    response = test_client.post("/api/v1/devhub-accesses", json=valid_payload())

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "temporarily_unavailable",
        "message": "persistence is temporarily unavailable",
        "details": [],
        "retryable": True,
        "correlation_id": response.json()["error"]["correlation_id"],
    }
    assert persisted_counts(engine) == {"accesses": 0, "access_events": 0, "access_users": 0, "events": 0, "people": 0}


@pytest.mark.parametrize(
    "mutate, expected_status, expected_code",
    [
        (lambda payload: payload.update(schema_version="2.0"), 400, "unsupported_schema_version"),
        (lambda payload: payload.update(unexpected="field"), 400, "invalid_request"),
        (lambda payload: payload.pop("ticket"), 400, "invalid_request"),
        (lambda payload: payload["ticket"].update(ticket_id=""), 400, "invalid_request"),
        (lambda payload: payload["repartition"].update(route="//JGM/DGISIS"), 422, "invalid_repartition_route"),
        (lambda payload: payload.update(occurred_at="bad-time"), 422, "invalid_occurred_at"),
        (lambda payload: payload["authorized_users"][0].update(cuil="bad"), 422, "invalid_cuil"),
        (lambda payload: payload.update(authorized_users=[]), 422, "authorized_users_required"),
        (lambda payload: payload.update(requested_by=None), 422, "invalid_null"),
    ],
)
def test_r2_validation_returns_path_scoped_codes_and_nested_correlation_id(mutate, expected_status: int, expected_code: str) -> None:
    test_client, engine = client_with_engine()
    payload = valid_payload()
    mutate(payload)

    response = test_client.post("/api/v1/devhub-accesses", json=payload)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["retryable"] is False
    assert response.json()["error"]["correlation_id"]
    assert "correlation_id" not in response.json()
    assert persisted_counts(engine) == {"accesses": 0, "access_events": 0, "access_users": 0, "events": 0, "people": 0}
