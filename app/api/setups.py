from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.setup import SetupAcceptanceError, SetupConflict, SetupRequest
from app.services.setup_acceptance import SetupAccepted, accept_setup


router = APIRouter(prefix="/api/v1", tags=["setups"])


def validation_details(errors: list[dict[str, object]]) -> list[dict[str, str]]:
    details = []
    for error in errors:
        location = list(error.get("loc", []))
        if location[:1] == ["body"]:
            location = location[1:]
        field = "body" if not location or any(not isinstance(part, str) for part in location) else ".".join(location)
        details.append({"field": field, "reason": str(error["type"])})
    return details


def error_response(error: SetupAcceptanceError, status_code: int) -> JSONResponse:
    details = []
    if isinstance(error, SetupConflict):
        details = [{"field": conflict.field, "reason": conflict.reason} for conflict in error.conflicts]
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.code.replace("_", " "),
                "details": details,
                "retryable": False,
            },
            "correlation_id": str(uuid4()),
        },
    )


def success_response(accepted: SetupAccepted) -> dict[str, object]:
    return {
        "event_id": accepted.event_id,
        "project": {"project_id": accepted.project_id},
        "component": {
            "component_id": accepted.component_id,
            "component_action": "created",
        },
        "repartition": {
            "organigrama_id": accepted.organigrama_id,
            "review_required": accepted.review_required,
        },
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "synchronization": {
            "synchronization_id": accepted.synchronization_id,
            "status": accepted.synchronization_status,
        },
        "idempotent_replay": accepted.idempotent_replay,
    }


def temporarily_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "temporarily_unavailable",
                "message": "persistence is temporarily unavailable",
                "details": [],
                "retryable": True,
            },
            "correlation_id": str(uuid4()),
        },
    )


@router.post("/setups", status_code=201, response_model=None)
def create_setup(
    request: SetupRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        accepted = accept_setup(session, request)
    except SetupAcceptanceError as error:
        return error_response(error, 409)
    except SQLAlchemyError:
        session.rollback()
        return temporarily_unavailable_response()
    response.status_code = 200 if accepted.idempotent_replay else 201
    return success_response(accepted)
