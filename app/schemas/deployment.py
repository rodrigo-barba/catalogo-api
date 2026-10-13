from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _error(code: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(code, message)


CUIL_PATTERN = re.compile(r"^[0-9]{11}$")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
SUPPORTED_SCHEMA_VERSION = "1.0"
ENVIRONMENTS = ("dev", "qa", "hml", "prod")
OUTCOMES = ("succeeded", "failed")


class Source(StrictModel):
    system: Literal["openshift"]
    cluster_code: str = ""

    @field_validator("cluster_code", mode="before")
    @classmethod
    def cluster_code_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value


class Component(StrictModel):
    component_id: str = ""

    @field_validator("component_id", mode="before")
    @classmethod
    def component_id_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @model_validator(mode="after")
    def component_id_is_present(self) -> Component:
        if not self.component_id:
            raise _error("component_id_required", "component.component_id is required")
        return self


class Actor(StrictModel):
    cuil: str
    username: str | None = None

    @field_validator("cuil", mode="before")
    @classmethod
    def cuil_is_ascii_digits(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if not isinstance(value, str) or CUIL_PATTERN.fullmatch(value) is None:
            raise _error("invalid_cuil", "actor.cuil must be exactly 11 ASCII digits")
        return value

    @field_validator("username", mode="before")
    @classmethod
    def username_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @field_validator("username")
    @classmethod
    def username_is_not_empty(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise _error("invalid_username", "actor.username must not be empty")
        return value


class Ticket(StrictModel):
    source_system: Literal["invgate"]
    ticket_id: str = ""

    @field_validator("source_system", mode="before")
    @classmethod
    def source_system_is_invgate(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value != "invgate":
            raise _error("invalid_ticket", "ticket reference is not valid")
        return value

    @field_validator("ticket_id", mode="before")
    @classmethod
    def ticket_id_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @model_validator(mode="after")
    def ticket_id_is_present(self) -> Ticket:
        if not self.ticket_id:
            raise _error("invalid_ticket", "ticket reference is not valid")
        return self


class Deployment(StrictModel):
    environment: Literal["dev", "qa", "hml", "prod"]
    outcome: Literal["succeeded", "failed"]
    tag: str = ""
    commit_sha: str | None = None
    space: str | None = None
    url: str | None = None
    result_summary: str | None = None

    @field_validator("environment", mode="before")
    @classmethod
    def environment_is_supported(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value not in ENVIRONMENTS:
            raise _error("invalid_environment", "deployment.environment is not supported")
        return value

    @field_validator("outcome", mode="before")
    @classmethod
    def outcome_is_supported(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value not in OUTCOMES:
            raise _error("invalid_outcome", "deployment.outcome is not supported")
        return value

    @field_validator("tag", mode="before")
    @classmethod
    def tag_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @field_validator("commit_sha", "space", "url", "result_summary", mode="before")
    @classmethod
    def optional_fields_are_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @field_validator("commit_sha")
    @classmethod
    def commit_sha_is_traceable(cls, value: str | None) -> str | None:
        if value is not None and COMMIT_SHA_PATTERN.fullmatch(value) is None:
            raise _error("invalid_commit_sha", "deployment.commit_sha is not a traceable SHA")
        return value

    @model_validator(mode="after")
    def outcome_requirements_hold(self) -> Deployment:
        if not self.tag:
            raise _error("tag_required_for_deployment", "deployment.tag is required")
        if self.outcome == "succeeded":
            if not self.url:
                raise _error("url_required_for_success", "deployment.url is required on success")
            if not self.url.lower().startswith(("http://", "https://")):
                raise _error("invalid_url", "deployment.url must be HTTP(S)")
        else:
            if not self.result_summary:
                raise _error(
                    "result_summary_required_for_failure",
                    "deployment.result_summary is required on failure",
                )
            if self.url and not self.url.lower().startswith(("http://", "https://")):
                raise _error("invalid_url", "deployment.url must be HTTP(S)")
        return self


class DeploymentRequest(StrictModel):
    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    source: Source
    component: Component
    actor: Actor
    deployment: Deployment | None = None
    ticket: Ticket | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def schema_version_is_supported(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value != SUPPORTED_SCHEMA_VERSION:
            raise _error("unsupported_schema_version", "schema_version is not supported")
        return value

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def attempt_time_is_rfc3339(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as error:
                raise _error("invalid_attempt_time", "attempt time must be RFC3339") from error
            return parsed
        return value

    @field_validator("started_at", "finished_at")
    @classmethod
    def attempt_time_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise _error("invalid_attempt_time", "attempt time must be RFC3339 UTC")
        return value.astimezone(UTC)

    @field_validator("deployment", mode="before")
    @classmethod
    def deployment_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @field_validator("ticket", mode="before")
    @classmethod
    def ticket_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @model_validator(mode="after")
    def deployment_is_present(self) -> DeploymentRequest:
        if self.deployment is None:
            raise _error("deployment_required", "deployment object is required")
        return self

    @model_validator(mode="after")
    def attempt_times_are_ordered(self) -> DeploymentRequest:
        if self.finished_at < self.started_at:
            raise _error("invalid_attempt_time", "finished_at precedes started_at")
        return self


@dataclass(frozen=True)
class Conflict:
    field: str
    reason: str


class DeploymentAcceptanceError(Exception):
    code: str


class ComponentNotFound(DeploymentAcceptanceError):
    code = "component_not_found"


class ComponentInactive(DeploymentAcceptanceError):
    code = "component_inactive"


class DeploymentEnvironmentNotFound(DeploymentAcceptanceError):
    code = "deployment_environment_not_found"


class InvalidEnvironment(DeploymentAcceptanceError):
    code = "invalid_environment"


class IdentityConflict(DeploymentAcceptanceError):
    code = "identity_conflict"


class EventIdReused(DeploymentAcceptanceError):
    code = "event_id_reused"


class ErrorEnvelope(StrictModel):
    code: str
    message: str
    details: list[Conflict]
    retryable: bool = False
