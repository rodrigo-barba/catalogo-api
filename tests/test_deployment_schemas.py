import pytest
from pydantic import ValidationError


def valid_success_payload() -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "ocp-deployment-01J8EXAMPLE000001",
        "started_at": "2026-10-06T14:24:10Z",
        "finished_at": "2026-10-06T14:25:30Z",
        "source": {"system": "openshift", "cluster_code": "cluster-dev-01"},
        "component": {"component_id": "dgisis/api-identidad"},
        "actor": {"cuil": "20123456789", "username": "ocp-pipeline"},
        "deployment": {
            "environment": "dev",
            "space": "portal-ciudadano-dev",
            "outcome": "succeeded",
            "url": "https://api-identidad.dev.example.invalid",
            "tag": "2.4.1",
            "commit_sha": "9f6b1c4d8e2a7b5f3c1d0e9a8b7c6d5e4f3a2b1c",
        },
        "ticket": {"source_system": "invgate", "ticket_id": "INC-1042"},
    }


def valid_failure_payload() -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "ocp-deployment-01J8EXAMPLE000002",
        "started_at": "2026-10-06T13:58:00Z",
        "finished_at": "2026-10-06T14:03:00Z",
        "source": {"system": "openshift", "cluster_code": "cluster-dev-01"},
        "component": {"component_id": "dgisis/api-identidad"},
        "actor": {"cuil": "20123456789"},
        "deployment": {
            "environment": "dev",
            "space": "portal-ciudadano-dev",
            "outcome": "failed",
            "result_summary": "Image pull failed.",
            "tag": "2.4.2",
        },
    }


def error_types(error: ValidationError) -> set[str]:
    return {detail["type"] for detail in error.errors()}


def test_deployment_request_accepts_the_strict_r3_success_shape() -> None:
    from app.schemas.deployment import DeploymentRequest

    request = DeploymentRequest.model_validate(valid_success_payload())

    assert request.schema_version == "1.0"
    assert request.event_id == "ocp-deployment-01J8EXAMPLE000001"
    assert request.source.system == "openshift"
    assert request.source.cluster_code == "cluster-dev-01"
    assert request.component.component_id == "dgisis/api-identidad"
    assert request.actor.cuil == "20123456789"
    assert request.actor.username == "ocp-pipeline"
    assert request.deployment.environment == "dev"
    assert request.deployment.outcome == "succeeded"
    assert request.deployment.tag == "2.4.1"
    assert request.ticket is not None
    assert request.ticket.ticket_id == "INC-1042"


def test_deployment_request_accepts_minimal_failure_without_optional_fields() -> None:
    from app.schemas.deployment import DeploymentRequest

    request = DeploymentRequest.model_validate(valid_failure_payload())

    assert request.actor.username is None
    assert request.deployment.commit_sha is None
    assert request.deployment.url is None
    assert request.deployment.result_summary == "Image pull failed."
    assert request.ticket is None


@pytest.mark.parametrize(
    "mutate,expected_code",
    [
        (lambda payload: payload.update(schema_version="2.0"), "unsupported_schema_version"),
        (lambda payload: payload["source"].update(system="other"), "literal_error"),
        (lambda payload: payload["component"].update(component_id=""), "component_id_required"),
        (lambda payload: payload["actor"].update(cuil="20-12345678-9"), "invalid_cuil"),
        (lambda payload: payload["actor"].update(cuil="2012345678"), "invalid_cuil"),
        (lambda payload: payload["actor"].update(cuil="2012345678a"), "invalid_cuil"),
        (lambda payload: payload["actor"].update(username=""), "invalid_username"),
        (lambda payload: payload["deployment"].update(environment="staging"), "invalid_environment"),
        (lambda payload: payload["deployment"].update(outcome="partial"), "invalid_outcome"),
        (lambda payload: payload["deployment"].update(tag=""), "tag_required_for_deployment"),
        (lambda payload: payload["deployment"].update(commit_sha="not-a-sha"), "invalid_commit_sha"),
        (lambda payload: payload["deployment"].pop("url"), "url_required_for_success"),
        (lambda payload: payload["deployment"].update(url="not-a-url"), "invalid_url"),
        (lambda payload: payload["ticket"].update(ticket_id=""), "invalid_ticket"),
        (lambda payload: payload["ticket"].update(source_system="jira"), "invalid_ticket"),
        (lambda payload: payload.pop("deployment"), "deployment_required"),
        (lambda payload: payload["source"].update(cluster_code=None), "invalid_null"),
    ],
)
def test_deployment_request_rejects_invalid_contract_values(mutate, expected_code) -> None:
    from app.schemas.deployment import DeploymentRequest

    payload = valid_success_payload()
    mutate(payload)

    with pytest.raises(ValidationError) as error:
        DeploymentRequest.model_validate(payload)

    assert expected_code in error_types(error.value)


