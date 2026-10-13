from __future__ import annotations

import socket

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests.test_deployment_schemas import valid_failure_payload, valid_success_payload


def build_session() -> Session:
    from app.models.base import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def successful_payload(event_id: str, finished_at: str, *, tag: str) -> dict:
    payload = valid_success_payload()
    payload["event_id"] = event_id
    payload["started_at"] = "2026-10-06T13:00:00Z"
    payload["finished_at"] = finished_at
    payload["deployment"]["tag"] = tag
    return payload


def seed_catalog(
    session: Session,
    *,
    estado: str = "ACTIVO",
    entorno_codes: tuple[str, ...] = ("DEV",),
    ce_codes: tuple[str, ...] = ("DEV",),
    espacio: str = "default-space",
):
    from app.models.catalog import (
        Componente,
        ComponenteEntorno,
        Entorno,
        Proyecto,
        RegistroIntegracion,
    )

    seed = RegistroIntegracion(tipo_registro="CARGA_INICIAL", payload={})
    session.add(seed)
    session.flush()

    entornos: dict[str, Entorno] = {}
    for index, code in enumerate(entorno_codes, start=1):
        entorno = Entorno(id=index, codigo=code, nombre=code.title(), orden=index)
        session.add(entorno)
        entornos[code] = entorno

    project = Proyecto(
        registro_alta_id=seed.id,
        nombre_normalizado="catalog",
        plataforma="OCP",
        ruta_organigrama="dgisis",
    )
    session.add(project)
    session.flush()

    component = Componente(
        proyecto_id=project.id,
        gitlab_repositorio_id="dgisis/api-identidad",
        nombre="API Identidad",
        nombre_normalizado="api-identidad",
        estado=estado,
        registro_alta_id=seed.id,
    )
    session.add(component)
    session.flush()

    for code in ce_codes:
        session.add(
            ComponenteEntorno(
                componente_id=component.id,
                entorno_id=entornos[code].id,
                espacio=espacio,
                registro_alta_id=seed.id,
            )
        )
    session.flush()
    return component


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_acceptance_records_successful_deployment_and_advances_pointers() -> None:
    from app.models.catalog import (
        ComponenteEntorno,
        ComponenteVersion,
        Despliegue,
        DespliegueEvento,
        Persona,
        RegistroIntegracion,
        SincronizacionOutbox,
    )
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.add(Persona(cuil="20123456789", usuario_ad="ocp-pipeline", nombre="Op", apellido="Pipeline"))
    session.commit()

    accepted = accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()

    deployment = session.scalar(select(Despliegue))
    version = session.scalar(select(ComponenteVersion))
    component_environment = session.scalar(select(ComponenteEntorno))
    outbox = session.scalar(select(SincronizacionOutbox))
    event = session.scalar(
        select(RegistroIntegracion).where(RegistroIntegracion.tipo_registro == "R3_DESPLIEGUE")
    )

    assert accepted.idempotent_replay is False
    assert accepted.deployment_id == deployment.id
    assert accepted.component_environment_id == component_environment.id
    assert accepted.synchronization_id == outbox.id
    assert accepted.persona_id is not None

    assert deployment.resultado == "EXITOSO"
    assert deployment.espacio == "portal-ciudadano-dev"
    assert deployment.referencia_ejecucion is None
    assert deployment.componente_version_id == version.id
    assert deployment.fecha_fin is not None
    assert deployment.fecha_inicio is not None
    assert deployment.registro_id == event.id

    assert component_environment.version_vigente_id == version.id
    assert component_environment.despliegue_vigente_id == deployment.id

    assert count(session, ComponenteVersion) == 1
    assert count(session, DespliegueEvento) == 1
    assert count(session, SincronizacionOutbox) == 1
    assert outbox.estado == "pending"
    assert event.id_evento_origen == "ocp-deployment-01J8EXAMPLE000001"


