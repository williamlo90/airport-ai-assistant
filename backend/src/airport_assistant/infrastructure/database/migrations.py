"""Transactional Alembic runner using only the direct migration credential."""

from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from airport_assistant.infrastructure.database.connections import connection_options, protect_driver_logs


class MigrationError(RuntimeError):
    pass


def migration_directory() -> Path:
    packaged = Path(__file__).resolve().parents[2] / "_migrations"
    directory = packaged if packaged.is_dir() else Path(__file__).resolve().parents[4] / "migrations"
    if not (directory / "env.py").is_file():
        raise MigrationError("Migration resources are missing.")
    return directory


def validate_schema(schema: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) or schema == "public" or schema.startswith("pg_"):
        raise MigrationError("Invalid application schema.")


def create_migration_engine(url: str, *, connect_timeout=10):
    protect_driver_logs()
    options = connection_options(connect_timeout)
    options["autocommit"] = False
    # No credential in Engine URL/repr/configuration or alembic.ini.
    return create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(url, **options),
                         poolclass=NullPool, echo=False, hide_parameters=True)


def migrate(url: str, *, schema="app", connect_timeout=10, action="upgrade",
            target_revision="head", runtime_role="airport_assistant_runtime",
            policy_role="airport_assistant_policy_owner") -> str | None:
    validate_schema(schema)
    if action not in {"upgrade", "current"}:
        raise MigrationError("Unsupported migration action.")
    engine = create_migration_engine(url, connect_timeout=connect_timeout)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
            if action == "upgrade":
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                   {"key": "airport-migrations:" + schema})
                quoted = connection.dialect.identifier_preparer.quote_identifier(schema)
                connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {quoted}")
                connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA {quoted} FROM PUBLIC")
                config = Config()
                config.set_main_option("script_location", str(migration_directory()))
                config.attributes.update(connection=connection, schema=schema,
                                         runtime_role=runtime_role, policy_role=policy_role)
                command.upgrade(config, target_revision)
            return MigrationContext.configure(connection, opts={
                "version_table": "schema_migrations", "version_table_schema": schema,
            }).get_current_revision()
    except Exception:
        raise MigrationError("Migration failed; transaction rolled back. Check schema state and database access.") from None
    finally:
        engine.dispose()
