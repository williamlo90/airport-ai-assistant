"""DB-01 offline checks: no network access or credentials required."""

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import psycopg
import pytest

from airport_assistant.composition import ApplicationResources, build_resources, database_loop_factory
from airport_assistant.config import ConfigurationError, Settings
from airport_assistant.database_config import (
    RuntimeDatabaseSettings, MigrationDatabaseSettings, load_database_settings, validate_same_target,
)
from airport_assistant.infrastructure.database.connections import (
    DatabaseError, RuntimeDatabase, connection_options, protect_driver_logs,
)
from airport_assistant.main import create_app

POOLED = "postgresql://runtime:secret-sentinel@ep-test-pooler.region.aws.neon.tech/testdb?sslmode=require"
DIRECT = "postgresql://migration:secret-sentinel@ep-test.region.aws.neon.tech/testdb?sslmode=require"


@pytest.fixture(autouse=True)
def isolated_database_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("APP_DB_") or name in {"DATABASE_URL", "DATABASE_DIRECT_URL", "APP_DATABASE_ENABLED"}:
            monkeypatch.delenv(name, raising=False)


def config(tmp_path, text):
    file = tmp_path / ".env"
    file.write_text(text, encoding="utf-8")
    return file


def test_credentials_are_redacted_and_runtime_does_not_need_direct_url(tmp_path):
    settings = load_database_settings(RuntimeDatabaseSettings, config(tmp_path, f"DATABASE_URL={POOLED}\n"))
    assert "secret-sentinel" not in repr(settings)
    assert "secret-sentinel" not in settings.model_dump_json()
    assert not hasattr(settings, "direct_url")
    assert settings.pool_max == 2


@pytest.mark.parametrize("model,key", [(RuntimeDatabaseSettings, "DATABASE_URL"), (MigrationDatabaseSettings, "DATABASE_DIRECT_URL")])
def test_missing_database_config_is_safe(tmp_path, model, key):
    with pytest.raises(ConfigurationError, match=key):
        load_database_settings(model, config(tmp_path, ""))


@pytest.mark.parametrize("url", [
    "secret-sentinel", POOLED.replace("postgresql:", "mysql:"),
    POOLED.replace("sslmode=require", "sslmode=disable"),
    POOLED + "&host=secret-sentinel", POOLED + "&sslmode=require",
    POOLED.replace("-pooler", ""), POOLED.replace(":secret-sentinel", ""),
])
def test_bad_runtime_url_never_exposes_values(tmp_path, url):
    with pytest.raises(ConfigurationError) as error:
        load_database_settings(RuntimeDatabaseSettings, config(tmp_path, f"DATABASE_URL={url}\n"))
    assert "DATABASE_URL" in str(error.value)
    assert "secret-sentinel" not in str(error.value)


def test_direct_cannot_be_pooled_and_targets_must_match(tmp_path):
    with pytest.raises(ConfigurationError, match="DATABASE_DIRECT_URL"):
        load_database_settings(MigrationDatabaseSettings, config(tmp_path, f"DATABASE_DIRECT_URL={POOLED}\n"))
    runtime = RuntimeDatabaseSettings(DATABASE_URL=POOLED)
    direct = MigrationDatabaseSettings(DATABASE_DIRECT_URL=DIRECT)
    validate_same_target(runtime, direct)
    other = MigrationDatabaseSettings(DATABASE_DIRECT_URL=DIRECT.replace("/testdb", "/otherdb"))
    with pytest.raises(ConfigurationError, match="same endpoint"):
        validate_same_target(runtime, other)


def test_pool_bounds_and_environment_override(tmp_path, monkeypatch):
    file = config(tmp_path, f"DATABASE_URL={POOLED}\nAPP_DB_POOL_MAX=3\n")
    monkeypatch.setenv("APP_DB_POOL_MAX", "4")
    assert load_database_settings(RuntimeDatabaseSettings, file).pool_max == 4
    monkeypatch.setenv("APP_DB_POOL_MAX", "secret-sentinel")
    with pytest.raises(ConfigurationError, match="APP_DB_POOL_MAX") as error:
        load_database_settings(RuntimeDatabaseSettings, file)
    assert "secret-sentinel" not in str(error.value)