def test_acceptance_records_failure_and_leaves_pointers_unchanged() -> None:
    from app.models.catalog import ComponenteEntorno, ComponenteVersion, Despliegue
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()

    component_environment = session.scalar(select(ComponenteEntorno))
    version_before = component_environment.version_vigente_id
    deployment_before = component_environment.despliegue_vigente_id

    accepted = accept_deployment(session, DeploymentRequest.model_validate(valid_failure_payload()))
    session.commit()

    deployment = session.scalar(select(Despliegue).where(Despliegue.id == accepted.deployment_id))
    component_environment = session.scalar(select(ComponenteEntorno))

    assert deployment.resultado == "FALLIDO"
    assert deployment.mensaje_resultado == "Image pull failed."
    assert deployment.referencia_ejecucion is None
    assert component_environment.version_vigente_id == version_before
    assert component_environment.despliegue_vigente_id == deployment_before
    assert count(session, ComponenteVersion) == 2


def test_acceptance_reuses_component_version_for_a_repeated_tag() -> None:
    from app.models.catalog import ComponenteVersion
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    second = valid_failure_payload()
    second["deployment"]["tag"] = "2.4.1"

    accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    accept_deployment(session, DeploymentRequest.model_validate(second))
    session.commit()

    version = session.scalar(select(ComponenteVersion))
    assert count(session, ComponenteVersion) == 1
    assert version.tag == "2.4.1"


def test_acceptance_rejects_component_that_was_never_set_up() -> None:
    from app.schemas.deployment import ComponentNotFound, DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()

    with pytest.raises(ComponentNotFound):
        accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))


def test_acceptance_rejects_inactive_component_distinctly() -> None:
    from app.schemas.deployment import ComponentInactive, DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session, estado="INACTIVO")

    with pytest.raises(ComponentInactive):
        accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))


def test_acceptance_rejects_environment_missing_from_catalog() -> None:
    from app.schemas.deployment import DeploymentRequest, InvalidEnvironment
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session, entorno_codes=("DEV",), ce_codes=("DEV",))
    payload = valid_success_payload()
    payload["deployment"]["environment"] = "qa"

    with pytest.raises(InvalidEnvironment):
        accept_deployment(session, DeploymentRequest.model_validate(payload))


def test_acceptance_rejects_component_without_the_requested_environment() -> None:
    from app.schemas.deployment import DeploymentEnvironmentNotFound, DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session, entorno_codes=("DEV", "QA"), ce_codes=("DEV",))
    payload = valid_success_payload()
    payload["deployment"]["environment"] = "qa"

    with pytest.raises(DeploymentEnvironmentNotFound):
        accept_deployment(session, DeploymentRequest.model_validate(payload))


def test_acceptance_keeps_unresolved_username_as_text_without_creating_persona() -> None:
    from app.models.catalog import Despliegue, Persona
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.commit()

    accepted = accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()

    deployment = session.scalar(select(Despliegue))
    assert accepted.persona_id is None
    assert count(session, Persona) == 0
    assert deployment.ejecutado_por_persona_id is None
    assert deployment.ejecutado_por_texto == "ocp-pipeline"


def test_acceptance_resolves_persona_by_username_when_cuil_is_unknown() -> None:
    from app.models.catalog import Despliegue, Persona
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    persona = Persona(cuil="20999999999", usuario_ad="ocp-pipeline", nombre="Op", apellido="Pipeline")
    session.add(persona)
    session.commit()

    accepted = accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()

    deployment = session.scalar(select(Despliegue))
    assert accepted.persona_id == persona.id
    assert deployment.ejecutado_por_persona_id == persona.id


def test_acceptance_rejects_dual_identity_match_before_any_write() -> None:
    from app.models.catalog import Despliegue, Persona, RegistroIntegracion
    from app.schemas.deployment import DeploymentRequest, IdentityConflict
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.add_all(
        [
            Persona(cuil="20123456789", usuario_ad="someone-else"),
            Persona(cuil="20999999999", usuario_ad="ocp-pipeline"),
        ]
    )
    session.commit()
    registros_before = count(session, RegistroIntegracion)

    with pytest.raises(IdentityConflict):
        accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))

    assert count(session, Despliegue) == 0
    assert count(session, RegistroIntegracion) == registros_before


