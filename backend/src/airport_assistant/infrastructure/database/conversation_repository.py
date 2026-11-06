"""Parameterised conversation storage; domain decisions stay in the service."""

from contextlib import asynccontextmanager

from psycopg import sql

from airport_assistant.infrastructure.database.migrations import validate_schema
from airport_assistant.modules.conversations.service import Conversation, Message, CustomerReadProjection, ConversationSessionLost
from airport_assistant.infrastructure.database.connections import DatabaseError


FIELDS = 'id,tenant_id,customer_principal_id,anonymous_session_id,language,version,generation_epoch,last_sequence,case_id,resource_security_labels,mode'


def conversation(row):
    return Conversation(*row[:9],tuple(row[9]),row[10],row[8] is not None) if row else None


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

    async def source_case_context(self,id):
        fields='id,version,resource_security_labels'
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.cases WHERE tenant_id=%s AND id=%s'),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def duplicate(self,conversation_id,client_message_id):
        ctx=self._context
        row=await (await self._c.execute(self.query('SELECT id,sequence,body,language FROM {s}.messages WHERE tenant_id=%s AND conversation_id=%s AND client_message_id=%s AND author_principal_id IS NOT DISTINCT FROM %s AND author_session_id IS NOT DISTINCT FROM %s'),
            (ctx.tenant_id,conversation_id,client_message_id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None))).fetchone()
        return Message(*row) if row else None

    async def customer_message(self,conversation_id,message_id):
        ctx=self._context
        row=await(await self._c.execute(self.query('''SELECT id,sequence,body,language FROM {s}.messages
            WHERE tenant_id=%s AND conversation_id=%s AND id=%s AND author_type='customer'
              AND author_principal_id IS NOT DISTINCT FROM %s AND author_session_id IS NOT DISTINCT FROM %s
              '''),
            (ctx.tenant_id,conversation_id,message_id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None))).fetchone()
        return Message(*row) if row else None

    async def customer_history(self,conversation_id,limit):
        ctx=self._context
        rows=await(await self._c.execute(self.query("""SELECT id,sequence,body,language FROM {s}.messages
            WHERE tenant_id=%s AND conversation_id=%s AND author_type='customer'
              AND author_principal_id IS NOT DISTINCT FROM %s AND author_session_id IS NOT DISTINCT FROM %s
              ORDER BY sequence DESC LIMIT %s"""),
            (ctx.tenant_id,conversation_id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,limit))).fetchall()
        return tuple(Message(*r) for r in reversed(rows))

    async def customer_read_projection(self,id,kind,*,message_id=None,limit=8):
        """One scoped statement snapshot, never a cross-operation grant cache."""
        from dataclasses import replace
        from uuid import UUID
        if kind not in {'meta','message','history'} or type(limit) is not int or not 1<=limit<=8:
            raise ValueError('Bounded customer read required.')
        ctx=self._context
        fields=','.join('c.'+name for name in FIELDS.split(','))
        row=await(await self._c.execute(self.query('SELECT '+fields+''',p.id,p.version,p.resource_security_labels,
            coalesce(data.items,'[]'::jsonb),{s}.conversation_session_valid()
            FROM {s}.conversations c
            LEFT JOIN {s}.cases p ON p.tenant_id=c.tenant_id AND p.id=c.case_id
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(jsonb_build_array(m.id,m.sequence,m.body,m.language) ORDER BY m.sequence) items
              FROM (SELECT id,sequence,body,language FROM {s}.messages
                WHERE tenant_id=c.tenant_id AND conversation_id=c.id AND author_type='customer'
                  AND author_principal_id IS NOT DISTINCT FROM %s AND author_session_id IS NOT DISTINCT FROM %s
                  AND %s IN ('message','history') AND (%s='history' OR id=%s)
                ORDER BY sequence DESC LIMIT %s) m
            ) data ON true
            WHERE c.tenant_id=%s AND c.id=%s'''),
            (ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,kind,kind,message_id,
             limit if kind=='history' else 1,ctx.tenant_id,id))).fetchone()
        if row is None:return None
        parent=dict(id=row[11],version=row[12],resource_security_labels=row[13]) if row[11] is not None else None
        head=replace(conversation(row[:11]),case_access_verified=parent is not None)
        messages=tuple(Message(UUID(item[0]),item[1],item[2],item[3]) for item in row[14])
        data=messages[0] if kind=='message' and len(messages)==1 else messages if kind=='history' else None
        return CustomerReadProjection(head,data,parent,row[15])

    async def append(self,conv,id,client_message_id,body):
        ctx=self._context
        allocated=await (await self._c.execute(self.query('UPDATE {s}.conversations SET last_sequence=last_sequence+1,version=version+1 WHERE tenant_id=%s AND id=%s RETURNING last_sequence'),
            (ctx.tenant_id,conv.id))).fetchone()
        if allocated is None:return None
        sequence=allocated[0]
        from psycopg.types.json import Jsonb
        # Existing report-only schemas grant INSERT on a fixed public column
        # list. Private classification has its own later migration/privilege.
        label_column=',resource_security_labels' if conv.resource_security_labels else ''
        label_value=',%s' if conv.resource_security_labels else ''
        parameters=(ctx.tenant_id,id,conv.id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,
            'customer',body,conv.language,client_message_id,sequence)
        if conv.resource_security_labels:parameters+=(Jsonb(list(conv.resource_security_labels)),)
        row=await (await self._c.execute(self.query('INSERT INTO {s}.messages(tenant_id,id,conversation_id,author_principal_id,author_session_id,author_type,body,language,client_message_id,sequence'+label_column+') VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s'+label_value+') RETURNING id,sequence,body,language'),parameters)).fetchone()
        return Message(*row)

    async def change_language(self,conv,language):
        row=await (await self._c.execute(self.query('UPDATE {s}.conversations SET language=%s,version=version+1,generation_epoch=generation_epoch+1 WHERE tenant_id=%s AND id=%s RETURNING '+FIELDS),
            (language,self._context.tenant_id,conv.id))).fetchone()
        return conversation(row)


    async def lock_conversation_case(self,id):
        row=await(await self._c.execute(self.query('SELECT case_id FROM {s}.conversations WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        if not row or row[0] is None:return None
        locked=await(await self._c.execute(self.query('SELECT id FROM {s}.cases WHERE tenant_id=%s AND id=%s FOR UPDATE'),(self._context.tenant_id,row[0]))).fetchone()
        if locked is None:
            from airport_assistant.modules.identity.auth import AuthError
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        return locked[0]
