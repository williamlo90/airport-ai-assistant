"""Draft storage on the shared bounded customer unit of work."""

import json
from uuid import UUID
from datetime import datetime
from psycopg import sql
from psycopg.types.json import Jsonb

from airport_assistant.infrastructure.database.conversation_repository import PostgresConversations,ConversationSQL
from airport_assistant.modules.drafts.models import Draft,Destination,ReportPayload,DraftError
from airport_assistant.modules.identity.auth import AuthError


FIELDS='id,tenant_id,conversation_id,actor_principal_id,actor_session_id,created_session_id,action_type,payload,destination,destination_queue_id,displayed_summary,language,version,payload_hash,created_at,expires_at,state,case_id,resource_security_labels'
RESULTS=",to_jsonb(action_drafts)->>'result_case_id',to_jsonb(action_drafts)->>'result_message_id',to_jsonb(action_drafts)->>'result_ref',to_jsonb(action_drafts)->>'committed_at'"
# to_jsonb keeps historical public schema fixtures readable before the private
# graph migration. Only a future guarded private INSERT needs the new column.
STORED=FIELDS+RESULTS+",coalesce(to_jsonb(action_drafts)->'source_observations','[]'::jsonb)"


def draft_row(row):
    if row is None:return None
    try:
        values=list(row[:19])
        values[7]=ReportPayload.model_validate(row[7])
        values[8]=Destination.model_validate_json(json.dumps(row[8]))
        if values.pop(9)!=values[8].queue_id:raise ValueError('Mismatched destination.')
        values[-1]=tuple(row[18])
        if len(row)>19:
            values.extend((UUID(row[19]) if row[19] else None,UUID(row[20]) if row[20] else None,row[21],datetime.fromisoformat(row[22]) if row[22] else None))
        evidence=None
        if len(row)>23:
            if type(row[23]) is not list:raise ValueError('Malformed source observations.')
            if row[23]:
                from airport_assistant.modules.source.evidence import SourceEvidence
                from airport_assistant.modules.source.labels import SecurityLabels
                evidence=SourceEvidence.from_records(row[1],row[23],
                    expected_labels=SecurityLabels.from_records(row[1],row[18]))
        return Draft(*values,case_access_verified=row[17] is not None,source_evidence=evidence)
    except (ValueError,AuthError,TypeError):
        raise DraftError(500,'INTERNAL_ERROR') from None


class PostgresDrafts(PostgresConversations):
    def unit(self,connection,context):return DraftSQL(connection,self._schema,context)


