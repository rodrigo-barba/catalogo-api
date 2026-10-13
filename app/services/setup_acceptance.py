from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.catalog import (
    Componente,
    ComponenteEntorno,
    Entorno,
    Organigrama,
    Proyecto,
    RegistroIntegracion,
    SetupEvento,
    SincronizacionOutbox,
)
from app.schemas.setup import Conflict, EventIdReused, SetupConflict, SetupDuplicate, SetupRequest


@dataclass(frozen=True)
class SetupAccepted:
    event_id: str
    project_id: int
    component_id: int
    synchronization_id: int
    synchronization_status: str
    organigrama_id: int | None
    review_required: bool
    idempotent_replay: bool = False


def canonicalize_setup(request: SetupRequest) -> dict[str, object]:
    data = request.model_dump(mode="json")
    data["source"]["cluster_code"] = data["source"]["cluster_code"].strip().lower()
    for section, fields in {"repartition": ("route", "name"), "project": ("platform_project_id", "name"), "component": ("component_id", "name")}.items():
        for field in fields:
            data[section][field] = " ".join(data[section][field].split()).casefold()
    url = data["component"]["git_repository_url"].strip().casefold().rstrip("/")
    data["component"]["git_repository_url"] = url
    data["environments"] = sorted(data["environments"], key=lambda item: item["environment"])
    return data


def _hash(canonical: dict[str, object]) -> str:
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _result(event: RegistroIntegracion, evidence: SetupEvento, outbox: SincronizacionOutbox, replay: bool) -> SetupAccepted:
    payload = evidence.evidencia_canonica
    return SetupAccepted(
        event_id=event.id_evento_origen or "",
        project_id=int(payload["project_id"]),
        component_id=int(payload["component_id"]),
        synchronization_id=outbox.id,
        synchronization_status=outbox.estado,
        organigrama_id=payload.get("organigrama_id"),
        review_required=bool(payload["review_required"]),
        idempotent_replay=replay,
    )


def _setup_conflicts(
    project: Proyecto,
    component: Componente | None,
    project_data: dict[str, object],
    component_data: dict[str, object],
    ticket_id: str,
) -> list[Conflict]:
    conflicts = []
    if project.id_proyecto_plataforma != project_data["platform_project_id"]:
        conflicts.append(Conflict("project.platform_project_id", "differs_from_established_identity"))
    if project.nombre_normalizado != project_data["name"]:
        conflicts.append(Conflict("project.name", "differs_from_established_setup"))
    if project.nro_ticket_setup not in (None, ticket_id):
        conflicts.append(Conflict("ticket.ticket_id", "differs_from_established_setup"))
    if component is None:
        conflicts.append(Conflict("component.component_id", "differs_from_established_identity"))
        return conflicts
    if component.gitlab_repositorio_id != component_data["component_id"]:
        conflicts.append(Conflict("component.component_id", "differs_from_established_identity"))
    if component.nombre_normalizado != component_data["name"]:
        conflicts.append(Conflict("component.name", "differs_from_established_setup"))
    if component.gitlab_repositorio_url != component_data["git_repository_url"]:
        conflicts.append(Conflict("component.git_repository_url", "differs_from_established_setup"))
    return conflicts


