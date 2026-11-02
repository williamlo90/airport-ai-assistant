"""Read-only source identity mapping under runtime self-principal RLS."""

from psycopg import sql
from airport_assistant.infrastructure.database.migrations import validate_schema
from airport_assistant.modules.identity.context import DatabaseContext


class PostgresSourceSubjects:
    def __init__(self,database,schema='app'):
        validate_schema(schema);self._database=database;self._schema=schema

    async def find_subject(self,context:DatabaseContext):
        if context.actor_kind!='customer' or context.principal_id is None:return None
        query=sql.SQL('SELECT p.source_subject_ref FROM {} p JOIN {} t ON t.id=p.tenant_id WHERE p.tenant_id=%s AND p.id=%s AND p.active AND t.active').format(sql.Identifier(self._schema,'principals'),sql.Identifier(self._schema,'tenants'))
        async with self._database.transaction(context) as c:
            row=await (await c.execute(query,(context.tenant_id,context.principal_id))).fetchone()
            return row[0] if row else None
