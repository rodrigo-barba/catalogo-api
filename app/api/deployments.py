from uuid import uuid4

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.deployment import DeploymentAcceptanceError, DeploymentRequest
from app.services.deployment_acceptance import DeploymentAccepted, accept_deployment


router = APIRouter(prefix="/api/v1", tags=["deployments"])


# Contract codes raised by the R3 schema as Pydantic custom errors. `main.py`
# translates request-validation failures on the deployments path to these codes
# while leaving the R1 setups path untouched.
R3_CONTRACT_CODES = frozenset(
    {
        "unsupported_schema_version",
        "invalid_null",
        "invalid_outcome",
        "invalid_environment",
        "tag_required_for_deployment",
        "invalid_commit_sha",
        "invalid_username",
        "url_required_for_success",
        "result_summary_required_for_failure",
        "invalid_ticket",
        "invalid_cuil",
        "invalid_url",
        "invalid_attempt_time",
        "component_id_required",
        "deployment_required",
    }
)

CONFLICT_CODES = frozenset({"identity_conflict", "event_id_reused"})


def r3_validation_error(errors: list[dict[str, object]]) -> tuple[int, str]:
    """Translate R3 validation failures into the contract's HTTP status and code."""

    for detail in errors:
        contract_code = str(detail.get("type", ""))
        if contract_code in R3_CONTRACT_CODES:
            status_code = 400 if contract_code == "unsupported_schema_version" else 422
            return status_code, contract_code
    if any(detail.get("input") is None for detail in errors):
        return 422, "invalid_null"
    return 400, "invalid_request"


def error_response(error: DeploymentAcceptanceError) -> JSONResponse:
    status_code = 409 if error.code in CONFLICT_CODES else 422
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.code.replace("_", " "),
                "details": [],
                "retryable": False,
            },
            "correlation_id": str(uuid4()),
        },
    )


def success_response(accepted: DeploymentAccepted, request: DeploymentRequest) -> dict[str, object]:
    return {
        "deployment_id": accepted.deployment_id,
        "event_id": request.event_id,
        "component": {"component_id": accepted.component_id},
        "component_environment": {
            "component_environment_id": accepted.component_environment_id,
        },
        "actor": {
            "cuil": request.actor.cuil,
            "username": request.actor.username,
            "persona_id": accepted.persona_id,
        },
        "component_action": "component.resolved",
        "recorded_at": accepted.recorded_at,
        "synchronization": {
            "synchronization_id": accepted.synchronization_id,
            "status": "pending",
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


@router.post("/deployments", status_code=201, response_model=None)
def create_deployment(
    request: DeploymentRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, object] | JSONResponse:
    try:
        accepted = accept_deployment(session, request)
    except DeploymentAcceptanceError as error:
        return error_response(error)
    except SQLAlchemyError:
        session.rollback()
        return temporarily_unavailable_response()
    response.status_code = 200 if accepted.idempotent_replay else 201
    return success_response(accepted, request)
