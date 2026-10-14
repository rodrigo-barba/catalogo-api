from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
import hmac
import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.catalog import (
    AccesoDevhub,
    AccesoDevhubUsuario,
    Componente,
    ComponenteEntorno,
    Despliegue,
    Entorno,
    Persona,
    Proyecto,
    RegistroIntegracion,
    SetupEvento,
)


DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_CURSOR_SECRET = b"catalogo-pull-reads-v1"
_PROJECTS_ENDPOINT = "projects"
_PROJECTS_ORDER = "project_id:asc"
_DEVHUB_HISTORY_ENDPOINT = "devhub-accesses"
_DEVHUB_HISTORY_ORDER = "access_id:asc"
_SETUPS_ENDPOINT = "components/setups"
_SETUPS_ORDER = "setup_event_id:asc"


class InvalidPagination(ValueError):
    pass


def normalize_route(route: str) -> str:
    return route[1:] if route.startswith("/") else route


def _encode_cursor(payload: dict[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_CURSOR_SECRET, body, sha256).digest()
    return urlsafe_b64encode(body + signature).decode().rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, object]:
    try:
        raw = urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        body, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.new(_CURSOR_SECRET, body, sha256).digest()):
            raise ValueError
        value = json.loads(body)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise InvalidPagination() from None
    if not isinstance(value, dict):
        raise InvalidPagination()
    return value


def _cursor_for_projects(route: str, cutoff: int, anchor: int) -> str:
    return _encode_cursor(
        {
            "endpoint": _PROJECTS_ENDPOINT,
            "filters": {"repartition_route": route},
            "order": _PROJECTS_ORDER,
            "cutoff": cutoff,
            "anchor": anchor,
        }
    )


def _cursor_for_history(endpoint: str, filters: dict[str, object], order: str, cutoff: int, anchor: int) -> str:
    return _encode_cursor(
        {"endpoint": endpoint, "filters": filters, "order": order, "cutoff": cutoff, "anchor": anchor}
    )


def _history_page(
    *,
    endpoint: str,
    filters: dict[str, object],
    order: str,
    cutoff: int | None,
    cursor: str | None,
    limit: int,
    offset: str | None,
) -> tuple[int, int] | None:
    if offset is not None or limit < 1 or limit > MAX_LIMIT:
        raise InvalidPagination()
    if cursor is None:
        return None if cutoff is None else (cutoff, 0)
    value = _decode_cursor(cursor)
    if (
        value.get("endpoint") != endpoint
        or value.get("filters") != filters
        or value.get("order") != order
        or not isinstance(value.get("cutoff"), int)
        or not isinstance(value.get("anchor"), int)
    ):
        raise InvalidPagination()
    return value["cutoff"], value["anchor"]


def _projects_statement(route: str):
    return select(Proyecto).where(or_(Proyecto.ruta_organigrama == route, Proyecto.ruta_organigrama == f"/{route}"))


def _project_item(project: Proyecto) -> dict[str, object]:
    return {
        "project_id": project.id,
        "platform_project_id": project.id_proyecto_plataforma,
        "name": project.nombre,
        "platform": project.plataforma.lower(),
        "state": project.estado,
        "setup_ticket_id": project.nro_ticket_setup,
        "repartition": {
            "route": project.ruta_organigrama,
            "organigrama_id": project.organigrama_id,
            "review_required": project.ruta_requiere_revision,
        },
    }


def list_projects(
    session: Session,
    *,
    repartition_route: str,
    limit: int,
    cursor: str | None,
    offset: str | None,
) -> dict[str, object]:
    if offset is not None or limit < 1 or limit > MAX_LIMIT:
        raise InvalidPagination()
    route = normalize_route(repartition_route)
    statement = _projects_statement(route)
    if cursor is None:
        cutoff = session.scalar(
            select(func.max(Proyecto.id)).where(
                or_(Proyecto.ruta_organigrama == route, Proyecto.ruta_organigrama == f"/{route}")
            )
        )
        anchor = 0
    else:
        value = _decode_cursor(cursor)
        if (
            value.get("endpoint") != _PROJECTS_ENDPOINT
            or value.get("filters") != {"repartition_route": route}
            or value.get("order") != _PROJECTS_ORDER
            or not isinstance(value.get("cutoff"), int)
            or not isinstance(value.get("anchor"), int)
        ):
            raise InvalidPagination()
        cutoff = value["cutoff"]
        anchor = value["anchor"]
    if cutoff is None:
        return {"items": [], "next_cursor": None}
    projects = list(
        session.scalars(
            statement.where(Proyecto.id <= cutoff, Proyecto.id > anchor).order_by(Proyecto.id).limit(limit + 1)
        )
    )
    has_next = len(projects) > limit
    page = projects[:limit]
    return {
        "items": [_project_item(project) for project in page],
        "next_cursor": _cursor_for_projects(route, cutoff, page[-1].id) if has_next else None,
    }


def project_components(session: Session, project_id: int) -> list[dict[str, object]] | None:
    project = session.get(Proyecto, project_id)
    if project is None:
        return None
    components = list(session.scalars(select(Componente).where(Componente.proyecto_id == project_id).order_by(Componente.id)))
    items = []
    for component in components:
        environments = []
        rows = session.execute(
            select(ComponenteEntorno, Entorno)
            .join(Entorno, Entorno.id == ComponenteEntorno.entorno_id)
            .where(ComponenteEntorno.componente_id == component.id)
            .order_by(Entorno.orden, Entorno.id)
        )
        for component_environment, environment in rows:
            current = (
                session.get(Despliegue, component_environment.despliegue_vigente_id)
                if component_environment.despliegue_vigente_id is not None
                else None
            )
            environments.append(
                {
                    "component_environment_id": component_environment.id,
                    "environment": {"environment_id": environment.id, "code": environment.codigo, "name": environment.nombre},
                    "space": component_environment.espacio,
                    "current_deployment": _deployment_item(current) if current is not None and current.resultado == "EXITOSO" else None,
                }
            )
        items.append(
            {
                "component_id": component.id,
                "name": component.nombre,
                "state": component.estado,
                "repository": {"repository_id": component.gitlab_repositorio_id, "url": component.gitlab_repositorio_url},
                "environments": environments,
            }
        )
    return items


