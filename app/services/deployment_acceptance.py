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
    ComponenteVersion,
    Despliegue,
    DespliegueEvento,
    Entorno,
    Persona,
    RegistroIntegracion,
    SincronizacionOutbox,
)
from app.schemas.deployment import (
    ComponentInactive,
    ComponentNotFound,
    DeploymentEnvironmentNotFound,
    DeploymentRequest,
    EventIdReused,
    IdentityConflict,
    InvalidEnvironment,
)


OUTCOME_RESULTS = {"succeeded": "EXITOSO", "failed": "FALLIDO"}


@dataclass(frozen=True)
class DeploymentAccepted:
    deployment_id: int
    component_id: int
    component_environment_id: int
    persona_id: int | None
    synchronization_id: int
    recorded_at: str
    idempotent_replay: bool = False


def _render_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonicalize_deployment(request: DeploymentRequest) -> dict[str, object]:
    data = request.model_dump(mode="json")
    data["source"]["cluster_code"] = data["source"]["cluster_code"].strip().lower()
    data["component"]["component_id"] = data["component"]["component_id"].strip()
    data["actor"]["cuil"] = data["actor"]["cuil"].strip()
    username = data["actor"].get("username")
    data["actor"]["username"] = username.strip() if username else None
    deployment = data["deployment"]
    deployment["environment"] = deployment["environment"].lower()
    deployment["outcome"] = deployment["outcome"].lower()
    deployment["tag"] = deployment["tag"].strip()
    deployment["commit_sha"] = deployment["commit_sha"].lower() if deployment.get("commit_sha") else None
    deployment["url"] = deployment["url"].strip().casefold().rstrip("/") if deployment.get("url") else None
    deployment["space"] = deployment["space"].strip() if deployment.get("space") else None
    deployment["result_summary"] = (
        deployment["result_summary"].strip() if deployment.get("result_summary") else None
    )
    ticket = data.get("ticket")
    if ticket is not None:
        ticket["ticket_id"] = ticket["ticket_id"].strip()
    data["started_at"] = _render_utc(request.started_at)
    data["finished_at"] = _render_utc(request.finished_at)
    return data


