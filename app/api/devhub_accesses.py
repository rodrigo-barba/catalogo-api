from uuid import uuid4

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.devhub_access import DevhubAccessRequest
from app.services.devhub_access_acceptance import (
    DevhubAccessAcceptanceError,
    DevhubAccessAccepted,
    EventIdReused,
    TemporarilyUnavailable,
    accept_devhub_access,
)


router = APIRouter(prefix="/api/v1", tags=["devhub-accesses"])

R2_DEVHUB_ACCESSES_PATH = "/api/v1/devhub-accesses"
R2_UNPROCESSABLE_CODES = frozenset(
    {
        "invalid_null",
        "invalid_repartition_route",
        "invalid_occurred_at",
        "invalid_cuil",
        "authorized_users_required",
    }
)


def r2_validation_error(errors: list[dict[str, object]]) -> tuple[int, str]:
    for detail in errors:
        code = str(detail.get("type", ""))
        if code == "unsupported_schema_version":
            return 400, code
        if code in R2_UNPROCESSABLE_CODES:
            return 422, code
    if any(detail.get("input") is None for detail in errors):
        return 422, "invalid_null"
    return 400, "invalid_request"


def error_response(
    code: str,
    status_code: int,
    *,
    details: list[dict[str, str]] | None = None,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": "persistence is temporarily unavailable" if code == "temporarily_unavailable" else code.replace("_", " "),
                "details": details or [],
                "retryable": retryable,
                "correlation_id": str(uuid4()),
            }
        },
    )


def success_response(accepted: DevhubAccessAccepted) -> dict[str, object]:
    response = {
        "event_id": accepted.event_id,
        "devhub_access_id": accepted.devhub_access_id,
        "repartition": {
            "route": accepted.route,
            "organigrama_id": accepted.organigrama_id,
            "review_required": accepted.review_required,
        },
        "authorized_users": [{"cuil": cuil} for cuil in accepted.authorized_cuils],
        "recorded_at": accepted.recorded_at,
    }
    if accepted.idempotent_replay:
        response["idempotent_replay"] = True
    return response


@router.post("/devhub-accesses", status_code=201, response_model=None)
def create_devhub_access(
    request: DevhubAccessRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        accepted = accept_devhub_access(session, request)
    except EventIdReused:
        return error_response("event_id_reused", 409)
    except TemporarilyUnavailable:
        session.rollback()
        return error_response("temporarily_unavailable", 503, retryable=True)
    except DevhubAccessAcceptanceError as error:
        return error_response(error.code, 409)
    response.status_code = 200 if accepted.idempotent_replay else 201
    return success_response(accepted)
