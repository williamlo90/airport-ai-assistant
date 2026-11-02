"""Offline checks for migration packaging, validation and failure handling."""

from contextlib import contextmanager
from pathlib import Path

import airport_assistant
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from airport_assistant.infrastructure.database import migrations


def test_packaged_revision_has_one_head():
    package = Path(airport_assistant.__file__).resolve().parent
    directory = migrations.migration_directory()
    if package.parent.name != "src":
        assert directory == package / "_migrations"
    config = Config()
    config.set_main_option("script_location", str(directory))
    assert ScriptDirectory.from_config(config).get_heads() == ["0002_runtime_rls"]


@pytest.mark.parametrize("schema", ["public", "pg_catalog", "app;DROP SCHEMA app", 'a"b', "", "a" * 64])
def test_invalid_schema_rejected_before_connection(schema, monkeypatch):
    monkeypatch.setattr(migrations, "create_migration_engine", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(migrations.MigrationError):
        migrations.migrate("not-a-real-secret", schema=schema)


def test_failed_upgrade_rolls_back_disposes_and_redacts(monkeypatch):
    state = []
    class Connection:
        class dialect:
            class identifier_preparer:
                @staticmethod
                def quote_identifier(value):
                    return '"' + value + '"'
        def exec_driver_sql(self, *args, **kwargs):
            pass
        def execute(self, *args, **kwargs):
            pass
    class Engine:
        @contextmanager
        def begin(self):
            try:
                yield Connection()
            except BaseException:
                state.append("rollback")
                raise
        def dispose(self):
            state.append("dispose")
    monkeypatch.setattr(migrations, "create_migration_engine", lambda *a, **k: Engine())
    def fail(*args):
        raise RuntimeError("secret-sentinel")
    monkeypatch.setattr(migrations.command, "upgrade", fail)
    with pytest.raises(migrations.MigrationError) as error:
        migrations.migrate("secret-sentinel")
    assert state == ["rollback", "dispose"]
    assert "secret-sentinel" not in str(error.value)
    assert error.value.__suppress_context__