class DraftSQL(ConversationSQL):
    async def lock_draft_sources(self,id):
        row=await(await self._c.execute(self.query('''SELECT d.case_id,to_jsonb(d)->>'result_case_id',c.case_id,
            coalesce(has_any_column_privilege(current_user,to_regclass(%s),'UPDATE'),false),d.resource_security_labels,c.resource_security_labels
            FROM {s}.action_drafts d JOIN {s}.conversations c ON(c.tenant_id,c.id)=(d.tenant_id,d.conversation_id)
            WHERE d.tenant_id=%s AND d.id=%s'''),(sql.Identifier(self._schema,'cases').as_string(),self._context.tenant_id,id))).fetchone()
        if row is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
        lock=row[3] is True
        if not lock and (row[4]!=[] or row[5]!=[]):raise AuthError(404,'RESOURCE_NOT_FOUND')
        ids=sorted({UUID(str(value)) for value in row[:3] if value is not None})
        if not ids:return ()
        # Frozen schema0008 has no runtime UPDATE column privilege and only
        # immutable public cases; PostgreSQL cannot row-lock those receipts.
        # Do not add privileges or permit private writes via that compatibility
        # path. Current lifecycle schemas use actual ordered row locks.
        fields='id' if lock else 'id,resource_security_labels'
        found=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.cases WHERE tenant_id=%s AND id=ANY(%s) ORDER BY id'+(' FOR UPDATE' if lock else '')),
            (self._context.tenant_id,ids))).fetchall()
        if [item[0] for item in found]!=ids:raise AuthError(404,'RESOURCE_NOT_FOUND')
        if not lock and any(item[1]!=[] for item in found):raise AuthError(404,'RESOURCE_NOT_FOUND')
        return tuple(ids)

    async def selected_attachment_context(self,draft_id,ids):
        fields='id,content_hash,resource_security_labels'
        rows=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.attachments WHERE tenant_id=%s AND draft_id=%s AND id=ANY(%s) ORDER BY id'),
            (self._context.tenant_id,draft_id,list(ids)))).fetchall()
        return [dict(zip(fields.split(','),row)) for row in rows]

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
        row=await(await self._c.execute(self.query('SELECT '+STORED+' FROM {s}.action_drafts WHERE tenant_id=%s AND id=%s FOR UPDATE'),(self._context.tenant_id,id))).fetchone()
        return draft_row(row)

    async def case_result(self,id):
        from airport_assistant.modules.cases.models import CommitResult
        row=await(await self._c.execute(self.query('SELECT id,public_reference,initial_message_id FROM {s}.cases WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        return CommitResult(case_id=row[0],result_ref=row[1],message_id=row[2]) if row else None

    async def create_draft(self,draft):
        columns='id,tenant_id,conversation_id,actor_principal_id,actor_session_id,created_session_id,action_type,payload,destination,destination_queue_id,displayed_summary,language,version,payload_hash,created_at,expires_at,case_id'
        values=(draft.id,draft.tenant_id,draft.conversation_id,draft.actor_principal_id,draft.actor_session_id,draft.created_session_id,draft.action_type,
                Jsonb(draft.payload.model_dump(mode='json')),Jsonb(draft.destination.model_dump(mode='json')),draft.destination.queue_id,
                draft.displayed_summary,draft.language,draft.version,draft.payload_hash,draft.created_at,draft.expires_at,draft.case_id)
        columns+=',resource_security_labels'
        values+=(Jsonb(list(draft.resource_security_labels)),)
        if draft.source_evidence is not None:
            columns+=',source_observations'
            values+=(Jsonb(draft.source_evidence.to_records()),)
        row=await(await self._c.execute(self.query('INSERT INTO {s}.action_drafts('+columns+') VALUES('+','.join(['%s']*len(values))+') RETURNING '+STORED),values)).fetchone()
        return draft_row(row)

    async def edit_draft(self,draft):
        row=await(await self._c.execute(self.query("UPDATE {s}.action_drafts SET payload=%s,destination=%s,destination_queue_id=%s,displayed_summary=%s,language=%s,version=%s,payload_hash=%s WHERE tenant_id=%s AND id=%s AND state='pending' AND expires_at>clock_timestamp() RETURNING "+STORED),
            (Jsonb(draft.payload.model_dump(mode='json')),Jsonb(draft.destination.model_dump(mode='json')),draft.destination.queue_id,
             draft.displayed_summary,draft.language,draft.version,draft.payload_hash,draft.tenant_id,draft.id))).fetchone()
        return draft_row(row)

    async def terminal(self,id,state):
        row=await(await self._c.execute(self.query("UPDATE {s}.action_drafts SET state=CASE WHEN expires_at<=clock_timestamp() THEN 'expired' ELSE %s END WHERE tenant_id=%s AND id=%s AND state='pending' RETURNING "+STORED),
            (state,self._context.tenant_id,id))).fetchone()
        return draft_row(row)


    async def validate_attachments(self,id,ids):
        available=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.private_attachments_ready()',))).fetchone())[0]
        if available:
            if (await(await self._c.execute(self.query('SELECT {s}.private_attachments_ready()'))).fetchone())[0] is not True:return False
            rows=await(await self._c.execute(self.query("""SELECT a.id FROM {s}.attachments a
                JOIN {s}.action_drafts d ON(d.tenant_id,d.id)=(a.tenant_id,a.draft_id)
                WHERE a.tenant_id=%s AND a.draft_id=%s AND a.id=ANY(%s) AND a.scan_status='clean'
                  AND a.created_session_id=d.created_session_id AND {s}.source_labels_valid(a.resource_security_labels)
                  AND {s}.source_labels_contains(d.resource_security_labels,a.resource_security_labels)
                ORDER BY a.id FOR SHARE OF a"""),(self._context.tenant_id,id,list(ids)))).fetchall()
        else:
            rows=await(await self._c.execute(self.query("SELECT id FROM {s}.attachments WHERE tenant_id=%s AND draft_id=%s AND id=ANY(%s) AND scan_status='clean' AND resource_security_labels='[]'::jsonb ORDER BY id FOR SHARE"),
                (self._context.tenant_id,id,list(ids)))).fetchall()
        return {r[0] for r in rows}==set(ids)


    async def lock_draft_case(self,id):
        row=await(await self._c.execute(self.query('SELECT case_id FROM {s}.action_drafts WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        if row and row[0] is not None:
            if await self.handoff_case(row[0],lock=True) is None:raise AuthError(404,'RESOURCE_NOT_FOUND')

    async def handoff_case(self,id,*,lock=False):
        row=await(await self._c.execute(self.query('SELECT c.id,c.status,c.initial_message_id,c.public_reference,q.id,q.organization_id,q.name,o.name,q.service_type FROM {s}.cases c JOIN {s}.service_queues q ON(q.tenant_id,q.id)=(c.tenant_id,c.queue_id) JOIN {s}.organizations o ON(o.tenant_id,o.id)=(c.tenant_id,c.organization_id) WHERE c.tenant_id=%s AND c.id=%s AND q.active AND o.active'+(' FOR UPDATE OF c' if lock else '')),(self._context.tenant_id,id))).fetchone()
        if row is None:return None
        return dict(id=row[0],status=row[1],message_id=row[2],result_ref=row[3],destination=Destination(queue_id=row[4],organization_id=row[5],queue_name=row[6],organization_name=row[7],service_type=row[8]))

    async def active_handoff(self,id):
        return (await(await self._c.execute(self.query("SELECT EXISTS(SELECT 1 FROM {s}.conversations WHERE tenant_id=%s AND case_id=%s AND mode IN('waiting_staff','human'))"),(self._context.tenant_id,id))).fetchone())[0]
