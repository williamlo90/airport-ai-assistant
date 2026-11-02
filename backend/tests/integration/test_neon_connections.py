"""Explicitly opt-in: creates and removes a DB-01 probe in a test database."""

import asyncio
import os
from pathlib import Path

import pytest

from airport_assistant.composition import build_resources, database_loop_factory
from airport_assistant.database_config import (
    RuntimeDatabaseSettings, MigrationDatabaseSettings, load_database_settings, validate_same_target,
)
from airport_assistant.infrastructure.database.verification import verify_connections


@pytest.mark.skipif(os.environ.get("RUN_NEON_DB_TESTS") != "1", reason="Set RUN_NEON_DB_TESTS=1 for dedicated Neon test database.")
def test_pooled_direct_commit_rollback_and_pool_reuse():
    file = Path(os.environ.get("DB_TEST_ENV_FILE", ".env"))
    runtime = load_database_settings(RuntimeDatabaseSettings, file)
    migration = load_database_settings(MigrationDatabaseSettings, file)
    validate_same_target(runtime, migration)
    resources = build_resources(runtime)
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:
        runner.run(verify_connections(resources.database, migration.url.get_secret_value(),
                                      connect_timeout=migration.connect_timeout))
