from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.pull_reads import DEFAULT_LIMIT, InvalidPagination, list_projects, project_components


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
