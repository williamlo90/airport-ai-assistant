"""Parameterised conversation storage; domain decisions stay in the service."""

from contextlib import asynccontextmanager

from psycopg import sql

from airport_assistant.infrastructure.database.migrations import validate_schema
from airport_assistant.modules.conversations.service import Conversation, Message, ConversationSessionLost
from airport_assistant.infrastructure.database.connections import DatabaseError


FIELDS = 'id,tenant_id,customer_principal_id,anonymous_session_id,language,version,generation_epoch,last_sequence,case_id,resource_security_labels'


def conversation(row):
    return Conversation(*row[:-1],tuple(row[-1])) if row else None


class PostgresConversations:
    def __init__(self,database,schema='app'):
        validate_schema(schema)
        self._database,self._schema=database,schema

    @asynccontextmanager
    async def transaction(self,context):
        try:
            async with self._database.transaction(context) as connection:
                yield self.unit(connection,context)
        except DatabaseError as error:
            if error.sqlstate == '42501':
                # The failed statement has already rolled back. Confirm actual
                # session loss once; never classify every DB/privilege error as
                # unauthenticated, and never retry the write.
                async with self._database.transaction(context) as connection:
                    active = await ConversationSQL(connection,self._schema,context).session_active()
                if not active:
                    raise ConversationSessionLost() from None
            raise

    def unit(self,connection,context):
        return ConversationSQL(connection,self._schema,context)


class ConversationSQL:
    def __init__(self,connection,schema,context):
        self._c,self._schema,self._context=connection,schema,context

    def query(self,statement):
        return sql.SQL(statement).format(s=sql.Identifier(self._schema))

    async def session_active(self):
        return (await (await self._c.execute(self.query('SELECT {s}.conversation_session_valid()'))).fetchone())[0]

    async def languages(self):
        row=await (await self._c.execute(self.query('SELECT supported_languages FROM {s}.tenants WHERE id=%s AND active'),
                                       (self._context.tenant_id,))).fetchone()
        return row[0] if row else []

    async def create(self,id,language):
        ctx=self._context
        row=await (await self._c.execute(self.query('INSERT INTO {s}.conversations(tenant_id,id,customer_principal_id,anonymous_session_id,language) VALUES(%s,%s,%s,%s,%s) RETURNING '+FIELDS),
            (ctx.tenant_id,id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,language))).fetchone()
        return conversation(row)

    async def find(self,id,*,lock=False):
        row=await (await self._c.execute(self.query('SELECT '+FIELDS+' FROM {s}.conversations WHERE tenant_id=%s AND id=%s'+(' FOR UPDATE' if lock else '')),
            (self._context.tenant_id,id))).fetchone()
        return conversation(row)

    async def duplicate(self,conversation_id,client_message_id):
        ctx=self._context
        row=await (await self._c.execute(self.query('SELECT id,sequence,body,language FROM {s}.messages WHERE tenant_id=%s AND conversation_id=%s AND client_message_id=%s AND author_principal_id IS NOT DISTINCT FROM %s AND author_session_id IS NOT DISTINCT FROM %s'),
            (ctx.tenant_id,conversation_id,client_message_id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None))).fetchone()
        return Message(*row) if row else None

    async def append(self,conv,id,client_message_id,body):
        ctx=self._context
        allocated=await (await self._c.execute(self.query('UPDATE {s}.conversations SET last_sequence=last_sequence+1,version=version+1 WHERE tenant_id=%s AND id=%s RETURNING last_sequence'),
            (ctx.tenant_id,conv.id))).fetchone()
        if allocated is None:return None
        sequence=allocated[0]
        row=await (await self._c.execute(self.query('INSERT INTO {s}.messages(tenant_id,id,conversation_id,author_principal_id,author_session_id,author_type,body,language,client_message_id,sequence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,sequence,body,language'),
            (ctx.tenant_id,id,conv.id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,'customer',body,conv.language,client_message_id,sequence))).fetchone()
        return Message(*row)

    async def change_language(self,conv,language):
        row=await (await self._c.execute(self.query('UPDATE {s}.conversations SET language=%s,version=version+1,generation_epoch=generation_epoch+1 WHERE tenant_id=%s AND id=%s RETURNING '+FIELDS),
            (language,self._context.tenant_id,conv.id))).fetchone()
        return conversation(row)