def test_deployment_request_rejects_failure_without_result_summary() -> None:
    from app.schemas.deployment import DeploymentRequest

    payload = valid_success_payload()
    payload["deployment"].update(outcome="failed")
    payload["deployment"].pop("url")

    with pytest.raises(ValidationError) as error:
        DeploymentRequest.model_validate(payload)

    assert "result_summary_required_for_failure" in error_types(error.value)


@pytest.mark.parametrize(
    "started_at,finished_at",
    [
        ("2026-10-06T14:25:30Z", "2026-10-06T14:24:10Z"),
        ("2026-10-06T14:24:10", "2026-10-06T14:25:30Z"),
        ("2026-10-06T14:24:10+03:00", "2026-10-06T14:25:30Z"),
        ("not-a-timestamp", "2026-10-06T14:25:30Z"),
    ],
)
def test_deployment_request_rejects_non_utc_or_disordered_attempt_times(started_at, finished_at) -> None:
    from app.schemas.deployment import DeploymentRequest

    payload = valid_success_payload()
    payload["started_at"] = started_at
    payload["finished_at"] = finished_at

    with pytest.raises(ValidationError) as error:
        DeploymentRequest.model_validate(payload)

    assert "invalid_attempt_time" in error_types(error.value)


def test_deployment_request_forbids_unknown_keys_and_explicit_nulls() -> None:
    from app.schemas.deployment import DeploymentRequest

    unknown = valid_success_payload() | {"unexpected": "value"}
    explicit_null = valid_success_payload()
    explicit_null["deployment"]["commit_sha"] = None

    with pytest.raises(ValidationError) as extra:
        DeploymentRequest.model_validate(unknown)
    with pytest.raises(ValidationError) as null:
        DeploymentRequest.model_validate(explicit_null)

    assert "extra_forbidden" in error_types(extra.value)
    assert "invalid_null" in error_types(null.value)


def test_deployment_schema_exposes_typed_acceptance_errors_and_conflict() -> None:
    from app.schemas.deployment import (
        ComponentInactive,
        ComponentNotFound,
        Conflict,
        DeploymentAcceptanceError,
        DeploymentEnvironmentNotFound,
        ErrorEnvelope,
        EventIdReused,
        IdentityConflict,
        InvalidEnvironment,
    )

    assert issubclass(ComponentNotFound, DeploymentAcceptanceError)
    assert issubclass(ComponentInactive, DeploymentAcceptanceError)
    assert issubclass(DeploymentEnvironmentNotFound, DeploymentAcceptanceError)
    assert issubclass(InvalidEnvironment, DeploymentAcceptanceError)
    assert issubclass(IdentityConflict, DeploymentAcceptanceError)
    assert issubclass(EventIdReused, DeploymentAcceptanceError)
    assert ComponentNotFound.code == "component_not_found"
    assert ComponentInactive.code == "component_inactive"
    assert DeploymentEnvironmentNotFound.code == "deployment_environment_not_found"
    assert InvalidEnvironment.code == "invalid_environment"
    assert IdentityConflict.code == "identity_conflict"
    assert EventIdReused.code == "event_id_reused"

    conflict = Conflict(field="actor.cuil", reason="differs")
    assert conflict.field == "actor.cuil"
    assert ErrorEnvelope(code="invalid_request", message="bad", details=[]).retryable is False
