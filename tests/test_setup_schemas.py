import pytest
from pydantic import ValidationError


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "ocp-setup-001",
        "source": {"system": "openshift"},
        "repartition": {"route": "/DGISIS/Platform", "name": "DGISIS"},
        "project": {"platform_project_id": "platform-1", "name": "Catalog"},
        "component": {
            "component_id": "dgisis/catalog-api",
            "name": "Catalog API",
            "git_repository_url": "https://git.example.invalid/dgisis/catalog-api/",
        },
        "environments": [{"environment": "dev", "space": "catalog-dev"}],
        "actor": {"cuil": "20123456789"},
        "ticket": {"source_system": "invgate", "ticket_id": "SET-42"},
    }


def test_setup_request_accepts_the_strict_r1_shape() -> None:
    from app.schemas.setup import SetupRequest

    request = SetupRequest.model_validate(valid_payload())

    assert request.schema_version == "1.0"
    assert request.actor.cuil == "20123456789"
    assert request.environments[0].environment == "dev"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(schema_version="2.0"),
        lambda payload: payload["source"].update(system="other"),
        lambda payload: payload["actor"].update(cuil="20-12345678-9"),
        lambda payload: payload.update(environments=[]),
        lambda payload: payload["ticket"].update(ticket_id=""),
    ],
)
def test_setup_request_rejects_invalid_required_values(mutate) -> None:
    from app.schemas.setup import SetupRequest

    payload = valid_payload()
    mutate(payload)

    with pytest.raises(ValidationError):
        SetupRequest.model_validate(payload)


def test_setup_request_forbids_unknown_and_explicit_null_fields() -> None:
    from app.schemas.setup import SetupRequest

    unknown = valid_payload() | {"unexpected": "value"}
    explicit_null = valid_payload()
    explicit_null["source"]["cluster_code"] = None

    with pytest.raises(ValidationError):
        SetupRequest.model_validate(unknown)
    with pytest.raises(ValidationError):
        SetupRequest.model_validate(explicit_null)


def test_setup_request_rejects_invalid_route_and_repository_url_and_exposes_error_shape() -> None:
    from app.schemas.setup import ErrorEnvelope, SetupRequest

    invalid_route = valid_payload()
    invalid_route["repartition"]["route"] = "not-a-route"
    invalid_url = valid_payload()
    invalid_url["component"]["git_repository_url"] = "not-a-url"

    with pytest.raises(ValidationError):
        SetupRequest.model_validate(invalid_route)
    with pytest.raises(ValidationError):
        SetupRequest.model_validate(invalid_url)

    envelope = ErrorEnvelope(code="invalid_route", message="Route is invalid", details=[])
    assert envelope.retryable is False
