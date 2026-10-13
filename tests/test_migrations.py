import importlib.util
from pathlib import Path
from types import ModuleType


VERSIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"
BASELINE_PATH = VERSIONS_DIR / "20261013_baseline.py"
R1_SETUP_PATH = VERSIONS_DIR / "20261013_r1_setup.py"


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
