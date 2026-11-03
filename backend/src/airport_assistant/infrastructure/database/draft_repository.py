"""Draft storage on the shared bounded customer unit of work."""

import json
from psycopg.types.json import Jsonb

from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations,ConversationSQL
from airport_assistant.modules.drafts.models import Draft,Destination,ReportPayload,DraftError
from airport_assistant.modules.identity.auth import AuthError


FIELDS='id,tenant_id,conversation_id,actor_principal_id,actor_session_id,created_session_id,action_type,payload,destination,destination_queue_id,displayed_summary,language,version,payload_hash,created_at,expires_at,state,case_id,resource_security_labels'


def draft_row(row):
    if row is None:return None
    try:
        values=list(row)
        values[7]=ReportPayload.model_validate(row[7])
        values[8]=Destination.model_validate_json(json.dumps(row[8]))
        if values.pop(9)!=values[8].queue_id:raise ValueError('Mismatched destination.')
        values[-1]=tuple(row[-1])
        return Draft(*values)
    except (ValueError,AuthError,TypeError):
        raise DraftError(500,'INTERNAL_ERROR') from None


class PostgresDrafts(PostgresConversations):
    def unit(self,connection,context):return DraftSQL(connection,self._schema,context)


class DraftSQL(ConversationSQL):
    async def now(self):
        return (await(await self._c.execute('SELECT clock_timestamp()')).fetchone())[0]

    async def destination(self,service_type):
        row=await(await self._c.execute(self.query("""SELECT q.id,q.organization_id,q.name,o.name,q.service_type
            FROM {s}.service_queues q JOIN {s}.organizations o ON(o.tenant_id,o.id)=(q.tenant_id,q.organization_id)
            WHERE q.tenant_id=%s AND q.active AND o.active AND q.service_type=%s ORDER BY q.id LIMIT 1"""),
            (self._context.tenant_id,service_type))).fetchone()
        return Destination(queue_id=row[0],organization_id=row[1],queue_name=row[2],organization_name=row[3],service_type=row[4]) if row else None

    async def locate_draft(self,id):
        row=await(await self._c.execute(self.query('SELECT conversation_id FROM {s}.action_drafts WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        return row[0] if row else None

    async def find_draft(self,id):
        row=await(await self._c.execute(self.query('SELECT '+FIELDS+' FROM {s}.action_drafts WHERE tenant_id=%s AND id=%s FOR UPDATE'),(self._context.tenant_id,id))).fetchone()
        return draft_row(row)

    async def create_draft(self,draft):
        columns='id,tenant_id,conversation_id,actor_principal_id,actor_session_id,created_session_id,action_type,payload,destination,destination_queue_id,displayed_summary,language,version,payload_hash,created_at,expires_at'
        values=(draft.id,draft.tenant_id,draft.conversation_id,draft.actor_principal_id,draft.actor_session_id,draft.created_session_id,draft.action_type,
                Jsonb(draft.payload.model_dump(mode='json')),Jsonb(draft.destination.model_dump(mode='json')),draft.destination.queue_id,
                draft.displayed_summary,draft.language,draft.version,draft.payload_hash,draft.created_at,draft.expires_at)
        row=await(await self._c.execute(self.query('INSERT INTO {s}.action_drafts('+columns+') VALUES('+','.join(['%s']*len(values))+') RETURNING '+FIELDS),values)).fetchone()
        return draft_row(row)

    async def edit_draft(self,draft):
        row=await(await self._c.execute(self.query("UPDATE {s}.action_drafts SET payload=%s,destination=%s,destination_queue_id=%s,displayed_summary=%s,language=%s,version=%s,payload_hash=%s WHERE tenant_id=%s AND id=%s AND state='pending' AND expires_at>clock_timestamp() RETURNING "+FIELDS),
            (Jsonb(draft.payload.model_dump(mode='json')),Jsonb(draft.destination.model_dump(mode='json')),draft.destination.queue_id,
             draft.displayed_summary,draft.language,draft.version,draft.payload_hash,draft.tenant_id,draft.id))).fetchone()
        return draft_row(row)

    async def terminal(self,id,state):
        row=await(await self._c.execute(self.query("UPDATE {s}.action_drafts SET state=CASE WHEN expires_at<=clock_timestamp() THEN 'expired' ELSE %s END WHERE tenant_id=%s AND id=%s AND state='pending' RETURNING "+FIELDS),
            (state,self._context.tenant_id,id))).fetchone()
        return draft_row(row)
