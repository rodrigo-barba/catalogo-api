from sqlalchemy import inspect


def test_catalog_metadata_includes_legacy_and_r1_persistence_tables() -> None:
    from app.models.base import Base
    from app.models.catalog import (
            Componente,
            ComponenteEntorno,
            Entorno,
        Organigrama,
        Proyecto,
        RegistroIntegracion,
        SetupEvento,
        SincronizacionOutbox,
    )

    assert set(Base.metadata.tables) == {
        "organigrama",
        "proyecto",
        "componente",
            "componente_entorno",
            "entorno",
        "registro_integracion",
        "setup_evento",
        "sincronizacion_outbox",
    }
    assert Organigrama.__tablename__ == "organigrama"
    assert Proyecto.__tablename__ == "proyecto"
    assert Componente.__tablename__ == "componente"
    assert ComponenteEntorno.__tablename__ == "componente_entorno"
    assert RegistroIntegracion.__tablename__ == "registro_integracion"
    assert SetupEvento.__tablename__ == "setup_evento"
    assert SincronizacionOutbox.__tablename__ == "sincronizacion_outbox"


def test_project_route_link_is_nullable_and_has_an_explicit_review_state() -> None:
    from app.models.catalog import Proyecto

    project_mapper = inspect(Proyecto)

    assert project_mapper.columns.organigrama_id.nullable is True
    assert project_mapper.columns.ruta_requiere_revision.nullable is False
    assert project_mapper.relationships.organigrama.uselist is False


def test_event_evidence_and_outbox_are_unique_per_integration_event() -> None:
    from app.models.catalog import RegistroIntegracion, SetupEvento, SincronizacionOutbox

    evidence_constraints = {constraint.name for constraint in SetupEvento.__table__.constraints}
    outbox_constraints = {constraint.name for constraint in SincronizacionOutbox.__table__.constraints}

    assert "uq_setup_evento_registro_integracion" in evidence_constraints
    assert "uq_sincronizacion_outbox_registro_integracion" in outbox_constraints
    assert inspect(RegistroIntegracion).relationships.setup_evento.uselist is False
    assert inspect(RegistroIntegracion).relationships.sincronizacion_outbox.uselist is False