def _hash(canonical: dict[str, object]) -> str:
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _is_mysql(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "mysql"


def _lookup_event(session: Session, event_id: str) -> RegistroIntegracion | None:
    return session.scalar(
        select(RegistroIntegracion).where(RegistroIntegracion.id_evento_origen == event_id)
    )


def _replay_result(event: RegistroIntegracion, evidence: DespliegueEvento) -> DeploymentAccepted:
    payload = evidence.evidencia_canonica
    return DeploymentAccepted(
        deployment_id=int(payload["deployment_id"]),
        component_id=int(payload["component_id"]),
        component_environment_id=int(payload["component_environment_id"]),
        persona_id=payload.get("persona_id"),
        synchronization_id=int(payload["synchronization_id"]),
        recorded_at=str(payload["recorded_at"]),
        idempotent_replay=True,
    )


def _classify_replay(event: RegistroIntegracion, semantic_hash: str) -> DeploymentAccepted:
    """Classify an already-persisted event_id against the incoming payload.

    Mirrors R1: a ledger row without R3 evidence (an R1/other event id reused as
    a deployment) is a conflict before any hash comparison; an identical
    canonical hash is a replay of the original resource; anything else is a
    reused event_id.
    """

    evidence = event.despliegue_evento
    if evidence is None or evidence.hash_semantico != semantic_hash:
        raise EventIdReused()
    return _replay_result(event, evidence)


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _pointer_key(finished_at: datetime, event_id: str | None) -> tuple[datetime, str]:
    return (_to_naive_utc(finished_at), event_id or "")


def _should_advance_pointer(
    session: Session,
    component_environment: ComponenteEntorno,
    request: DeploymentRequest,
    outcome: str,
) -> bool:
    """A failure never advances; a success advances only when strictly newer.

    Ordering is the tuple ``(finished_at, event_id)``; ties on the finish time
    break lexicographically on the origin event id.
    """

    if outcome != "succeeded":
        return False
    current_id = component_environment.despliegue_vigente_id
    if current_id is None:
        return True
    current = session.get(Despliegue, current_id)
    if current is None:
        return True
    current_event_id = session.scalar(
        select(RegistroIntegracion.id_evento_origen).where(
            RegistroIntegracion.id == current.registro_id
        )
    )
    return _pointer_key(request.finished_at, request.event_id) > _pointer_key(
        current.fecha_fin, current_event_id
    )


def _resolve_actor(session: Session, request: DeploymentRequest) -> tuple[Persona | None, str | None]:
    by_cuil = session.scalar(select(Persona).where(Persona.cuil == request.actor.cuil))
    by_username = None
    if request.actor.username:
        by_username = session.scalar(select(Persona).where(Persona.usuario_ad == request.actor.username))
    if by_cuil is not None and by_username is not None and by_cuil.id != by_username.id:
        raise IdentityConflict()
    return (by_cuil or by_username), (request.actor.username or None)


def _insert_registro(session: Session, request: DeploymentRequest) -> RegistroIntegracion:
    values = {
        "tipo_registro": "R3_DESPLIEGUE",
        "id_evento_origen": request.event_id,
        "herramienta_origen": "OCP",
        "nro_ticket_invgate": request.ticket.ticket_id if request.ticket else None,
        "fecha_evento": datetime.now(UTC),
        "payload": request.model_dump(mode="json"),
    }
    if _is_mysql(session):
        session.execute(
            mysql_insert(RegistroIntegracion)
            .values(**values)
            .on_duplicate_key_update(id=RegistroIntegracion.id)
        )
        event = session.scalar(
            select(RegistroIntegracion).where(RegistroIntegracion.id_evento_origen == request.event_id)
        )
        assert event is not None
        return event
    event = RegistroIntegracion(**values)
    session.add(event)
    session.flush()
    return event


def _resolve_version(
    session: Session,
    component: Componente,
    canonical: dict[str, object],
) -> ComponenteVersion:
    tag = canonical["deployment"]["tag"]
    commit_sha = canonical["deployment"].get("commit_sha")
    if _is_mysql(session):
        session.execute(
            mysql_insert(ComponenteVersion)
            .values(
                componente_id=component.id,
                tag=tag,
                commit_sha=commit_sha,
                fecha_primer_despliegue=datetime.now(UTC),
            )
            .on_duplicate_key_update(id=ComponenteVersion.id)
        )
        version = session.scalar(
            select(ComponenteVersion).where(
                ComponenteVersion.componente_id == component.id,
                ComponenteVersion.tag == tag,
            )
        )
        assert version is not None
        return version
    version = session.scalar(
        select(ComponenteVersion).where(
            ComponenteVersion.componente_id == component.id,
            ComponenteVersion.tag == tag,
        )
    )
    if version is None:
        version = ComponenteVersion(componente_id=component.id, tag=tag, commit_sha=commit_sha)
        session.add(version)
        session.flush()
    return version


def accept_deployment(session: Session, request: DeploymentRequest) -> DeploymentAccepted:
    canonical = canonicalize_deployment(request)
    semantic_hash = _hash(canonical)
    deployment_data = canonical["deployment"]

    existing_event = _lookup_event(session, request.event_id)
    if existing_event is not None:
        return _classify_replay(existing_event, semantic_hash)

    try:
        with session.begin_nested():
            component = session.scalar(
                select(Componente).where(
                    Componente.gitlab_repositorio_id == canonical["component"]["component_id"]
                )
            )
            if component is None:
                raise ComponentNotFound()
            if component.estado != "ACTIVO":
                raise ComponentInactive()

            entorno = session.scalar(
                select(Entorno).where(Entorno.codigo == deployment_data["environment"].upper())
            )
            if entorno is None:
                raise InvalidEnvironment()

            component_environment = session.scalar(
                select(ComponenteEntorno)
                .where(
                    ComponenteEntorno.componente_id == component.id,
                    ComponenteEntorno.entorno_id == entorno.id,
                )
                .with_for_update()
            )
            if component_environment is None:
                raise DeploymentEnvironmentNotFound()

            persona, executed_by_text = _resolve_actor(session, request)

            event = _insert_registro(session, request)
            if event.despliegue_evento is not None:
                # A concurrent transaction committed the same event_id between
                # our pre-check and this insert (MySQL deduplicates via
                # ON DUPLICATE KEY UPDATE). Classify exactly as the sequential path.
                return _classify_replay(event, semantic_hash)

            version = _resolve_version(session, component, canonical)

            deployment = Despliegue(
                componente_entorno_id=component_environment.id,
                componente_version_id=version.id,
                resultado=OUTCOME_RESULTS[deployment_data["outcome"]],
                herramienta_origen="OCP",
                nro_ticket_invgate=request.ticket.ticket_id if request.ticket else None,
                espacio=deployment_data["space"] or component_environment.espacio,
                commit_sha=deployment_data["commit_sha"],
                referencia_ejecucion=None,
                ejecutado_por_persona_id=persona.id if persona is not None else None,
                ejecutado_por_texto=executed_by_text,
                fecha_inicio=request.started_at,
                fecha_fin=request.finished_at,
                mensaje_resultado=deployment_data["result_summary"],
                registro_id=event.id,
            )
            session.add(deployment)
            session.flush()

            if _should_advance_pointer(
                session, component_environment, request, deployment_data["outcome"]
            ):
                component_environment.version_vigente_id = version.id
                component_environment.despliegue_vigente_id = deployment.id

            recorded_at = _render_utc(datetime.now(UTC).replace(microsecond=0))
            outbox = SincronizacionOutbox(registro_integracion=event, estado="pending")
            session.add(outbox)
            session.flush()

            evidence = DespliegueEvento(
                registro_integracion=event,
                hash_semantico=semantic_hash,
                evidencia_canonica={
                    "deployment_id": deployment.id,
                    "component_id": component.id,
                    "component_environment_id": component_environment.id,
                    "persona_id": persona.id if persona is not None else None,
                    "synchronization_id": outbox.id,
                    "recorded_at": recorded_at,
                },
                actor_cuil=request.actor.cuil,
            )
            session.add(evidence)
            session.flush()

            return DeploymentAccepted(
                deployment_id=deployment.id,
                component_id=component.id,
                component_environment_id=component_environment.id,
                persona_id=persona.id if persona is not None else None,
                synchronization_id=outbox.id,
                recorded_at=recorded_at,
                idempotent_replay=False,
            )
    except IntegrityError:
        # The `(herramienta_origen, id_evento_origen)` unique key is the arbiter
        # for concurrent same-event writers. Re-read the winner and classify it
        # exactly as the sequential replay path.
        winner = _lookup_event(session, request.event_id)
        if winner is not None:
            return _classify_replay(winner, semantic_hash)
        raise
