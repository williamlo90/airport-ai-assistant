"""Bounded async runtime pool and separate direct migration connections."""

from contextlib import asynccontextmanager
import asyncio
import logging

import certifi
import psycopg
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout, TooManyRequests

from airport_assistant.modules.identity.context import DatabaseContext


class DatabaseError(RuntimeError):
    def __init__(self, sqlstate: str | None = None):
        super().__init__("Database operation failed.")
        self.sqlstate = sqlstate


class RedactDriverLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "database_driver_event (details redacted)"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def protect_driver_logs() -> None:
    for name in ("psycopg", "psycopg.pool", "psycopg.transaction"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, RedactDriverLog) for item in logger.filters):
            logger.addFilter(RedactDriverLog())


def connection_options(connect_timeout: int) -> dict:
    return {
        "autocommit": True,
        "prepare_threshold": None,
        "connect_timeout": connect_timeout,
        "sslmode": "verify-full",
        "sslrootcert": certifi.where(),
        "channel_binding": "require",
    }


class RuntimeDatabase:
    def __init__(self, url: str, *, pool_max=2, max_waiting=8, connect_timeout=10,
                 acquire_timeout=10, statement_timeout_ms=10_000,schema='app'):
        from airport_assistant.infrastructure.database.migrations import validate_schema
        validate_schema(schema)
        self.schema=schema
        protect_driver_logs()
        self.webhook_required = False
        self._private_history_schema = None
        self._statement_timeout_ms = statement_timeout_ms
        self._acquire_timeout = acquire_timeout
        self._pool = AsyncConnectionPool(
            url, open=False, min_size=0, max_size=pool_max,
            max_waiting=max_waiting, timeout=acquire_timeout,
            reconnect_timeout=connect_timeout, name="airport-runtime",
            kwargs=connection_options(connect_timeout),
            check=self._check_connection,
        )

    async def _check_connection(self, connection) -> None:
        try:
            async with asyncio.timeout(self._acquire_timeout):
                await AsyncConnectionPool.check_connection(connection)
        except BaseException:
            # Do not return an uncertain/in-flight check connection as healthy.
            await connection.close()
            raise

    def require_private_mail_history(self,schema='app'):
        from airport_assistant.infrastructure.database.migrations import validate_schema
        validate_schema(schema)
        if schema!=self.schema:raise ValueError('Private history and runtime schemas must match.')
        self._private_history_schema=schema

    async def open(self) -> None:
        try:
            await self._pool.open()
            async with asyncio.timeout(self._acquire_timeout + self._statement_timeout_ms / 1000):
                async with self.transaction() as connection:
                    await connection.execute("SELECT 1")
                    await self._validate_role(connection)
        except TimeoutError:
            await self._pool.close()
            raise DatabaseError() from None
        except BaseException:
            await self._pool.close()
            raise

    async def ready(self) -> bool:
        """Bounded core connection check; never read tenant data or providers."""
        try:
            async with asyncio.timeout(2):
                async with self.transaction() as connection:
                    return await (await connection.execute('SELECT 1')).fetchone() == (1,)
        except Exception:
            return False

    async def close(self) -> None:
        await self._pool.close()

    async def _validate_role(self, connection) -> None:
        row = await (await connection.execute("""
            SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb
                OR r.rolreplication OR r.rolinherit OR current_user <> session_user
                OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid)
                OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid)
                OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner=r.oid AND nspname IN ('app',%s))
                OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           WHERE n.nspname IN ('app',%s) AND c.relowner=r.oid)
                OR has_database_privilege(current_user,current_database(),'CREATE')
                OR has_database_privilege(current_user,current_database(),'TEMP')
                OR has_schema_privilege(current_user,'public','CREATE')
            FROM pg_roles r WHERE rolname=current_user
        """,(self.schema,self.schema))).fetchone()
        if row is None or row[0]:
            raise DatabaseError()
        row = await (await connection.execute("SELECT has_schema_privilege(current_user,%s,'CREATE') OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='app' AND has_schema_privilege(current_user,oid,'CREATE'))",(self.schema,))).fetchone()
        if row[0]:
            raise DatabaseError()
        if getattr(self,'auth_mail_required',False):
            signature=self.schema+'.auth_mail_route_allowed(uuid,uuid,text,text,text)'
            row=await(await connection.execute("SELECT CASE WHEN to_regprocedure(%s) IS NULL THEN false ELSE has_function_privilege(current_user,%s,'EXECUTE') END",(signature,signature))).fetchone()
            if row!=(True,):raise DatabaseError()
        if self.webhook_required:
            signature=self.schema+'.email_webhook_accept(text,text,text,jsonb)'
            row=await(await connection.execute("SELECT CASE WHEN to_regprocedure(%s) IS NULL THEN false ELSE has_function_privilege(current_user,%s,'EXECUTE') END",(signature,signature))).fetchone()
            if row!=(True,):raise DatabaseError()
        if self._private_history_schema is not None:
            signature=self._private_history_schema+'.private_mail_history_ready()'
            exists=await(await connection.execute('SELECT to_regprocedure(%s) IS NOT NULL',(signature,))).fetchone()
            if exists!=(True,):raise DatabaseError()
            row=await(await connection.execute(psycopg.sql.SQL('SELECT {}.private_mail_history_ready()').format(psycopg.sql.Identifier(self._private_history_schema)))).fetchone()
            if row!=(True,):raise DatabaseError()

    @asynccontextmanager
    async def transaction(self, context: DatabaseContext | None = None):
        if context is not None and not isinstance(context, DatabaseContext):
            raise ValueError("Validated database context is required.")
        try:
            # pool.timeout does not cover network I/O inside its check callback.
            # The application budget covers queueing and that callback together.
            async with asyncio.timeout(self._acquire_timeout):
                connection = await self._pool.getconn()
            try:
                async with connection.transaction():
                    try:
                        # One atomic round trip for the same transaction-local
                        # safety/context settings; reset every field on checkout.
                        await connection.execute("""SELECT
                            set_config('statement_timeout',%s,true),set_config('lock_timeout','3000',true),
                            set_config('idle_in_transaction_session_timeout','10000',true),set_config('TimeZone','UTC',true),
                            set_config('app.tenant_id',%s,true),set_config('app.principal_id',%s,true),
                            set_config('app.actor_kind',%s,true),set_config('app.session_id',%s,true)""",
                            (str(self._statement_timeout_ms),str(context.tenant_id) if context else "",
                             str(context.principal_id) if context and context.principal_id else "",
                             context.actor_kind if context else "anonymous",str(context.session_id) if context and context.session_id else ""))
                        yield connection
                    except asyncio.CancelledError:
                        # Close locally before transaction exit: don't wait for a
                        # network rollback on a cancelled/stalled connection.
                        await connection.close()
                        raise
            finally:
                await self._pool.putconn(connection)
        except (psycopg.Error, PoolTimeout, PoolClosed, TooManyRequests, TimeoutError) as exc:
            raise DatabaseError(getattr(exc, "sqlstate", None)) from None


@asynccontextmanager
async def migration_connection(url: str, *, connect_timeout=10):
    protect_driver_logs()
    try:
        async with await psycopg.AsyncConnection.connect(url, **connection_options(connect_timeout)) as connection:
            await connection.execute("SET statement_timeout = '10s'")
            await connection.execute("SET lock_timeout = '3s'")
            yield connection
    except psycopg.Error as exc:
        raise DatabaseError(exc.sqlstate) from None
