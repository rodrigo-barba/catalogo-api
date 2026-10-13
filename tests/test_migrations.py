import importlib.util
from pathlib import Path
from types import ModuleType


VERSIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"
BASELINE_PATH = VERSIONS_DIR / "20261013_baseline.py"
R1_SETUP_PATH = VERSIONS_DIR / "20261013_r1_setup.py"
R3_DEPLOYMENT_PATH = VERSIONS_DIR / "20261014_r3_deployment.py"
R2_DEVHUB_ACCESS_PATH = VERSIONS_DIR / "20261015_r2_devhub_access.py"


def load_revision(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OperationRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_baseline_stamp_revision_emits_no_ddl() -> None:
    baseline = load_revision(BASELINE_PATH)
    recorder = OperationRecorder()
    baseline.op = recorder

    baseline.upgrade()
    baseline.downgrade()

    assert baseline.down_revision is None
    assert recorder.calls == []


def test_r1_setup_upgrade_adds_only_additive_persistence_structures() -> None:
    r1_setup = load_revision(R1_SETUP_PATH)
    recorder = OperationRecorder()
    r1_setup.op = recorder

    r1_setup.upgrade()

    call_names = [name for name, _, _ in recorder.calls]
    created_tables = [args[0] for name, args, _ in recorder.calls if name == "create_table"]
    created_indexes = [args[0] for name, args, _ in recorder.calls if name == "create_index"]
    created_constraints = [args[0] for name, args, _ in recorder.calls if name == "create_unique_constraint"]

    assert r1_setup.down_revision == "20261013_baseline"
    assert created_tables == ["setup_evento", "sincronizacion_outbox"]
    assert "add_column" in call_names
    assert "uq_registro_integracion_evento_origen_global" in created_constraints
    assert "ix_setup_evento_hash_semantico" in created_indexes
    assert "drop_table" not in call_names


def test_r1_setup_downgrade_reverses_additive_ddl_without_touching_triggers() -> None:
    r1_setup = load_revision(R1_SETUP_PATH)
    recorder = OperationRecorder()
    r1_setup.op = recorder

    r1_setup.downgrade()

    call_names = [name for name, _, _ in recorder.calls]
    dropped_tables = [args[0] for name, args, _ in recorder.calls if name == "drop_table"]

    assert dropped_tables == ["sincronizacion_outbox", "setup_evento"]
    assert "drop_constraint" in call_names
    assert "drop_column" in call_names
    assert "execute" not in call_names


def test_r3_deployment_upgrade_creates_only_evidence_table() -> None:
    r3_deployment = load_revision(R3_DEPLOYMENT_PATH)
    recorder = OperationRecorder()
    r3_deployment.op = recorder

    r3_deployment.upgrade()

    call_names = [name for name, _, _ in recorder.calls]
    created_tables = [args[0] for name, args, _ in recorder.calls if name == "create_table"]
    created_indexes = [args[0] for name, args, _ in recorder.calls if name == "create_index"]
    create_table_args = next(args for name, args, _ in recorder.calls if name == "create_table")
    constraint_names = {getattr(arg, "name", None) for arg in create_table_args[1:]}

    assert r3_deployment.down_revision == "20261013_r1_setup"
    assert created_tables == ["despliegue_evento"]
    assert "uq_despliegue_evento_registro_integracion" in constraint_names
    assert "ix_despliegue_evento_hash_semantico" in created_indexes
    assert "drop_table" not in call_names


def test_r3_deployment_downgrade_drops_only_evidence_table() -> None:
    r3_deployment = load_revision(R3_DEPLOYMENT_PATH)
    recorder = OperationRecorder()
    r3_deployment.op = recorder

    r3_deployment.downgrade()

    call_names = [name for name, _, _ in recorder.calls]
    dropped_tables = [args[0] for name, args, _ in recorder.calls if name == "drop_table"]
    dropped_indexes = [args[0] for name, args, _ in recorder.calls if name == "drop_index"]

    assert dropped_tables == ["despliegue_evento"]
    assert "ix_despliegue_evento_hash_semantico" in dropped_indexes
    assert "execute" not in call_names


def test_r2_devhub_access_upgrade_adds_evidence_review_flag_and_relaxes_legacy_route_check() -> None:
    r2_devhub_access = load_revision(R2_DEVHUB_ACCESS_PATH)
    recorder = OperationRecorder()
    r2_devhub_access.op = recorder

    r2_devhub_access.upgrade()

    created_tables = [args[0] for name, args, _ in recorder.calls if name == "create_table"]
    created_indexes = [args[0] for name, args, _ in recorder.calls if name == "create_index"]
    created_checks = [args[0] for name, args, _ in recorder.calls if name == "create_check_constraint"]
    dropped_checks = [args[0] for name, args, _ in recorder.calls if name == "drop_constraint"]
    create_table_args = next(args for name, args, _ in recorder.calls if name == "create_table")
    constraint_names = {getattr(arg, "name", None) for arg in create_table_args[1:]}

    assert r2_devhub_access.down_revision == "20261014_r3_deployment"
    assert created_tables == ["acceso_devhub_evento"]
    assert "uq_acceso_devhub_evento_registro_integracion" in constraint_names
    assert "ix_acceso_devhub_evento_hash_semantico" in created_indexes
    assert "ck_acceso_devhub_2" in dropped_checks
    assert "ck_acceso_devhub_2" in created_checks
    assert any(
        name == "add_column"
        and args[0] == "acceso_devhub"
        and getattr(args[1], "name", None) == "ruta_requiere_revision"
        for name, args, _ in recorder.calls
    )
    assert "drop_table" not in [name for name, _, _ in recorder.calls]


def test_r2_devhub_access_downgrade_normalizes_routes_before_restoring_legacy_check() -> None:
    r2_devhub_access = load_revision(R2_DEVHUB_ACCESS_PATH)
    recorder = OperationRecorder()
    r2_devhub_access.op = recorder

    r2_devhub_access.downgrade()

    calls = recorder.calls
    call_names = [name for name, _, _ in calls]
    statements = [args[0] for name, args, _ in calls if name == "execute"]
    normalization_index = next(index for index, call in enumerate(calls) if call[0] == "execute")
    restore_index = next(
        index
        for index, call in enumerate(calls)
        if call[0] == "create_check_constraint" and call[1][0] == "ck_acceso_devhub_2"
    )

    assert any(
        statement.startswith("UPDATE acceso_devhub SET ruta_organigrama = SUBSTRING(ruta_organigrama, 2)")
        for statement in statements
    )
    assert normalization_index < restore_index
    assert [args[0] for name, args, _ in calls if name == "drop_table"] == ["acceso_devhub_evento"]
    assert "ix_acceso_devhub_evento_hash_semantico" in [args[0] for name, args, _ in calls if name == "drop_index"]
    assert "drop_column" in call_names
