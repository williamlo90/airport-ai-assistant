"""Inbox persistence capabilities; signature verification is a business gate."""
from psycopg import sql
from psycopg.types.json import Jsonb
from airport_assistant.infrastructure.database.mail import PostgresMail


class PostgresInbox(PostgresMail):
    async def accept(self,alias,account_ref,event_id,event):
        query=sql.SQL('SELECT {}.email_webhook_accept(%s,%s,%s,%s)').format(sql.Identifier(self._schema))
        async with self._database.transaction() as c:
            return (await(await c.execute(query,(alias,account_ref,event_id,Jsonb(event)))).fetchone())[0]

    async def snapshot(self,lease):return await self._call('email_inbox_snapshot',lease)
    async def bind(self,lease):return await self._call('email_inbox_bind',lease)
    async def complete(self,lease,content,reason):
        return await self._call('email_inbox_complete',lease,Jsonb(content) if content is not None else None,reason)