def test_acceptance_falls_back_to_component_environment_space() -> None:
    from app.models.catalog import Despliegue
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session, espacio="catalog-dev")
    payload = valid_success_payload()
    payload["deployment"].pop("space")

    accept_deployment(session, DeploymentRequest.model_validate(payload))
    session.commit()

    deployment = session.scalar(select(Despliegue))
    assert deployment.espacio == "catalog-dev"


def test_acceptance_never_opens_an_invgate_connection(monkeypatch) -> None:
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("InvGate call attempted"))

    accept_deployment(session, DeploymentRequest.model_validate(valid_success_payload()))
    session.commit()


def test_older_success_is_history_only_and_never_regresses_pointers() -> None:
    from app.models.catalog import ComponenteEntorno, ComponenteVersion, Despliegue, DespliegueEvento, SincronizacionOutbox
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.commit()

    newer = accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-newer", "2026-10-06T15:00:00Z", tag="3.0.0")),
    )
    session.commit()
    component_environment = session.scalar(select(ComponenteEntorno))
    version_before = component_environment.version_vigente_id

    older = accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-older", "2026-10-06T14:00:00Z", tag="2.0.0")),
    )
    session.commit()

    component_environment = session.scalar(select(ComponenteEntorno))
    assert older.idempotent_replay is False
    assert component_environment.despliegue_vigente_id == newer.deployment_id
    assert component_environment.version_vigente_id == version_before
    older_row = session.get(Despliegue, older.deployment_id)
    assert older_row is not None
    assert older_row.resultado == "EXITOSO"
    assert count(session, Despliegue) == 2
    assert count(session, DespliegueEvento) == 2
    assert count(session, SincronizacionOutbox) == 2
    assert count(session, ComponenteVersion) == 2


def test_newer_success_advances_the_pointer_to_the_latest_attempt() -> None:
    from app.models.catalog import ComponenteEntorno, ComponenteVersion, Despliegue
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.commit()

    first = accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-first", "2026-10-06T14:00:00Z", tag="1.0.0")),
    )
    session.commit()
    second = accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-second", "2026-10-06T15:00:00Z", tag="2.0.0")),
    )
    session.commit()

    component_environment = session.scalar(select(ComponenteEntorno))
    second_version = session.scalar(
        select(ComponenteVersion).where(ComponenteVersion.tag == "2.0.0")
    )
    second_deployment = session.get(Despliegue, second.deployment_id)
    assert second_deployment is not None
    assert component_environment.despliegue_vigente_id == second.deployment_id
    assert component_environment.version_vigente_id == second_version.id
    assert component_environment.despliegue_vigente_id != first.deployment_id


def test_equal_finished_at_breaks_pointer_ties_on_event_id() -> None:
    from app.models.catalog import ComponenteEntorno
    from app.schemas.deployment import DeploymentRequest
    from app.services.deployment_acceptance import accept_deployment

    session = build_session()
    seed_catalog(session)
    session.commit()

    accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-a", "2026-10-06T15:00:00Z", tag="1.0.0")),
    )
    session.commit()
    component_environment = session.scalar(select(ComponenteEntorno))
    winner = component_environment.despliegue_vigente_id

    earlier_tie = accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-0", "2026-10-06T15:00:00Z", tag="2.0.0")),
    )
    session.commit()
    component_environment = session.scalar(select(ComponenteEntorno))
    assert component_environment.despliegue_vigente_id == winner
    assert earlier_tie.deployment_id != winner

    later_tie = accept_deployment(
        session,
        DeploymentRequest.model_validate(successful_payload("evt-b", "2026-10-06T15:00:00Z", tag="3.0.0")),
    )
    session.commit()
    component_environment = session.scalar(select(ComponenteEntorno))
    assert component_environment.despliegue_vigente_id == later_tie.deployment_id
