from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(StrictModel):
    system: Literal["openshift"]
    cluster_code: str = ""


class Repartition(StrictModel):
    route: str = Field(min_length=1)
    name: str = Field(min_length=1)

    @field_validator("route")
    @classmethod
    def route_is_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("route must start with a slash")
        return value


class Project(StrictModel):
    platform_project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class Component(StrictModel):
    component_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    git_repository_url: str = Field(min_length=1)

    @field_validator("git_repository_url")
    @classmethod
    def repository_url_is_http(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("repository URL must be HTTP(S)")
        return value


class Environment(StrictModel):
    environment: Literal["dev", "qa", "hml", "prod"]
    space: str = Field(min_length=1)


class Actor(StrictModel):
    cuil: str = Field(pattern=r"^[0-9]{11}$")


class Ticket(StrictModel):
    source_system: Literal["invgate"]
    ticket_id: str = Field(min_length=1)


class SetupRequest(StrictModel):
    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=1)
    source: Source
    repartition: Repartition
    project: Project
    component: Component
    environments: list[Environment] = Field(min_length=1)
    actor: Actor
    ticket: Ticket

    @field_validator("environments")
    @classmethod
    def environments_are_unique(cls, values: list[Environment]) -> list[Environment]:
        names = [value.environment for value in values]
        if len(names) != len(set(names)):
            raise ValueError("environments must not repeat")
        return values


@dataclass(frozen=True)
class Conflict:
    field: str
    reason: str


class SetupAcceptanceError(Exception):
    code: str


class ErrorEnvelope(StrictModel):
    code: str
    message: str
    details: list[Conflict]
    retryable: bool = False


class EventIdReused(SetupAcceptanceError):
    code = "event_id_reused"


class SetupDuplicate(SetupAcceptanceError):
    code = "setup_duplicate"


class SetupConflict(SetupAcceptanceError):
    code = "setup_conflict"

    def __init__(self, conflicts: list[Conflict]) -> None:
        self.conflicts = conflicts
        super().__init__(self.code)
