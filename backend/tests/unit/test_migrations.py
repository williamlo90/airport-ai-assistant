"""Offline checks for migration packaging, validation and failure handling."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import sys

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
    assert ScriptDirectory.from_config(config).get_heads() == ["0009_case_lifecycle"]


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


def test_new_revision_is_offline_and_aborts_until_implemented(tmp_path,monkeypatch):
    root=tmp_path/"backend"
    directory=root/"migrations"
    shutil.copytree(migrations.migration_directory(),directory)
    monkeypatch.setattr(migrations,"migration_directory",lambda:directory)
    monkeypatch.setattr(migrations,"__file__",str(root/"src/airport_assistant/infrastructure/database/migrations.py"))
    monkeypatch.setattr(migrations,"create_migration_engine",lambda *a,**k:pytest.fail("new must not connect"))
    created=migrations.new_revision('feature "safe quote"')
    assert created.is_file() and created.parent==directory/"versions"
    config=Config();config.set_main_option("script_location",str(directory))
    graph=ScriptDirectory.from_config(config)
    assert len(graph.get_heads())==1
    script=graph.get_revision(graph.get_heads()[0])
    assert script.down_revision=="0009_case_lifecycle"
    with pytest.raises(NotImplementedError):script.module.upgrade()


def test_new_cli_does_not_load_credentials(monkeypatch,tmp_path,capsys):
    from airport_assistant import migrate as cli
    monkeypatch.setattr(sys,"argv",["migrate","new","--message","offline"])
    monkeypatch.setattr(cli,"load_database_settings",lambda *a,**k:pytest.fail("new must not load credentials"))
    monkeypatch.setattr(cli,"new_revision",lambda message:tmp_path/"new_revision.py")
    cli.main()
    assert "created revision" in capsys.readouterr().out


def test_multiple_heads_rejected_before_connection(tmp_path,monkeypatch):
    directory=tmp_path/"migrations"
    shutil.copytree(migrations.migration_directory(),directory)
    (directory/"versions/fork.py").write_text("revision='fork'\ndown_revision='0001_foundation'\n",encoding="utf-8")
    monkeypatch.setattr(migrations,"migration_directory",lambda:directory)
    monkeypatch.setattr(migrations,"create_migration_engine",lambda *a,**k:pytest.fail("ambiguous head must not connect"))
    with pytest.raises(migrations.MigrationError,match="single"):
        migrations.migrate("secret-sentinel")
