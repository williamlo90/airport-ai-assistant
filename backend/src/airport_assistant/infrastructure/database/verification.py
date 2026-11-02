"""Opt-in DB-01 probe for a dedicated test database, never application startup."""

from uuid import uuid4

from psycopg import sql

from airport_assistant.infrastructure.database.connections import DatabaseError, RuntimeDatabase, migration_connection


class ProbeRollback(Exception):
    pass


async def verify_connections(runtime: RuntimeDatabase, direct_url: str, *, connect_timeout=10) -> None:
    table = sql.Identifier("public", "_airport_db01_probe_" + uuid4().hex)
    await runtime.open()
    try:
        async with runtime.transaction() as connection:
            row = await (await connection.execute("SELECT current_user, current_database()" )).fetchone()
            runtime_role, runtime_database = row
            if not connection.pgconn.ssl_in_use:
                raise DatabaseError()
        async with migration_connection(direct_url, connect_timeout=connect_timeout) as direct:
            row = await (await direct.execute("SELECT current_database()" )).fetchone()
            if row[0] != runtime_database or not direct.pgconn.ssl_in_use:
                raise DatabaseError()
            await direct.execute(sql.SQL("CREATE TABLE {} (token uuid PRIMARY KEY)").format(table))
            try:
                await direct.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(table, sql.Identifier(runtime_role)))
                try:
                    async with runtime.transaction() as connection:
                        await connection.execute(sql.SQL("INSERT INTO {} VALUES (%s)").format(table), (uuid4(),))
                        raise ProbeRollback()
                except ProbeRollback:
                    pass
                row = await (await direct.execute(sql.SQL("SELECT count(*) FROM {}").format(table))).fetchone()
                if row[0] != 0:
                    raise DatabaseError()
                async with runtime.transaction() as connection:
                    await connection.execute(sql.SQL("INSERT INTO {} VALUES (%s)").format(table), (uuid4(),))
                row = await (await direct.execute(sql.SQL("SELECT count(*) FROM {}").format(table))).fetchone()
                if row[0] != 1:
                    raise DatabaseError()
                # A failed SQL transaction must not poison the next pool borrower.
                try:
                    async with runtime.transaction() as connection:
                        await connection.execute("SELECT 1 / 0")
                except DatabaseError as exc:
                    if exc.sqlstate != "22012":
                        raise
                else:
                    raise DatabaseError()
                async with runtime.transaction() as connection:
                    row = await (await connection.execute("SELECT 1")).fetchone()
                    if row != (1,):
                        raise DatabaseError()
            finally:
                await direct.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))
    finally:
        await runtime.close()
