from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.deployments import r3_validation_error
from app.api.deployments import router as deployments_router
from app.api.setups import router as setups_router, validation_details

R3_DEPLOYMENTS_PATH = "/api/v1/deployments"

app = FastAPI()
app.include_router(setups_router)
app.include_router(deployments_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    errors = error.errors()
    if request.url.path == R3_DEPLOYMENTS_PATH:
        status_code, code = r3_validation_error(errors)
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": code.replace("_", " "),
                    "details": validation_details(errors),
                    "retryable": False,
                },
                "correlation_id": str(uuid4()),
            },
        )
    is_explicit_null = any(detail.get("input") is None for detail in errors)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_null" if is_explicit_null else "validation_error",
                "message": "explicit null is not permitted" if is_explicit_null else "request validation failed",
                "details": validation_details(errors),
                "retryable": False,
            },
            "correlation_id": str(uuid4()),
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
