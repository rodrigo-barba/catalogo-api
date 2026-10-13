from sqlalchemy import create_engine
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import pytest
import socket

from tests.test_setup_schemas import valid_payload


def create_session() -> Session:
    from app.models.base import Base
    from app.models.catalog import Entorno

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            Entorno(id=1, codigo="DEV", nombre="Development", orden=1),
            Entorno(id=2, codigo="QA", nombre="Quality", orden=2),
        ]
    )
    session.commit()
    return session


def test_acceptance_creates_event_evidence_and_pending_outbox() -> None:
    from app.models.catalog import RegistroIntegracion, SincronizacionOutbox
    from app.schemas.setup import SetupRequest
    from app.services.setup_acceptance import accept_setup

    session = create_session()
    accepted = accept_setup(session, SetupRequest.model_validate(valid_payload()))
    session.commit()

    assert accepted.idempotent_replay is False
    assert accepted.review_required is True
    assert accepted.synchronization_status == "pending"
    assert session.scalar(select(func.count()).select_from(RegistroIntegracion)) == 1
    assert session.scalar(select(func.count()).select_from(SincronizacionOutbox)) == 1
    assert session.scalar(select(SincronizacionOutbox.estado)) == "pending"


def test_acceptance_resolves_only_current_organigrama_routes_and_reviews_historical_matches() -> None:
    from app.models.catalog import Organigrama, Proyecto
    from app.schemas.setup import SetupRequest
    from app.services.setup_acceptance import accept_setup

    session = create_session()
    session.add_all(
        [
            Organigrama(id=1, ruta_completa="/dgisis/current", es_vigente=1),
            Organigrama(id=2, ruta_completa="/dgisis/historical", es_vigente=None),
        ]
    )
    session.commit()

    current_payload = valid_payload()
    current_payload["event_id"] = "ocp-setup-current"
    current_payload["repartition"]["route"] = "/DGISIS/Current"
    current = accept_setup(session, SetupRequest.model_validate(current_payload))
    session.commit()

    historical_payload = valid_payload()
    historical_payload["event_id"] = "ocp-setup-historical"
    historical_payload["project"]["platform_project_id"] = "platform-historical"
    historical_payload["component"]["component_id"] = "dgisis/historical-api"
    historical_payload["repartition"]["route"] = "/DGISIS/Historical"
    historical = accept_setup(session, SetupRequest.model_validate(historical_payload))
    session.commit()

    current_project = session.scalar(select(Proyecto).where(Proyecto.id == current.project_id))
    historical_project = session.scalar(select(Proyecto).where(Proyecto.id == historical.project_id))
    assert current_project is not None
    assert current_project.organigrama_id == 1
    assert current_project.ruta_requiere_revision is False
    assert historical_project is not None
    assert historical_project.organigrama_id is None
    assert historical_project.ruta_requiere_revision is True
    assert historical.review_required is True


def test_acceptance_never_opens_an_invgate_connection_or_creates_deployment_state(monkeypatch) -> None:
    from app.models.catalog import ComponenteEntorno
    from app.schemas.setup import SetupRequest
    from app.services.setup_acceptance import accept_setup

    session = create_session()
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("InvGate call attempted"))

    accept_setup(session, SetupRequest.model_validate(valid_payload()))
    session.commit()

    component_environment = session.scalar(select(ComponenteEntorno))
    assert component_environment is not None
    assert component_environment.version_vigente_id is None
    assert component_environment.despliegue_vigente_id is None


def test_acceptance_replays_semantically_equivalent_event_without_duplicates() -> None:
    from app.schemas.setup import SetupRequest
    from app.services.setup_acceptance import accept_setup

    session = create_session()
    replay_payload = valid_payload()
    replay_payload["source"]["cluster_code"] = ""
    replay_payload["component"]["git_repository_url"] = " HTTPS://GIT.EXAMPLE.INVALID/DGISIS/CATALOG-API "
    replay_payload["environments"] = [
        {"environment": "qa", "space": "catalog-qa"},
        {"environment": "dev", "space": "catalog-dev"},
    ]
    first_payload = valid_payload()
    first_payload["environments"] = list(reversed(replay_payload["environments"]))
    first = accept_setup(session, SetupRequest.model_validate(first_payload))
    replay = accept_setup(session, SetupRequest.model_validate(replay_payload))

    assert replay.idempotent_replay is True
    assert replay.event_id == first.event_id


def test_acceptance_rejects_reused_event_and_new_event_conflicts_without_mutation() -> None:
    from app.schemas.setup import SetupConflict, SetupRequest, EventIdReused
    from app.services.setup_acceptance import accept_setup

    session = create_session()
    accept_setup(session, SetupRequest.model_validate(valid_payload()))
    changed_event = valid_payload()
    changed_event["ticket"]["ticket_id"] = "SET-99"
    with pytest.raises(EventIdReused):
        accept_setup(session, SetupRequest.model_validate(changed_event))
    new_event = valid_payload()
    new_event["event_id"] = "ocp-setup-002"
    new_event["ticket"]["ticket_id"] = "SET-99"
    with pytest.raises(SetupConflict) as error:
        accept_setup(session, SetupRequest.model_validate(new_event))

    assert {conflict.field for conflict in error.value.conflicts} == {"ticket.ticket_id"}


def test_acceptance_rolls_back_all_rows_when_the_outbox_write_fails(monkeypatch) -> None:
    from app.models.catalog import (
        Componente,
        ComponenteEntorno,
        Proyecto,
        RegistroIntegracion,
        SetupEvento,
        SincronizacionOutbox,
    )
    from app.schemas.setup import SetupRequest
    from app.services.setup_acceptance import accept_setup

    session = create_session()
    monkeypatch.setattr(session, "add_all", lambda _: (_ for _ in ()).throw(RuntimeError("write failed")))

    with pytest.raises(RuntimeError, match="write failed"):
        accept_setup(session, SetupRequest.model_validate(valid_payload()))

    assert session.scalar(select(func.count()).select_from(Proyecto)) == 0
    assert session.scalar(select(func.count()).select_from(Componente)) == 0
    assert session.scalar(select(func.count()).select_from(ComponenteEntorno)) == 0
    assert session.scalar(select(func.count()).select_from(RegistroIntegracion)) == 0
    assert session.scalar(select(func.count()).select_from(SetupEvento)) == 0
    assert session.scalar(select(func.count()).select_from(SincronizacionOutbox)) == 0