def accept_setup(session: Session, request: SetupRequest) -> SetupAccepted:
    canonical = canonicalize_setup(request)
    semantic_hash = _hash(canonical)
    existing_event = session.scalar(select(RegistroIntegracion).where(RegistroIntegracion.id_evento_origen == request.event_id))
    if existing_event is not None:
        evidence = existing_event.setup_evento
        if evidence is not None and evidence.hash_semantico == semantic_hash:
            return _result(existing_event, evidence, existing_event.sincronizacion_outbox, True)
        raise EventIdReused()

    try:
        with session.begin_nested():
            route = canonical["repartition"]["route"]
            organigrama = session.scalar(
                select(Organigrama).where(
                    Organigrama.ruta_completa == route,
                    Organigrama.es_vigente == 1,
                )
            )
            project_data = canonical["project"]
            component_data = canonical["component"]
            project_by_platform = session.scalar(
                select(Proyecto).where(Proyecto.id_proyecto_plataforma == project_data["platform_project_id"])
            )
            component_by_identity = session.scalar(
                select(Componente).where(Componente.gitlab_repositorio_id == component_data["component_id"])
            )
            project = project_by_platform or (component_by_identity.proyecto if component_by_identity is not None else None)
            if project is not None:
                component = component_by_identity
                if component is None:
                    component = session.scalar(select(Componente).where(Componente.proyecto_id == project.id))
                conflicts = _setup_conflicts(project, component, project_data, component_data, request.ticket.ticket_id)
                if conflicts:
                    raise SetupConflict(conflicts)
                raise SetupDuplicate()
            event_values = {
                "tipo_registro": "R1_ALTA",
                "id_evento_origen": request.event_id,
                "herramienta_origen": "OCP",
                "nro_ticket_invgate": request.ticket.ticket_id,
                "fecha_evento": datetime.now(UTC),
                "payload": request.model_dump(mode="json"),
            }
            if session.bind is not None and session.bind.dialect.name == "mysql":
                session.execute(
                    mysql_insert(RegistroIntegracion)
                    .values(**event_values)
                    .on_duplicate_key_update(id=RegistroIntegracion.id)
                )
                event = session.scalar(
                    select(RegistroIntegracion).where(RegistroIntegracion.id_evento_origen == request.event_id)
                )
                assert event is not None
                if event.setup_evento is not None:
                    if event.setup_evento.hash_semantico == semantic_hash:
                        return _result(event, event.setup_evento, event.sincronizacion_outbox, True)
                    raise EventIdReused()
            else:
                event = RegistroIntegracion(**event_values)
                session.add(event)
                session.flush()
            project = Proyecto(
                id_proyecto_plataforma=project_data["platform_project_id"],
                nombre=request.project.name,
                nombre_normalizado=project_data["name"],
                plataforma="OCP",
                ruta_organigrama=request.repartition.route.lstrip("/"),
                nro_ticket_setup=request.ticket.ticket_id,
                registro_alta_id=event.id,
                organigrama=organigrama,
                ruta_requiere_revision=organigrama is None,
            )
            session.add(project)
            session.flush()
            component = Componente(
                proyecto=project,
                gitlab_repositorio_id=component_data["component_id"],
                nombre=request.component.name,
                nombre_normalizado=component_data["name"],
                gitlab_repositorio_url=component_data["git_repository_url"],
                registro_alta_id=event.id,
            )
            session.add(component)
            session.flush()
            for environment in canonical["environments"]:
                catalog_environment = session.scalar(
                    select(Entorno).where(Entorno.codigo == environment["environment"].upper())
                )
                if catalog_environment is None:
                    raise RuntimeError(f"environment {environment['environment']} is not configured")
                session.add(
                    ComponenteEntorno(
                        componente=component,
                        entorno_id=catalog_environment.id,
                        espacio=environment["space"],
                        registro_alta_id=event.id,
                    )
                )
            evidence = SetupEvento(
                registro_integracion=event,
                hash_semantico=semantic_hash,
                evidencia_canonica={
                    "project_id": project.id,
                    "component_id": component.id,
                    "organigrama_id": organigrama.id if organigrama is not None else None,
                    "review_required": organigrama is None,
                },
                actor_cuil=request.actor.cuil,
            )
            outbox = SincronizacionOutbox(registro_integracion=event, estado="pending")
            session.add_all([evidence, outbox])
            session.flush()
            return _result(event, evidence, outbox, False)
    except IntegrityError:
        existing_event = session.scalar(
            select(RegistroIntegracion).where(RegistroIntegracion.id_evento_origen == request.event_id)
        )
        if existing_event is not None and existing_event.setup_evento is not None:
            if existing_event.setup_evento.hash_semantico == semantic_hash:
                return _result(existing_event, existing_event.setup_evento, existing_event.sincronizacion_outbox, True)
        raise
