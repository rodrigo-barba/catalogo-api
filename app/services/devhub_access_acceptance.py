from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.catalog import (
    AccesoDevhub,
    AccesoDevhubEvento,
    AccesoDevhubUsuario,
    Organigrama,
    Persona,
    RegistroIntegracion,
)
from app.schemas.devhub_access import DevhubAccessRequest


class DevhubAccessAcceptanceError(Exception):
    code: str


class EventIdReused(DevhubAccessAcceptanceError):
    code = "event_id_reused"


class TemporarilyUnavailable(DevhubAccessAcceptanceError):
    code = "temporarily_unavailable"


@dataclass(frozen=True)
class DevhubAccessAccepted:
    event_id: str
    devhub_access_id: int
    route: str
    organigrama_id: int | None
    review_required: bool
    authorized_cuils: tuple[str, ...]
    recorded_at: str
    idempotent_replay: bool = False


def _render_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _authorized_cuils(request: DevhubAccessRequest) -> tuple[str, ...]:
    return tuple(sorted({person.cuil for person in request.authorized_users}))


def canonicalize_devhub_access(request: DevhubAccessRequest) -> dict[str, object]:
    """Return the semantic fields, retaining raw request bytes separately in the ledger."""

    return {
        "occurred_at": _render_utc(request.occurred_at),
        "repartition": {
            "route": request.repartition.route,
            "name": request.repartition.name,
        },
        "ticket": {"source_system": request.ticket.source_system, "ticket_id": request.ticket.ticket_id},
        "requested_by": request.requested_by.cuil if request.requested_by is not None else None,
        "authorized_cuils": _authorized_cuils(request),
    }


def _semantic_hash(canonical: dict[str, object]) -> str:
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _is_mysql(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "mysql"


def _lookup_event(session: Session, event_id: str) -> RegistroIntegracion | None:
    return session.scalar(select(RegistroIntegracion).where(RegistroIntegracion.id_evento_origen == event_id))


def _result(event: RegistroIntegracion, evidence: AccesoDevhubEvento, *, replay: bool) -> DevhubAccessAccepted:
    result = evidence.evidencia_canonica
    return DevhubAccessAccepted(
        event_id=event.id_evento_origen or "",
        devhub_access_id=int(result["devhub_access_id"]),
        route=str(result["route"]),
        organigrama_id=result.get("organigrama_id"),
        review_required=bool(result["review_required"]),
        authorized_cuils=tuple(result["authorized_cuils"]),
        recorded_at=str(result["recorded_at"]),
        idempotent_replay=replay,
    )


def _classify_replay(event: RegistroIntegracion, semantic_hash: str) -> DevhubAccessAccepted:
    evidence = event.acceso_devhub_evento
    if evidence is None or evidence.hash_semantico != semantic_hash:
        raise EventIdReused()
    return _result(event, evidence, replay=True)


def _insert_event(session: Session, request: DevhubAccessRequest) -> RegistroIntegracion:
    values = {
        "tipo_registro": "R2_ACCESO",
        "id_evento_origen": request.event_id,
        "herramienta_origen": "DEVHUB",
        "nro_ticket_invgate": request.ticket.ticket_id,
        "fecha_evento": request.occurred_at,
        "payload": request.model_dump(mode="json"),
    }
    if _is_mysql(session):
        session.execute(
            mysql_insert(RegistroIntegracion)
            .values(**values)
            .on_duplicate_key_update(id=RegistroIntegracion.id)
        )
        event = _lookup_event(session, request.event_id)
        assert event is not None
        return event
    event = RegistroIntegracion(**values)
    session.add(event)
    session.flush()
    return event


def _resolve_person(session: Session, cuil: str) -> Persona:
    if _is_mysql(session):
        session.execute(mysql_insert(Persona).values(cuil=cuil).on_duplicate_key_update(id=Persona.id))
        person = session.scalar(select(Persona).where(Persona.cuil == cuil))
        assert person is not None
        return person
    person = session.scalar(select(Persona).where(Persona.cuil == cuil))
    if person is None:
        person = Persona(cuil=cuil)
        session.add(person)
        session.flush()
    return person


def _resolve_organigrama(session: Session, route: str) -> Organigrama | None:
    normalized = route.lstrip("/").casefold()
    return session.scalar(
        select(Organigrama).where(
            func.lower(Organigrama.ruta_completa) == normalized,
            Organigrama.es_vigente == 1,
        )
    )


def accept_devhub_access(session: Session, request: DevhubAccessRequest) -> DevhubAccessAccepted:
    canonical = canonicalize_devhub_access(request)
    semantic_hash = _semantic_hash(canonical)
    existing_event = _lookup_event(session, request.event_id)
    if existing_event is not None:
        return _classify_replay(existing_event, semantic_hash)

    try:
        with session.begin_nested():
            event = _insert_event(session, request)
            if event.acceso_devhub_evento is not None:
                return _classify_replay(event, semantic_hash)

            organigrama = _resolve_organigrama(session, request.repartition.route)
            requester = (
                _resolve_person(session, request.requested_by.cuil)
                if request.requested_by is not None
                else None
            )
            authorized_people = [_resolve_person(session, cuil) for cuil in _authorized_cuils(request)]
            access = AccesoDevhub(
                ruta_organigrama=request.repartition.route,
                organigrama=organigrama,
                dg_solicitante_persona_id=requester.id if requester is not None else None,
                nro_ticket_invgate=request.ticket.ticket_id,
                fecha_habilitacion=request.occurred_at,
                registro_id=event.id,
                ruta_requiere_revision=organigrama is None,
            )
            session.add(access)
            session.flush()
            session.add_all(
                [AccesoDevhubUsuario(acceso_devhub_id=access.id, persona_id=person.id) for person in authorized_people]
            )
            recorded_at = _render_utc(datetime.now(UTC).replace(microsecond=0))
            evidence = AccesoDevhubEvento(
                registro_integracion=event,
                hash_semantico=semantic_hash,
                evidencia_canonica={
                    "devhub_access_id": access.id,
                    "route": request.repartition.route,
                    "organigrama_id": organigrama.id if organigrama is not None else None,
                    "review_required": organigrama is None,
                    "authorized_cuils": list(_authorized_cuils(request)),
                    "recorded_at": recorded_at,
                },
            )
            session.add(evidence)
            session.flush()
            return _result(event, evidence, replay=False)
    except EventIdReused:
        raise
    except IntegrityError as error:
        winner = _lookup_event(session, request.event_id)
        if winner is not None:
            return _classify_replay(winner, semantic_hash)
        raise TemporarilyUnavailable() from error
    except Exception as error:
        raise TemporarilyUnavailable() from error
