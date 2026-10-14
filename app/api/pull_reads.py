from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.pull_reads import (
    DEFAULT_LIMIT,
    InvalidEnvironment,
    InvalidPagination,
    current_devhub_accesses,
    list_component_deployments,
    list_component_setups,
    list_devhub_accesses,
    list_projects,
    project_components,
)


router = APIRouter(prefix="/api/v1", tags=["pull-reads"])


def error_response(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": code.replace("_", " "), "details": [], "retryable": False}},
    )


@router.get("/projects", response_model=None)
def get_projects(
    repartition_route: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    offset: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        return list_projects(
            session,
            repartition_route=repartition_route,
            limit=limit,
            cursor=cursor,
            offset=offset,
        )
    except InvalidPagination:
        return error_response("invalid_pagination", 400)


@router.get("/projects/{project_id}/components", response_model=None)
def get_project_components(
    project_id: int,
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    components = project_components(session, project_id)
    if components is None:
        return error_response("not_found", 404)
    return {"items": components, "next_cursor": None}


@router.get("/devhub-accesses/current", response_model=None)
def get_current_devhub_accesses(
    repartition_route: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return current_devhub_accesses(session, repartition_route=repartition_route)


@router.get("/devhub-accesses", response_model=None)
def get_devhub_accesses(
    repartition_route: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    offset: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        return list_devhub_accesses(
            session, repartition_route=repartition_route, limit=limit, cursor=cursor, offset=offset
        )
    except InvalidPagination:
        return error_response("invalid_pagination", 400)


@router.get("/components/{component_id}/setups", response_model=None)
def get_component_setups(
    component_id: int,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    offset: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        setups = list_component_setups(session, component_id=component_id, limit=limit, cursor=cursor, offset=offset)
    except InvalidPagination:
        return error_response("invalid_pagination", 400)
    if setups is None:
        return error_response("not_found", 404)
    return setups


@router.get("/components/{component_id}/deployments", response_model=None)
def get_component_deployments(
    component_id: int,
    environment: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    offset: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        deployments = list_component_deployments(
            session,
            component_id=component_id,
            environment=environment,
            limit=limit,
            cursor=cursor,
            offset=offset,
        )
    except InvalidEnvironment:
        return error_response("invalid_environment", 400)
    except InvalidPagination:
        return error_response("invalid_pagination", 400)
    if deployments is None:
        return error_response("not_found", 404)
    return deployments
