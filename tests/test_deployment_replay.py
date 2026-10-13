from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from tests.test_deployment_schemas import valid_success_payload
from tests.test_deployment_service import build_session, count, seed_catalog


def build_engine_with_event_unique_index():
    """Mirror the production global key `uq_registro_integracion_1`.

    The legacy MariaDB baseline declares
    `UNIQUE (herramienta_origen, id_evento_origen)`. The SQLite test schema is
    derived from the ORM metadata, which does not down-declare that physical
    constraint, so the race tests recreate it explicitly to exercise the
    duplicate-key guard.
    """

    from app.models.base import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_registro_integracion_evento_origen_global "
                "ON registro_integracion (herramienta_origen, id_evento_origen)"
            )
        )
    return engine


def test_acceptance_replays_identical_event_with_the_original_recorded_at() -> None:
    from app.models.catalog import Despliegue, DespliegueEvento, SincronizacionOutbox
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.commit()

    first = accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()

    replay_payload = valid_success_payload()
    replay_payload["source"]["cluster_code"] = " Cluster-DEV-01 "
    replay_payload["deployment"]["tag"] = " 2.4.1 "
    replay_payload["deployment"]["url"] = "HTTPS://API-IDENTIDAD.DEV.EXAMPLE.INVALID/"
    replay_payload["deployment"]["commit_sha"] = "9F6B1C4D8E2A7B5F3C1D0E9A8B7C6D5E4F3A2B1C"
    replay = accept_deployment(session, DeploymentRequest.model_validate(replay_payload))

    assert replay.idempotent_replay is True
    assert replay.deployment_id == first.deployment_id
    assert replay.component_environment_id == first.component_environment_id
    assert replay.synchronization_id == first.synchronization_id
    assert replay.recorded_at == first.recorded_at
    assert count(session, Despliegue) == 1
    assert count(session, DespliegueEvento) == 1
    assert count(session, SincronizacionOutbox) == 1


def test_acceptance_rejects_reused_event_id_with_changed_content() -> None:
    from app.models.catalog import Despliegue, DespliegueEvento, SincronizacionOutbox
    from app.schemas.deployment import DeploymentRequest, EventIdReused
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.commit()
    accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()

    changed = valid_success_payload()
    changed["deployment"]["tag"] = "2.4.9"

    with pytest.raises(EventIdReused):
        accept_deployment(session, DeploymentRequest.model_validate(changed))

    assert count(session, Despliegue) == 1
    assert count(session, DespliegueEvento) == 1
    assert count(session, SincronizacionOutbox) == 1


def test_acceptance_rejects_an_r1_event_id_reused_for_a_deployment() -> None:
    from app.models.catalog import Despliegue, RegistroIntegracion
    from app.schemas.deployment import DeploymentRequest, EventIdReused
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.add(
        RegistroIntegracion(
            tipo_registro="R1_ALTA",
            herramienta_origen="OCP",
            id_evento_origen=valid_success_payload()["event_id"],
            payload={},
        )
    )
    session.commit()

    with pytest.raises(EventIdReused):
        accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))

    assert count(session, Despliegue) == 0


def test_duplicate_event_key_race_is_classified_as_a_replay(monkeypatch) -> None:
    from app.models.catalog import Despliegue, DespliegueEvento, RegistroIntegracion, SincronizacionOutbox
    from app.schemas.deployment import DeploymentRequest
    from app.services import deployment_acceptance
    from app.services.deployment_acceptance import accept_deployment

    engine = build_engine_with_event_unique_index()
    winner_session = Session(engine)
    seed_catalog(winner_session)
    winner_session.commit()
    request = DeploymentRequest.model_validate(valid_success_payload())
    winner = accept_deployment(winner_session, request)
    winner_session.commit()
    winner_session.close()

    loser_session = Session(engine)
    real_lookup = getattr(deployment_acceptance, "_lookup_event", None)
    lookups = {"count": 0}

    def stale_lookup(session, event_id):
        lookups["count"] += 1
        if lookups["count"] == 1:
            return None  # the loser's pre-check ran before the winner committed
        return real_lookup(session, event_id)

    monkeypatch.setattr(deployment_acceptance, "_lookup_event", stale_lookup, raising=False)

    replay = accept_deployment(loser_session, request)

    assert lookups["count"] == 2
    assert replay.idempotent_replay is True
    assert replay.deployment_id == winner.deployment_id
    assert replay.recorded_at == winner.recorded_at
    assert count(loser_session, Despliegue) == 1
    assert count(loser_session, DespliegueEvento) == 1
    assert count(loser_session, SincronizacionOutbox) == 1
    assert loser_session.scalar(
        select(func.count())
        .select_from(RegistroIntegracion)
        .where(RegistroIntegracion.tipo_registro == "R3_DESPLIEGUE")
    ) == 1


def test_duplicate_event_key_race_with_changed_content_is_rejected(monkeypatch) -> None:
    from app.models.catalog import Despliegue, DespliegueEvento, SincronizacionOutbox
    from app.schemas.deployment import DeploymentRequest, EventIdReused
    from app.services import deployment_acceptance
    from app.services.deployment_acceptance import accept_deployment

    engine = build_engine_with_event_unique_index()
    winner_session = Session(engine)
    seed_catalog(winner_session)
    winner_session.commit()
    accepted = accept_deployment(winner_session, DeploymentRequest.model_validate(valid_success_payload()))
    winner_session.commit()
    assert accepted.idempotent_replay is False
    winner_session.close()

    loser_session = Session(engine)
    real_lookup = getattr(deployment_acceptance, "_lookup_event", None)
    lookups = {"count": 0}

    def stale_lookup(session, event_id):
        lookups["count"] += 1
        if lookups["count"] == 1:
            return None
        return real_lookup(session, event_id)

    monkeypatch.setattr(deployment_acceptance, "_lookup_event", stale_lookup, raising=False)

    changed = valid_success_payload()
    changed["deployment"]["tag"] = "2.4.9"

    with pytest.raises(EventIdReused):
        accept_deployment(loser_session, DeploymentRequest.model_validate(changed))

    check = Session(engine)
    assert count(check, Despliegue) == 1
    assert count(check, DespliegueEvento) == 1
    assert count(check, SincronizacionOutbox) == 1
