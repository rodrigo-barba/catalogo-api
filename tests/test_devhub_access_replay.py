from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests.test_devhub_access_schemas import valid_payload


def build_session() -> Session:
    from app.models.base import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_semantic_replay_returns_original_facts_and_does_not_duplicate_rows() -> None:
    from app.models.catalog import AccesoDevhub, AccesoDevhubEvento, AccesoDevhubUsuario, RegistroIntegracion
    from app.schemas.devhub_access import DevhubAccessRequest
    from app.services.devhub_access_acceptance import accept_devhub_access

    session = build_session()
    original = valid_payload()
    original["authorized_users"] = [{"cuil": "27345678901"}, {"cuil": "20456789012"}]
    first = accept_devhub_access(session, DevhubAccessRequest.model_validate(original))
    session.commit()

    replay_payload = valid_payload()
    replay_payload["authorized_users"] = [
        {"cuil": "20456789012"},
        {"cuil": "27345678901"},
        {"cuil": "20456789012"},
    ]
    replay = accept_devhub_access(session, DevhubAccessRequest.model_validate(replay_payload))

    assert replay.idempotent_replay is True
    assert replay.devhub_access_id == first.devhub_access_id
    assert replay.route == first.route
    assert replay.recorded_at == first.recorded_at
    assert replay.authorized_cuils == first.authorized_cuils
    assert session.scalar(select(func.count()).select_from(RegistroIntegracion)) == 1
    assert session.scalar(select(func.count()).select_from(AccesoDevhub)) == 1
    assert session.scalar(select(func.count()).select_from(AccesoDevhubUsuario)) == 2
    assert session.scalar(select(func.count()).select_from(AccesoDevhubEvento)) == 1


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["ticket"].update(ticket_id="INC-9999"),
    lambda payload: payload.update(occurred_at="2026-10-06T14:11:00Z"),
])
def test_changed_event_id_reuse_is_rejected_without_new_rows(mutate) -> None:
    from app.models.catalog import AccesoDevhub, RegistroIntegracion
    from app.schemas.devhub_access import DevhubAccessRequest
    from app.services.devhub_access_acceptance import EventIdReused, accept_devhub_access

    session = build_session()
    accept_devhub_access(session, DevhubAccessRequest.model_validate(valid_payload()))
    changed = valid_payload()
    mutate(changed)

    with pytest.raises(EventIdReused):
        accept_devhub_access(session, DevhubAccessRequest.model_validate(changed))

    assert session.scalar(select(func.count()).select_from(RegistroIntegracion)) == 1
    assert session.scalar(select(func.count()).select_from(AccesoDevhub)) == 1


def test_cross_contract_event_id_reuse_is_rejected() -> None:
    from app.models.catalog import RegistroIntegracion
    from app.schemas.devhub_access import DevhubAccessRequest
    from app.services.devhub_access_acceptance import EventIdReused, accept_devhub_access

    session = build_session()
    session.add(RegistroIntegracion(id_evento_origen="devhub-access-001", tipo_registro="R1_ALTA", payload={}))
    session.commit()

    with pytest.raises(EventIdReused):
        accept_devhub_access(session, DevhubAccessRequest.model_validate(valid_payload()))
