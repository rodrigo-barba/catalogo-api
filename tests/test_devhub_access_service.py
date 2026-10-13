from __future__ import annotations

import socket

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests.test_devhub_access_schemas import valid_payload


def build_session() -> Session:
    from app.models.base import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def count(session: Session, model: object) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_acceptance_persists_one_access_personas_links_and_raw_payload() -> None:
    from app.models.catalog import (
        AccesoDevhub,
        AccesoDevhubEvento,
        AccesoDevhubUsuario,
        Componente,
        ComponenteEntorno,
        Persona,
        Proyecto,
        RegistroIntegracion,
        SincronizacionOutbox,
    )
    from app.schemas.devhub_access import DevhubAccessRequest
    from app.services.devhub_access_acceptance import accept_devhub_access

    session = build_session()
    payload = valid_payload()
    payload["requested_by"] = {"cuil": "20123456789"}
    payload["authorized_users"] = [
        {"cuil": "27345678901"},
        {"cuil": "20456789012"},
        {"cuil": "27345678901"},
    ]

    accepted = accept_devhub_access(session, DevhubAccessRequest.model_validate(payload))
    session.commit()

    access = session.scalar(select(AccesoDevhub))
    event = session.scalar(select(RegistroIntegracion))
    assert accepted.idempotent_replay is False
    assert accepted.devhub_access_id == access.id
    assert accepted.route == "/JGM/DGISIS"
    assert accepted.authorized_cuils == ("20456789012", "27345678901")
    assert event.payload == payload
    assert access.ruta_organigrama == "/JGM/DGISIS"
    assert count(session, Persona) == 3
    assert count(session, AccesoDevhubUsuario) == 2
    assert count(session, AccesoDevhubEvento) == 1
    assert count(session, Proyecto) == 0
    assert count(session, Componente) == 0
    assert count(session, ComponenteEntorno) == 0
    assert count(session, SincronizacionOutbox) == 0


def test_acceptance_resolves_current_route_and_flags_unmatched_route_without_network(monkeypatch) -> None:
    from app.models.catalog import AccesoDevhub, Organigrama
    from app.schemas.devhub_access import DevhubAccessRequest
    from app.services.devhub_access_acceptance import accept_devhub_access

    session = build_session()
    session.add(Organigrama(id=12, ruta_completa="JGM/DGISIS", es_vigente=1))
    session.commit()
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("network call attempted"))

    matched = accept_devhub_access(session, DevhubAccessRequest.model_validate(valid_payload()))
    unmatched_payload = valid_payload()
    unmatched_payload["event_id"] = "devhub-access-unmatched"
    unmatched_payload["repartition"]["route"] = "JGM/UNKNOWN"
    unmatched = accept_devhub_access(session, DevhubAccessRequest.model_validate(unmatched_payload))
    session.commit()

    rows = session.scalars(select(AccesoDevhub).order_by(AccesoDevhub.id)).all()
    assert matched.organigrama_id == 12
    assert matched.review_required is False
    assert unmatched.organigrama_id is None
    assert unmatched.review_required is True
    assert rows[0].ruta_organigrama == "/JGM/DGISIS"
    assert rows[1].ruta_organigrama == "JGM/UNKNOWN"
    assert rows[1].ruta_requiere_revision is True


def test_acceptance_rolls_back_every_r2_row_on_persistence_failure(monkeypatch) -> None:
    from app.models.catalog import AccesoDevhub, AccesoDevhubEvento, AccesoDevhubUsuario, Persona, RegistroIntegracion
    from app.schemas.devhub_access import DevhubAccessRequest
    from app.services.devhub_access_acceptance import TemporarilyUnavailable, accept_devhub_access

    session = build_session()
    monkeypatch.setattr(session, "add_all", lambda _: (_ for _ in ()).throw(RuntimeError("write failed")))

    with pytest.raises(TemporarilyUnavailable):
        accept_devhub_access(session, DevhubAccessRequest.model_validate(valid_payload()))

    assert count(session, RegistroIntegracion) == 0
    assert count(session, AccesoDevhub) == 0
    assert count(session, AccesoDevhubUsuario) == 0
    assert count(session, AccesoDevhubEvento) == 0
    assert count(session, Persona) == 0
