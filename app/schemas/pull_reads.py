from typing import TypedDict


class PullReadPage(TypedDict):
    items: list[dict[str, object]]
    next_cursor: str | None
