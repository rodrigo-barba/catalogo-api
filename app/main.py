from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.setups import router as setups_router, validation_details

app = FastAPI()
app.include_router(setups_router)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    errors = error.errors()
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