def test_secure_connection_options():
    options = connection_options(5)
    assert options["sslmode"] == "verify-full"
    assert options["channel_binding"] == "require"
    assert Path(options["sslrootcert"]).is_file()
    assert options["autocommit"] is True
    assert options["prepare_threshold"] is None
    assert options["connect_timeout"] == 5


def test_pool_is_bounded_and_network_lazy():
    # Construction happens before an event loop and must not open sockets.
    db = RuntimeDatabase(POOLED, pool_max=3, max_waiting=7)
    assert db._pool.min_size == 0
    assert db._pool.max_size == 3
    assert db._pool.max_waiting == 7
    assert db._pool.closed
    assert "secret-sentinel" not in repr(db)


class FakeConnection:
    def __init__(self):
        self.executed = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    async def close(self):
        self.closed = True

    async def execute(self, query, params=None):
        self.executed.append((query, params))

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        except BaseException:
            self.rolled_back += 1
            raise
        else:
            self.committed += 1


class FakePool:
    def __init__(self, connection):
        self.conn = connection
        self.closed = False

    async def open(self):
        pass

    async def close(self):
        self.closed = True

    async def getconn(self):
        return self.conn

    async def putconn(self, connection):
        assert connection is self.conn


def test_transaction_commit_rollback_cancel_and_reuse():
    db = RuntimeDatabase(POOLED)
    conn = FakeConnection()
    db._pool = FakePool(conn)
    async def run():
        async with db.transaction():
            pass
        with pytest.raises(ValueError):
            async with db.transaction():
                raise ValueError("business failure")
        with pytest.raises(asyncio.CancelledError):
            async with db.transaction():
                raise asyncio.CancelledError()
        async with db.transaction():
            pass
    asyncio.run(run())
    assert (conn.committed, conn.rolled_back) == (2, 2)
    assert all("true" in query for query, _ in conn.executed)


def test_driver_failure_redacted_and_pool_closed_on_startup_failure():
    db = RuntimeDatabase(POOLED)
    conn = FakeConnection()
    conn.execute = AsyncMock(side_effect=psycopg.OperationalError("secret-sentinel"))
    db._pool = FakePool(conn)
    with pytest.raises(DatabaseError) as error:
        asyncio.run(db.open())
    assert db._pool.closed
    assert "secret-sentinel" not in str(error.value)
    assert error.value.__suppress_context__


def test_lifecycle_closes_on_app_exception():
    database = AsyncMock()
    resources = ApplicationResources(database=database)
    async def run():
        with pytest.raises(ValueError):
            async with resources.lifecycle():
                raise ValueError("stop")
    asyncio.run(run())
    database.open.assert_awaited_once()
    database.close.assert_awaited_once()


def test_api_database_configuration_is_explicit():
    assert build_resources(None).database is None
    with pytest.raises(ConfigurationError):
        create_app(Settings(env="test", database_enabled=True))
    app = create_app(Settings(env="test", database_enabled=True), database_settings=RuntimeDatabaseSettings(DATABASE_URL=POOLED))
    assert app.state.resources.database._pool.closed


def test_fastapi_lifespan_opens_and_closes_configured_runtime(monkeypatch):
    database = AsyncMock()
    monkeypatch.setattr("airport_assistant.composition.RuntimeDatabase", lambda *args, **kwargs: database)
    app = create_app(Settings(env="test", database_enabled=True), database_settings=RuntimeDatabaseSettings(DATABASE_URL=POOLED))
    database.open.assert_not_awaited()
    async def run():
        async with app.router.lifespan_context(app):
            database.open.assert_awaited_once()
            database.close.assert_not_awaited()
    asyncio.run(run())
    database.close.assert_awaited_once()


