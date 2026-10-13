import pytest
from pydantic import ValidationError


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "devhub-access-001",
        "occurred_at": "2026-10-06T14:10:00Z",
        "source": {"system": "devhub"},
        "repartition": {"route": "/JGM/DGISIS", "name": "DGISIS"},
        "ticket": {"source_system": "invgate", "ticket_id": "INC-1042"},
        "authorized_users": [{"cuil": "27345678901"}],
    }


def error_types(error: ValidationError) -> set[str]:
    return {detail["type"] for detail in error.errors()}


def test_devhub_access_request_accepts_required_r2_values_and_optional_requester() -> None:
    from app.schemas.devhub_access import DevhubAccessRequest

    request = DevhubAccessRequest.model_validate(valid_payload())

    assert request.event_id == "devhub-access-001"
    assert request.occurred_at.isoformat() == "2026-10-06T14:10:00+00:00"
    assert request.repartition.route == "/JGM/DGISIS"
    assert request.requested_by is None
    assert request.authorized_users[0].cuil == "27345678901"

    without_slash = valid_payload()
    without_slash["repartition"]["route"] = "JGM/DGISIS"
    with_requester = valid_payload()
    with_requester["requested_by"] = {"cuil": "20123456789"}

    assert DevhubAccessRequest.model_validate(without_slash).repartition.route == "JGM/DGISIS"
    assert DevhubAccessRequest.model_validate(with_requester).requested_by.cuil == "20123456789"


@pytest.mark.parametrize(
    "mutate,expected_code",
    [
        (lambda payload: payload.update(schema_version="2.0"), "unsupported_schema_version"),
        (lambda payload: payload.update(event_id=""), "invalid_request"),
        (lambda payload: payload.update(occurred_at="2026-10-06T14:10:00+03:00"), "invalid_occurred_at"),
        (lambda payload: payload["source"].update(system="openshift"), "invalid_request"),
        (lambda payload: payload["repartition"].update(route="//JGM/DGISIS"), "invalid_repartition_route"),
        (lambda payload: payload["repartition"].update(name=""), "invalid_request"),
        (lambda payload: payload["ticket"].update(source_system="jira"), "invalid_request"),
        (lambda payload: payload["ticket"].update(ticket_id=""), "invalid_request"),
        (lambda payload: payload.update(authorized_users=[]), "authorized_users_required"),
        (lambda payload: payload["authorized_users"][0].update(cuil="20-12345678-9"), "invalid_cuil"),
    ],
)
def test_devhub_access_request_rejects_invalid_r2_contract_values(mutate, expected_code) -> None:
    from app.schemas.devhub_access import DevhubAccessRequest

    payload = valid_payload()
    mutate(payload)

    with pytest.raises(ValidationError) as error:
        DevhubAccessRequest.model_validate(payload)

    assert expected_code in error_types(error.value)


def test_devhub_access_request_rejects_unknown_keys_and_explicit_nulls() -> None:
    from app.schemas.devhub_access import DevhubAccessRequest

    unknown = valid_payload() | {"unexpected": "value"}
    explicit_null = valid_payload()
    explicit_null["requested_by"] = None

    with pytest.raises(ValidationError) as extra:
        DevhubAccessRequest.model_validate(unknown)
    with pytest.raises(ValidationError) as null:
        DevhubAccessRequest.model_validate(explicit_null)

    assert "extra_forbidden" in error_types(extra.value)
    assert "invalid_null" in error_types(null.value)