def _deployment_item(deployment: Despliegue) -> dict[str, object]:
    return {
        "deployment_id": deployment.id,
        "result": deployment.resultado,
        "ticket_id": deployment.nro_ticket_invgate,
        "commit_sha": deployment.commit_sha,
        "finished_at": deployment.fecha_fin.isoformat(),
    }


def _person_item(person: Persona) -> dict[str, object]:
    return {"person_id": person.id, "cuil": person.cuil}


def _devhub_access_item(session: Session, access: AccesoDevhub) -> dict[str, object]:
    requested_by = session.get(Persona, access.dg_solicitante_persona_id) if access.dg_solicitante_persona_id else None
    authorized_users = list(
        session.scalars(
            select(Persona)
            .join(AccesoDevhubUsuario, AccesoDevhubUsuario.persona_id == Persona.id)
            .where(AccesoDevhubUsuario.acceso_devhub_id == access.id)
            .order_by(Persona.cuil, Persona.id)
        )
    )
    return {
        "access_id": access.id,
        "event_id": access.registro_integracion.id_evento_origen,
        "enabled_at": access.fecha_habilitacion.isoformat(),
        "ticket_id": access.nro_ticket_invgate,
        "repartition": {
            "route": access.ruta_organigrama,
            "organigrama_id": access.organigrama_id,
            "review_required": access.ruta_requiere_revision,
        },
        "requested_by": _person_item(requested_by) if requested_by is not None else None,
        "authorized_users": [_person_item(person) for person in authorized_users],
    }


def current_devhub_accesses(session: Session, *, repartition_route: str) -> dict[str, object]:
    route = normalize_route(repartition_route)
    accesses = list(
        session.scalars(
            select(AccesoDevhub)
            .where(
                or_(AccesoDevhub.ruta_organigrama == route, AccesoDevhub.ruta_organigrama == f"/{route}"),
                AccesoDevhub.estado == "ACTIVO",
            )
            .order_by(AccesoDevhub.id)
        )
    )
    return {"items": [_devhub_access_item(session, access) for access in accesses], "next_cursor": None}


def list_devhub_accesses(
    session: Session, *, repartition_route: str, limit: int, cursor: str | None, offset: str | None
) -> dict[str, object]:
    route = normalize_route(repartition_route)
    statement = select(AccesoDevhub).where(
        or_(AccesoDevhub.ruta_organigrama == route, AccesoDevhub.ruta_organigrama == f"/{route}")
    )
    access_rows = statement.subquery()
    page_state = _history_page(
        endpoint=_DEVHUB_HISTORY_ENDPOINT,
        filters={"repartition_route": route},
        order=_DEVHUB_HISTORY_ORDER,
        cutoff=session.scalar(select(func.max(access_rows.c.id))) if cursor is None else None,
        cursor=cursor,
        limit=limit,
        offset=offset,
    )
    if page_state is None:
        return {"items": [], "next_cursor": None}
    cutoff, anchor = page_state
    accesses = list(session.scalars(statement.where(AccesoDevhub.id <= cutoff, AccesoDevhub.id > anchor).order_by(AccesoDevhub.id).limit(limit + 1)))
    page = accesses[:limit]
    return {
        "items": [_devhub_access_item(session, access) for access in page],
        "next_cursor": _cursor_for_history(_DEVHUB_HISTORY_ENDPOINT, {"repartition_route": route}, _DEVHUB_HISTORY_ORDER, cutoff, page[-1].id)
        if len(accesses) > limit
        else None,
    }


def list_component_setups(
    session: Session, *, component_id: int, limit: int, cursor: str | None, offset: str | None
) -> dict[str, object] | None:
    if session.get(Componente, component_id) is None:
        return None
    statement = (
        select(SetupEvento)
        .join(RegistroIntegracion, SetupEvento.registro_integracion_id == RegistroIntegracion.id)
        .where(SetupEvento.evidencia_canonica["component_id"].as_integer() == component_id)
    )
    setup_rows = statement.subquery()
    page_state = _history_page(
        endpoint=_SETUPS_ENDPOINT,
        filters={"component_id": component_id},
        order=_SETUPS_ORDER,
        cutoff=session.scalar(select(func.max(setup_rows.c.id))) if cursor is None else None,
        cursor=cursor,
        limit=limit,
        offset=offset,
    )
    if page_state is None:
        return {"items": [], "next_cursor": None}
    cutoff, anchor = page_state
    setups = list(session.scalars(statement.where(SetupEvento.id <= cutoff, SetupEvento.id > anchor).order_by(SetupEvento.id).limit(limit + 1)))
    page = setups[:limit]
    items = [
        {
            "setup_event_id": setup.id,
            "event_id": setup.registro_integracion.id_evento_origen,
            "ticket_id": setup.registro_integracion.nro_ticket_invgate,
            "actor": {"cuil": setup.actor_cuil},
            "recorded_at": setup.creado_en.isoformat(),
        }
        for setup in page
    ]
    return {
        "items": items,
        "next_cursor": _cursor_for_history(_SETUPS_ENDPOINT, {"component_id": component_id}, _SETUPS_ORDER, cutoff, page[-1].id)
        if len(setups) > limit
        else None,
    }
