from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.catalog import Despliegue, Entorno, Proyecto, SincronizacionOutbox
from tests.test_deployment_schemas import valid_failure_payload, valid_success_payload
from tests.test_setup_schemas import valid_payload

def client_with_engine() -> tuple[TestClient, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Entorno(id=1, codigo="DEV", nombre="Development", orden=1))
        session.commit()

    def get_test_session():
        session = Session(engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = get_test_session
    return TestClient(app), engine

def create_setup(
    client: TestClient,
    *,
    event_id: str,
    route: str = "/JGM/DGISIS",
    component_id: str = "dgisis/catalog-api",
    platform_project_id: str = "platform-1",
) -> dict[str, object]:
    payload = valid_payload()
    payload["event_id"] = event_id
    payload["repartition"]["route"] = route
    payload["component"]["component_id"] = component_id
    payload["component"]["name"] = component_id.rsplit("/", 1)[-1]
    payload["project"]["platform_project_id"] = platform_project_id
    payload["project"]["name"] = platform_project_id
    response = client.post("/api/v1/setups", json=payload)
    assert response.status_code == 201
    return response.json()

def test_projects_normalizes_optional_leading_slash_and_empty_routes_are_200() -> None:
    client, _engine = client_with_engine()
    created = create_setup(client, event_id="pull-setup-1")

    selected = client.get("/api/v1/projects", params={"repartition_route": "JGM/DGISIS"})
    empty = client.get("/api/v1/projects", params={"repartition_route": "/JGM/OTHER"})

    assert selected.status_code == 200
    assert selected.json()["items"] == [
        {
            "project_id": created["project"]["project_id"],
            "platform_project_id": "platform-1",
            "name": "platform-1",
            "platform": "ocp",
            "state": "ACTIVO",
            "setup_ticket_id": "SET-42",
            "repartition": {"route": "JGM/DGISIS", "organigrama_id": None, "review_required": True},
        }
    ]
    assert empty.json() == {"items": [], "next_cursor": None}

def test_projects_pagination_rejects_invalid_offset_and_cross_filter_cursors() -> None:
    client, _engine = client_with_engine()
    create_setup(client, event_id="pull-setup-1")
    create_setup(client, event_id="pull-setup-2", component_id="dgisis/catalog-web", platform_project_id="platform-2")

    first = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS", "limit": 1})
    cursor = first.json()["next_cursor"]
    invalid = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS", "cursor": "not-a-cursor"})
    offset = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS", "offset": 1})
    crossed = client.get("/api/v1/projects", params={"repartition_route": "/JGM/OTHER", "cursor": cursor})

    assert first.status_code == 200
    assert cursor
    for response in (invalid, offset, crossed):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_pagination"


def test_projects_use_default_and_max_limit_and_stable_cutoff() -> None:
    client, _engine = client_with_engine()
    for number in range(1, 52):
        create_setup(
            client,
            event_id=f"pull-setup-{number}",
            component_id=f"dgisis/component-{number}",
            platform_project_id=f"platform-{number}",
        )

    default_page = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS"})
    max_page = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS", "limit": 201})
    first = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS", "limit": 1})
    create_setup(
        client,
        event_id="pull-setup-later",
        component_id="dgisis/component-later",
        platform_project_id="platform-later",
    )
    continued = client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS", "cursor": first.json()["next_cursor"]})

    assert len(default_page.json()["items"]) == 50
    assert max_page.status_code == 400
    assert max_page.json()["error"]["code"] == "invalid_pagination"
    assert len(continued.json()["items"]) == 50
    assert all(item["platform_project_id"] != "platform-later" for item in continued.json()["items"])

def test_hierarchy_missing_project_is_404_and_components_show_only_current_success_by_environment() -> None:
    client, _engine = client_with_engine()
    setup = create_setup(client, event_id="pull-setup-1")
    project_id = setup["project"]["project_id"]

    success = valid_success_payload()
    success["event_id"] = "pull-deployment-success"
    success["component"]["component_id"] = "dgisis/catalog-api"
    success["finished_at"] = "2026-10-06T14:25:30Z"
    failed = valid_failure_payload()
    failed["event_id"] = "pull-deployment-failure"
    failed["component"]["component_id"] = "dgisis/catalog-api"
    failed["finished_at"] = "2026-10-06T14:26:30Z"
    assert client.post("/api/v1/deployments", json=success).status_code == 201
    assert client.post("/api/v1/deployments", json=failed).status_code == 201

    response = client.get(f"/api/v1/projects/{project_id}/components")
    missing = client.get("/api/v1/projects/999/components")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert response.status_code == 200
    component = response.json()["items"][0]
    assert component["repository"] == {"repository_id": "dgisis/catalog-api", "url": "https://git.example.invalid/dgisis/catalog-api"}
    deployment = component["environments"][0]["current_deployment"]
    assert deployment["deployment_id"] > 0
    assert deployment["result"] == "EXITOSO"
    assert deployment["finished_at"] == "2026-10-06T14:25:30"


def test_shared_gets_preserve_rows_and_outbox() -> None:
    client, engine = client_with_engine()
    setup = create_setup(client, event_id="pull-setup-1")
    with Session(engine) as session:
        before = {
            "projects": session.scalar(select(func.count()).select_from(Proyecto)),
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        }

    assert client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS"}).status_code == 200
    assert client.get(f"/api/v1/projects/{setup['project']['project_id']}/components").status_code == 200

    with Session(engine) as session:
        assert {
            "projects": session.scalar(select(func.count()).select_from(Proyecto)),
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        } == before
