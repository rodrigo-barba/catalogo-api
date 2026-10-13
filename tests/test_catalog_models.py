from sqlalchemy import inspect


def test_catalog_metadata_includes_legacy_r1_r2_and_r3_persistence_tables() -> None:
    from app.models.base import Base
    from app.models.catalog import (
        Componente,
        ComponenteEntorno,
        ComponenteVersion,
        AccesoDevhub,
        AccesoDevhubEvento,
        AccesoDevhubUsuario,
        Despliegue,
        DespliegueEvento,
        Entorno,
        Organigrama,
        Persona,
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
        "persona",
        "componente_version",
        "despliegue",
        "despliegue_evento",
        "acceso_devhub",
        "acceso_devhub_usuario",
        "acceso_devhub_evento",
    }
    assert Organigrama.__tablename__ == "organigrama"
    assert Proyecto.__tablename__ == "proyecto"
    assert Componente.__tablename__ == "componente"
    assert ComponenteEntorno.__tablename__ == "componente_entorno"
    assert RegistroIntegracion.__tablename__ == "registro_integracion"
    assert SetupEvento.__tablename__ == "setup_evento"
    assert SincronizacionOutbox.__tablename__ == "sincronizacion_outbox"
    assert Persona.__tablename__ == "persona"
    assert ComponenteVersion.__tablename__ == "componente_version"
    assert Despliegue.__tablename__ == "despliegue"
    assert DespliegueEvento.__tablename__ == "despliegue_evento"
    assert AccesoDevhub.__tablename__ == "acceso_devhub"
    assert AccesoDevhubUsuario.__tablename__ == "acceso_devhub_usuario"
    assert AccesoDevhubEvento.__tablename__ == "acceso_devhub_evento"


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


def test_r3_deployment_tables_have_legacy_unique_constraints_and_evidence_index() -> None:
    from app.models.catalog import (
        ComponenteVersion,
        Despliegue,
        DespliegueEvento,
        RegistroIntegracion,
    )

    version_constraints = {constraint.name for constraint in ComponenteVersion.__table__.constraints}
    deployment_constraints = {constraint.name for constraint in Despliegue.__table__.constraints}
    evidence_constraints = {constraint.name for constraint in DespliegueEvento.__table__.constraints}
    evidence_indexes = {index.name for index in DespliegueEvento.__table__.indexes}

    assert "uq_componente_version_1" in version_constraints
    assert "uq_despliegue_1" in deployment_constraints
    assert "uq_despliegue_2" in deployment_constraints
    assert "uq_despliegue_evento_registro_integracion" in evidence_constraints
    assert "ix_despliegue_evento_hash_semantico" in evidence_indexes
    assert inspect(RegistroIntegracion).relationships.despliegue_evento.uselist is False


def test_r3_despliegue_required_columns_and_nullable_execution_reference() -> None:
    from app.models.catalog import Despliegue

    deployment_mapper = inspect(Despliegue)

    assert deployment_mapper.columns.componente_version_id.nullable is False
    assert deployment_mapper.columns.espacio.nullable is False
    assert deployment_mapper.columns.referencia_ejecucion.nullable is True
    assert deployment_mapper.columns.fecha_fin.nullable is False
    assert deployment_mapper.columns.registro_id.nullable is False


def test_r2_access_mappings_preserve_legacy_links_and_add_replay_evidence() -> None:
    from app.models.catalog import AccesoDevhub, AccesoDevhubEvento, AccesoDevhubUsuario, RegistroIntegracion

    access_mapper = inspect(AccesoDevhub)
    link_mapper = inspect(AccesoDevhubUsuario)
    evidence_constraints = {constraint.name for constraint in AccesoDevhubEvento.__table__.constraints}
    evidence_indexes = {index.name for index in AccesoDevhubEvento.__table__.indexes}

    assert access_mapper.columns.organigrama_id.nullable is True
    assert access_mapper.columns.dg_solicitante_persona_id.nullable is True
    assert access_mapper.columns.registro_id.nullable is False
    assert access_mapper.columns.ruta_requiere_revision.nullable is False
    assert link_mapper.columns.acceso_devhub_id.primary_key is True
    assert link_mapper.columns.persona_id.primary_key is True
    assert "uq_acceso_devhub_evento_registro_integracion" in evidence_constraints
    assert "ix_acceso_devhub_evento_hash_semantico" in evidence_indexes
    assert access_mapper.relationships.registro_integracion.uselist is False
    assert inspect(RegistroIntegracion).relationships.acceso_devhub_evento.uselist is False
