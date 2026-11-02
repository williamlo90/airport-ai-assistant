"""Restricted runtime calls narrow auth functions; no direct identity writes."""

from datetime import datetime
from uuid import UUID

from psycopg import sql

from airport_assistant.modules.identity.auth import Session
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.infrastructure.database.migrations import validate_schema


def session_from_json(value)->Session|None:
    if value is None:return None
    return Session(UUID(value["id"]),UUID(value["tenant_id"]),UUID(value["principal_id"]) if value["principal_id"] else None,
                   value["kind"],datetime.fromisoformat(value["expires_at"]),value["csrf_hash"])


class PostgresAuthRepository:
    def __init__(self,database:RuntimeDatabase,schema="app"):
        validate_schema(schema);self._database=database;self._schema=schema

    async def _call(self,function,*args):
        statement=sql.SQL("SELECT {}({})").format(sql.Identifier(self._schema,function),sql.SQL(",").join(sql.Placeholder() for _ in args))
        async with self._database.transaction() as connection:
            row=await (await connection.execute(statement,args)).fetchone()
            return row[0]

    async def tenant_for_host(self,host):return await self._call("lookup_tenant_host",host)

    async def session(self,tenant,token_hash):return session_from_json(await self._call("auth_session",tenant,token_hash))

    async def create_anonymous(self,*args):return session_from_json(await self._call("auth_new_anonymous",*args))

    async def issue(self,*args):return await self._call("auth_issue",*args)

    async def cancel(self,*args):await self._call("auth_cancel",*args)

    async def verify(self,*args):
        result=await self._call("auth_verify",*args)
        return result["status"],session_from_json(result.get("session"))

    async def revoke(self,*args):return await self._call("auth_revoke",*args)
