"""Alembic uses only the connection supplied by the safe migration runner."""

from alembic import context

connection = context.config.attributes.get("connection")
schema = context.config.attributes.get("schema")
if connection is None or schema is None:
    raise RuntimeError("Use airport-assistant-migrate with a supplied migration connection.")

context.configure(connection=connection, target_metadata=None,
                  version_table="schema_migrations", version_table_schema=schema)
with context.begin_transaction():
    context.run_migrations()