def test_failed_startup_still_closes_resources(monkeypatch):
    database = AsyncMock()
    database.open.side_effect = DatabaseError()
    monkeypatch.setattr("airport_assistant.composition.RuntimeDatabase", lambda *args, **kwargs: database)
    app = create_app(Settings(env="test", database_enabled=True), database_settings=RuntimeDatabaseSettings(DATABASE_URL=POOLED))
    async def run():
        with pytest.raises(DatabaseError):
            async with app.router.lifespan_context(app):
                pytest.fail("failed DB startup must not enter app lifespan")
    asyncio.run(run())
    database.close.assert_awaited_once()


def test_driver_logs_are_redacted_even_with_exception(caplog):
    protect_driver_logs()
    protect_driver_logs()
    logger = logging.getLogger("psycopg.pool")
    with caplog.at_level(logging.WARNING):
        try:
            raise RuntimeError("secret-sentinel")
        except RuntimeError:
            logger.exception("failed connection %s", POOLED)
    assert "database_driver_event" in caplog.text
    assert "secret-sentinel" not in caplog.text


def test_database_cli_does_not_connect_without_live_flag(tmp_path):
    file = config(tmp_path, f"DATABASE_URL={POOLED}\nDATABASE_DIRECT_URL={DIRECT}\n")
    result = subprocess.run([sys.executable, "-m", "airport_assistant.database_check", "--env-file", str(file)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert "No network" in result.stdout
    assert "secret-sentinel" not in result.stdout + result.stderr


def test_missing_credentials_cli_is_safe(tmp_path):
    file = config(tmp_path, "")
    result = subprocess.run([sys.executable, "-m", "airport_assistant.database_check", "--env-file", str(file)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "Traceback" not in result.stderr


def test_selector_loop_factory():
    loop = database_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_checkout_deadline_includes_connection_check_and_reclaims_connection(monkeypatch):
    # Exercise the installed pool getconn/check/cancellation path, not a fake timeout.
    db = RuntimeDatabase(POOLED, acquire_timeout=0.03)
    connection = AsyncMock()
    connection.autocommit = True
    async def stalled_check(*args, **kwargs):
        await asyncio.Event().wait()
    connection.execute.side_effect = stalled_check
    monkeypatch.setattr(db._pool, "_check_open_getconn", lambda: None)
    monkeypatch.setattr(db._pool, "_getconn_unchecked", AsyncMock(return_value=connection))
    reclaim = AsyncMock()
    monkeypatch.setattr(db._pool, "_putconn", reclaim)
    async def run():
        with pytest.raises(DatabaseError):
            async with db.transaction():
                pytest.fail("stalled checkout must not yield")
    asyncio.run(run())
    connection.close.assert_awaited_once()
    reclaim.assert_awaited_once_with(connection, from_getconn=True)


def test_cancel_during_check_discards_and_reclaims_connection(monkeypatch):
    db = RuntimeDatabase(POOLED)
    connection = AsyncMock()
    connection.autocommit = True
    entered = asyncio.Event()
    async def stalled_check(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    connection.execute.side_effect = stalled_check
    monkeypatch.setattr(db._pool, "_check_open_getconn", lambda: None)
    monkeypatch.setattr(db._pool, "_getconn_unchecked", AsyncMock(return_value=connection))
    reclaim = AsyncMock()
    monkeypatch.setattr(db._pool, "_putconn", reclaim)
    async def borrow():
        async with db.transaction():
            pytest.fail("cancelled checkout must not yield")
    async def run():
        task = asyncio.create_task(borrow())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    connection.close.assert_awaited_once()
    reclaim.assert_awaited_once_with(connection, from_getconn=True)


def test_startup_probe_has_client_deadline_and_closes_pool():
    db = RuntimeDatabase(POOLED, acquire_timeout=0.03, statement_timeout_ms=10)
    connection = FakeConnection()
    pool = FakePool(connection)
    db._pool = pool
    async def execute(query, params=None):
        if query == "SELECT 1":
            await asyncio.Event().wait()
    connection.execute = execute
    with pytest.raises(DatabaseError):
        asyncio.run(db.open())
    assert pool.closed
    assert connection.rolled_back == 1
    assert connection.closed
