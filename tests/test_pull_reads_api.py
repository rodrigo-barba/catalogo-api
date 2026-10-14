from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.main import app
from app.models.base import Base
from app.models.catalog import (
    AccesoDevhub,
    Despliegue,
    Entorno,
    Persona,
    Proyecto,
    RegistroIntegracion,
    SetupEvento,
    SincronizacionOutbox,
)
from tests.test_devhub_access_schemas import valid_payload as valid_devhub_payload
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


def create_devhub_access(client: TestClient, *, event_id: str, route: str = "/JGM/DGISIS") -> dict[str, object]:
    payload = valid_devhub_payload()
    payload["event_id"] = event_id
    payload["repartition"]["route"] = route
    payload["requested_by"] = {"cuil": "20123456789"}
    payload["authorized_users"] = [{"cuil": "27345678901"}, {"cuil": "20456789012"}]
    response = client.post("/api/v1/devhub-accesses", json=payload)
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


def test_all_gets_preserve_rows_and_outbox() -> None:
    client, engine = client_with_engine()
    setup = create_setup(client, event_id="pull-setup-1")
    create_devhub_access(client, event_id="pull-devhub-1")
    with Session(engine) as session:
        before = {
            "projects": session.scalar(select(func.count()).select_from(Proyecto)),
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "accesses": session.scalar(select(func.count()).select_from(AccesoDevhub)),
            "setup_events": session.scalar(select(func.count()).select_from(SetupEvento)),
            "people": session.scalar(select(func.count()).select_from(Persona)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        }

    assert client.get("/api/v1/projects", params={"repartition_route": "/JGM/DGISIS"}).status_code == 200
    assert client.get(f"/api/v1/projects/{setup['project']['project_id']}/components").status_code == 200
    assert client.get("/api/v1/devhub-accesses/current", params={"repartition_route": "/JGM/DGISIS"}).status_code == 200
    assert client.get("/api/v1/devhub-accesses", params={"repartition_route": "/JGM/DGISIS"}).status_code == 200
    assert client.get(f"/api/v1/components/{setup['component']['component_id']}/setups").status_code == 200

    with Session(engine) as session:
        assert {
            "projects": session.scalar(select(func.count()).select_from(Proyecto)),
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "accesses": session.scalar(select(func.count()).select_from(AccesoDevhub)),
            "setup_events": session.scalar(select(func.count()).select_from(SetupEvento)),
            "people": session.scalar(select(func.count()).select_from(Persona)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        } == before


def test_devhub_current_returns_all_active_rows_with_distinct_people_and_empty_routes() -> None:
    client, engine = client_with_engine()
    first = create_devhub_access(client, event_id="devhub-pull-1")
    second = create_devhub_access(client, event_id="devhub-pull-2", route="JGM/DGISIS")
    with Session(engine) as session:
        session.add(AccesoDevhub(
            ruta_organigrama="/JGM/DGISIS",
            nro_ticket_invgate="INC-INACTIVE",
            fecha_habilitacion=session.get(AccesoDevhub, first["devhub_access_id"]).fecha_habilitacion,
            estado="INACTIVO",
            registro_id=session.scalar(select(RegistroIntegracion.id).order_by(RegistroIntegracion.id)),
            ruta_requiere_revision=True,
        ))
        session.commit()

    current = client.get("/api/v1/devhub-accesses/current", params={"repartition_route": "JGM/DGISIS"})
    empty = client.get("/api/v1/devhub-accesses/current", params={"repartition_route": "/JGM/OTHER"})

    assert current.status_code == 200
    assert [item["access_id"] for item in current.json()["items"]] == [first["devhub_access_id"], second["devhub_access_id"]]
    assert current.json()["items"][0] == {
        "access_id": first["devhub_access_id"],
        "event_id": "devhub-pull-1",
        "enabled_at": current.json()["items"][0]["enabled_at"],
        "ticket_id": "INC-1042",
        "repartition": {"route": "/JGM/DGISIS", "organigrama_id": None, "review_required": True},
        "requested_by": {"person_id": 1, "cuil": "20123456789"},
        "authorized_users": [{"person_id": 2, "cuil": "20456789012"}, {"person_id": 3, "cuil": "27345678901"}],
    }
    assert empty.json() == {"items": [], "next_cursor": None}


def test_devhub_history_uses_stable_keyset_pagination_and_gets_do_not_mutate() -> None:
    client, engine = client_with_engine()
    first = create_devhub_access(client, event_id="devhub-history-1")
    create_devhub_access(client, event_id="devhub-history-2")
    with Session(engine) as session:
        before = {
            "accesses": session.scalar(select(func.count()).select_from(AccesoDevhub)),
            "people": session.scalar(select(func.count()).select_from(Persona)),
            "setup_events": session.scalar(select(func.count()).select_from(SetupEvento)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        }

    page_one = client.get("/api/v1/devhub-accesses", params={"repartition_route": "/JGM/DGISIS", "limit": 1})
    create_devhub_access(client, event_id="devhub-history-later")
    page_two = client.get("/api/v1/devhub-accesses", params={"repartition_route": "/JGM/DGISIS", "cursor": page_one.json()["next_cursor"]})

    assert page_one.status_code == 200
    assert page_one.json()["items"][0]["access_id"] == first["devhub_access_id"]
    assert page_one.json()["items"][0]["requested_by"]["cuil"] == "20123456789"
    assert page_one.json()["items"][0]["authorized_users"][0]["cuil"] == "20456789012"
    assert [item["event_id"] for item in page_two.json()["items"]] == ["devhub-history-2"]
    with Session(engine) as session:
        assert {
            "accesses": session.scalar(select(func.count()).select_from(AccesoDevhub)),
            "people": session.scalar(select(func.count()).select_from(Persona)),
            "setup_events": session.scalar(select(func.count()).select_from(SetupEvento)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        } == {**before, "accesses": before["accesses"] + 1}


def test_setup_history_paginates_persisted_evidence_and_missing_component_is_404() -> None:
    client, engine = client_with_engine()
    first = create_setup(client, event_id="setup-history-1")
    component_id = first["component"]["component_id"]
    with Session(engine) as session:
        event = RegistroIntegracion(
            id_evento_origen="setup-history-2",
            tipo_registro="R1_ALTA",
            herramienta_origen="OCP",
            nro_ticket_invgate="SET-43",
            payload={},
        )
        session.add(event)
        session.flush()
        session.add(SetupEvento(
            registro_integracion=event,
            hash_semantico="setup-history-2",
            evidencia_canonica={"component_id": component_id},
            actor_cuil="20987654321",
        ))
        session.commit()

    history = client.get(f"/api/v1/components/{component_id}/setups", params={"limit": 1})
    continued = client.get(f"/api/v1/components/{component_id}/setups", params={"cursor": history.json()["next_cursor"]})
    missing = client.get("/api/v1/components/999/setups")

    assert history.status_code == 200
    assert history.json()["items"] == [{
        "setup_event_id": 1,
        "event_id": "setup-history-1",
        "ticket_id": "SET-42",
        "actor": {"cuil": "20123456789"},
        "recorded_at": history.json()["items"][0]["recorded_at"],
    }]
    assert history.json()["next_cursor"]
    assert continued.json()["items"] == [{
        "setup_event_id": 2,
        "event_id": "setup-history-2",
        "ticket_id": "SET-43",
        "actor": {"cuil": "20987654321"},
        "recorded_at": continued.json()["items"][0]["recorded_at"],
    }]
    assert continued.json()["next_cursor"] is None
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_deployments_history_filters_paginates_and_preserves_identity_and_outbox() -> None:
    client, engine = client_with_engine()
    setup = create_setup(client, event_id="deployment-history-setup")
    component_id = setup["component"]["component_id"]
    empty_component = create_setup(
        client,
        event_id="deployment-history-empty-setup",
        component_id="dgisis/catalog-empty",
        platform_project_id="platform-empty",
    )
    with Session(engine) as session:
        session.add(Persona(cuil="20123456789", usuario_ad="ocp-pipeline"))
        session.commit()

    success = valid_success_payload()
    success["event_id"] = "deployment-history-success"
    success["component"]["component_id"] = "dgisis/catalog-api"
    success["finished_at"] = "2026-10-06T14:25:30Z"
    failed = valid_failure_payload()
    failed["event_id"] = "deployment-history-failure"
    failed["component"]["component_id"] = "dgisis/catalog-api"
    failed["finished_at"] = "2026-10-06T14:26:30Z"
    assert client.post("/api/v1/deployments", json=success).status_code == 201
    assert client.post("/api/v1/deployments", json=failed).status_code == 201

    with Session(engine) as session:
        before = {
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        }

    first = client.get(f"/api/v1/components/{component_id}/deployments", params={"environment": "dev", "limit": 1})
    later = valid_success_payload()
    later["event_id"] = "deployment-history-later"
    later["component"]["component_id"] = "dgisis/catalog-api"
    later["finished_at"] = "2026-10-06T14:27:30Z"
    assert client.post("/api/v1/deployments", json=later).status_code == 201
    continued = client.get(f"/api/v1/components/{component_id}/deployments", params={"environment": "dev", "cursor": first.json()["next_cursor"]})
    all_environments = client.get(f"/api/v1/components/{component_id}/deployments")
    invalid_environment = client.get(f"/api/v1/components/{component_id}/deployments", params={"environment": "staging"})
    missing = client.get("/api/v1/components/999/deployments")
    empty = client.get(f"/api/v1/components/{empty_component['component']['component_id']}/deployments")

    assert first.status_code == 200
    assert first.json()["items"] == [{
        "deployment_id": 1,
        "event_id": "deployment-history-success",
        "component_environment_id": 1,
        "environment": "DEV",
        "result": "EXITOSO",
        "ticket_id": "INC-1042",
        "tag": "2.4.1",
        "commit_sha": "9f6b1c4d8e2a7b5f3c1d0e9a8b7c6d5e4f3a2b1c",
        "result_summary": None,
        "actor": {"person_id": 1, "cuil": "20123456789", "username": "ocp-pipeline"},
        "started_at": "2026-10-06T14:24:10",
        "finished_at": "2026-10-06T14:25:30",
    }]
    assert first.json()["next_cursor"]
    assert continued.status_code == 200
    assert [item["event_id"] for item in continued.json()["items"]] == ["deployment-history-failure"]
    assert continued.json()["items"][0]["result"] == "FALLIDO"
    assert continued.json()["items"][0]["ticket_id"] is None
    assert continued.json()["items"][0]["result_summary"] == "Image pull failed."
    assert [item["event_id"] for item in all_environments.json()["items"]] == [
        "deployment-history-success",
        "deployment-history-failure",
        "deployment-history-later",
    ]
    assert empty.json() == {"items": [], "next_cursor": None}
    assert invalid_environment.status_code == 400
    assert invalid_environment.json()["error"]["code"] == "invalid_environment"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    with Session(engine) as session:
        assert {
            "deployments": session.scalar(select(func.count()).select_from(Despliegue)),
            "outbox": session.scalar(select(func.count()).select_from(SincronizacionOutbox)),
        } == {"deployments": before["deployments"] + 1, "outbox": before["outbox"] + 1}


def test_history_routes_reject_invalid_pagination() -> None:
    client, _engine = client_with_engine()

    setup = create_setup(client, event_id="invalid-pagination-setup-1")
    component_id = str(setup["component"]["component_id"])
    with Session(_engine) as session:
        event = RegistroIntegracion(
            id_evento_origen="invalid-pagination-setup-2",
            tipo_registro="R1_ALTA",
            herramienta_origen="OCP",
            nro_ticket_invgate="SET-43",
            payload={},
        )
        session.add(event)
        session.flush()
        session.add(
            SetupEvento(
                registro_integracion=event,
                hash_semantico="invalid-pagination-setup-2",
                evidencia_canonica={"component_id": component_id},
                actor_cuil="20987654321",
            )
        )
        session.commit()
    other_setup = create_setup(
        client,
        event_id="invalid-pagination-setup-other",
        component_id="dgisis/catalog-web",
        platform_project_id="invalid-pagination-other",
    )
    setup_route = f"/api/v1/components/{component_id}/setups"
    setup_cursor = client.get(setup_route, params={"limit": 1}).json()["next_cursor"]

    with Session(_engine) as session:
        session.add(Persona(cuil="20123456789", usuario_ad="ocp-pipeline"))
        session.commit()

    deployment = valid_success_payload()
    deployment["event_id"] = "invalid-pagination-deployment-1"
    deployment["component"]["component_id"] = "dgisis/catalog-api"
    deployment["finished_at"] = "2026-10-06T14:25:30Z"
    assert client.post("/api/v1/deployments", json=deployment).status_code == 201
    deployment["event_id"] = "invalid-pagination-deployment-2"
    deployment["finished_at"] = "2026-10-06T14:26:30Z"
    assert client.post("/api/v1/deployments", json=deployment).status_code == 201
    deployments_route = f"/api/v1/components/{component_id}/deployments"
    deployments_cursor = client.get(deployments_route, params={"limit": 1}).json()["next_cursor"]

    create_devhub_access(client, event_id="invalid-pagination-devhub-1")
    create_devhub_access(client, event_id="invalid-pagination-devhub-2")
    devhub_route = "/api/v1/devhub-accesses"
    devhub_params = {"repartition_route": "/JGM/DGISIS"}
    devhub_cursor = client.get(devhub_route, params={**devhub_params, "limit": 1}).json()["next_cursor"]

    cases = (
        (devhub_route, devhub_params, devhub_cursor, devhub_route, {"repartition_route": "/JGM/OTHER"}),
        (setup_route, {}, setup_cursor, f"/api/v1/components/{other_setup['component']['component_id']}/setups", {}),
        (deployments_route, {}, deployments_cursor, deployments_route, {"environment": "DEV"}),
    )
    for route, params, cursor, cross_route, cross_filter in cases:
        requests = (
            client.get(route, params={**params, "limit": 201}),
            client.get(route, params={**params, "offset": 1}),
            client.get(route, params={**params, "cursor": "not-a-cursor"}),
            client.get(cross_route, params={**params, **cross_filter, "cursor": cursor}),
        )
        assert cursor
        for response in requests:
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "invalid_pagination"
