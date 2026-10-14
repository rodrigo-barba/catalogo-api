from typing import TypedDict


class PullReadPage(TypedDict):
    items: list[dict[str, object]]
    next_cursor: str | None


class DeploymentHistoryItem(TypedDict):
    deployment_id: int
    event_id: str | None
    component_environment_id: int
    environment: str
    result: str
    ticket_id: str | None
    tag: str
    commit_sha: str | None
    result_summary: str | None
    actor: dict[str, object]
    started_at: str | None
    finished_at: str
