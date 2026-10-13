from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.main import app


def initialize_database(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE session_entries (value TEXT NOT NULL)"))


def saved_values(database_url: str) -> list[str]:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        return list(connection.execute(text("SELECT value FROM session_entries ORDER BY value")).scalars())


def test_session_dependency_commits_successful_request_work(tmp_path: Path) -> None:
    from app.db import get_session_for_settings

    database_url = f"sqlite+pysqlite:///{tmp_path / 'committed.db'}"
    initialize_database(database_url)
    dependency = get_session_for_settings(Settings(database_url=database_url))

    session = next(dependency)
    session.execute(text("INSERT INTO session_entries (value) VALUES ('committed')"))

    with pytest.raises(StopIteration):
        next(dependency)

    assert saved_values(database_url) == ["committed"]


def test_session_dependency_rolls_back_request_work_on_exception(tmp_path: Path) -> None:
    from app.db import get_session_for_settings

    database_url = f"sqlite+pysqlite:///{tmp_path / 'rolled-back.db'}"
    initialize_database(database_url)
    dependency = get_session_for_settings(Settings(database_url=database_url))

    session = next(dependency)
    session.execute(text("INSERT INTO session_entries (value) VALUES ('rolled-back')"))

    with pytest.raises(RuntimeError, match="request failed"):
        dependency.throw(RuntimeError("request failed"))

    assert saved_values(database_url) == []


def test_session_dependency_commits_work_when_invoked_by_fastapi(tmp_path: Path, monkeypatch) -> None:
    from app.db import get_session

    database_url = f"sqlite+pysqlite:///{tmp_path / 'fastapi.db'}"
    initialize_database(database_url)
    monkeypatch.setenv("CATALOGO_DATABASE_URL", database_url)
    get_settings.cache_clear()
    test_app = FastAPI()

    @test_app.post("/entries")
    def create_entry(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(text("INSERT INTO session_entries (value) VALUES ('fastapi')"))
        return {"status": "created"}

    response = TestClient(test_app).post("/entries")

    assert response.status_code == 200
    assert response.json() == {"status": "created"}
    assert saved_values(database_url) == ["fastapi"]
    get_settings.cache_clear()


def test_health_remains_database_independent_when_session_dependency_exists(monkeypatch) -> None:
    monkeypatch.delenv("CATALOGO_DATABASE_URL", raising=False)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
