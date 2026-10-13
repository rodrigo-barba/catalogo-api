from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


CUIL_PATTERN = re.compile(r"^[0-9]{11}$")
ROUTE_PATTERN = re.compile(r"^/?[^/]+(?:/[^/]+){0,4}$")
SUPPORTED_SCHEMA_VERSION = "1.0"


def _error(code: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(code, message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(StrictModel):
    system: Literal["devhub"]

    @field_validator("system", mode="before")
    @classmethod
    def system_is_devhub(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value != "devhub":
            raise _error("invalid_request", "source.system must be devhub")
        return value


class Repartition(StrictModel):
    route: str
    name: str

    @field_validator("route", mode="before")
    @classmethod
    def route_is_valid(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if not isinstance(value, str) or ROUTE_PATTERN.fullmatch(value) is None:
            raise _error("invalid_repartition_route", "repartition.route is not valid")
        return value

    @field_validator("name", mode="before")
    @classmethod
    def name_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @model_validator(mode="after")
    def name_is_present(self) -> Repartition:
        if not self.name:
            raise _error("invalid_request", "repartition.name is required")
        return self


class Ticket(StrictModel):
    source_system: Literal["invgate"]
    ticket_id: str

    @field_validator("source_system", mode="before")
    @classmethod
    def source_system_is_invgate(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value != "invgate":
            raise _error("invalid_request", "ticket reference is required")
        return value

    @field_validator("ticket_id", mode="before")
    @classmethod
    def ticket_id_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @model_validator(mode="after")
    def ticket_is_valid(self) -> Ticket:
        if not self.ticket_id:
            raise _error("invalid_request", "ticket reference is required")
        return self


class Person(StrictModel):
    cuil: str

    @field_validator("cuil", mode="before")
    @classmethod
    def cuil_is_ascii_digits(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if not isinstance(value, str) or CUIL_PATTERN.fullmatch(value) is None:
            raise _error("invalid_cuil", "cuil must be exactly 11 ASCII digits")
        return value


class DevhubAccessRequest(StrictModel):
    schema_version: Literal["1.0"]
    event_id: str
    occurred_at: datetime
    source: Source
    repartition: Repartition
    ticket: Ticket
    requested_by: Person | None = None
    authorized_users: list[Person]

    @field_validator("schema_version", mode="before")
    @classmethod
    def schema_version_is_supported(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if value != SUPPORTED_SCHEMA_VERSION:
            raise _error("unsupported_schema_version", "schema_version is not supported")
        return value

    @field_validator("event_id", mode="before")
    @classmethod
    def event_id_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @field_validator("occurred_at", mode="before")
    @classmethod
    def occurred_at_is_rfc3339(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as error:
                raise _error("invalid_occurred_at", "occurred_at must be RFC3339 UTC") from error
        return value

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise _error("invalid_occurred_at", "occurred_at must be RFC3339 UTC")
        return value.astimezone(UTC)

    @field_validator("requested_by", mode="before")
    @classmethod
    def requested_by_is_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @field_validator("authorized_users", mode="before")
    @classmethod
    def authorized_users_are_not_null(cls, value: object) -> object:
        if value is None:
            raise _error("invalid_null", "explicit null is not permitted")
        return value

    @model_validator(mode="after")
    def required_values_are_present(self) -> DevhubAccessRequest:
        if not self.event_id:
            raise _error("invalid_request", "event_id is required")
        if not self.authorized_users:
            raise _error("authorized_users_required", "authorized_users is required")
        return self
