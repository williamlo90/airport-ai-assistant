"""Case graph persistence; rules and recipient eligibility stay in business code."""

from contextlib import asynccontextmanager
import json
from psycopg.types.json import Jsonb
from airport_assistant.infrastructure.database.draft_repository import PostgresDrafts,DraftSQL
from airport_assistant.modules.cases.models import CommitResult
from airport_assistant.modules.drafts.models import Destination


class PostgresCases(PostgresDrafts):
    def unit(self,connection,context):return CaseSQL(connection,self._schema,context)


class CaseSQL(DraftSQL):
    async def confirmation_context(self,id):
        fields='id,origin_draft_id,origin_conversation_id,source_observations,resource_security_labels'
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.cases WHERE tenant_id=%s AND id=%s'),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def receipt(self,id):
        row=await(await self._c.execute(self.query('''SELECT c.id,c.public_reference,a.confirmed_version
            FROM {s}.cases c JOIN {s}.action_consents a ON(a.tenant_id,a.case_id)=(c.tenant_id,c.id)
            WHERE c.tenant_id=%s AND a.draft_id=%s'''),(self._context.tenant_id,id))).fetchone()
        return dict(draft_id=id,state='committed',case_id=row[0],result_ref=row[1],version=row[2],expires_at=None) if row else None

    async def lock_destination(self,expected):
        organization=await(await self._c.execute(self.query('SELECT name FROM {s}.organizations WHERE tenant_id=%s AND id=%s AND active FOR SHARE'),
            (self._context.tenant_id,expected.organization_id))).fetchone()
        if organization is None:return None
        row=await(await self._c.execute(self.query('SELECT id,organization_id,name,service_type FROM {s}.service_queues WHERE tenant_id=%s AND id=%s AND organization_id=%s AND active FOR SHARE'),
            (self._context.tenant_id,expected.queue_id,expected.organization_id))).fetchone()
        return Destination(queue_id=row[0],organization_id=row[1],queue_name=row[2],organization_name=organization[0],service_type=row[3]) if row else None

    @asynccontextmanager
    async def savepoint(self):
        async with self._c.transaction():yield

    async def lock_key(self,key):
        ctx=self._context
        scope=json.dumps([str(ctx.tenant_id),str(ctx.principal_id or ctx.session_id),'confirm_draft',key])
        await self._c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(scope,))

    async def idempotency(self,key):
        ctx=self._context
        row=await(await self._c.execute(self.query("SELECT request_hash FROM {s}.idempotency_records WHERE tenant_id=%s AND operation='confirm_draft' AND key=%s AND actor_principal_id IS NOT DISTINCT FROM %s AND actor_session_id IS NOT DISTINCT FROM %s"),
            (ctx.tenant_id,key,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None))).fetchone()
        return row[0] if row else None

    async def verified_contact(self,email):
        ctx=self._context
        if ctx.actor_kind!='customer':return None
        row=await(await self._c.execute(self.query('SELECT id FROM {s}.contacts WHERE tenant_id=%s AND principal_id=%s AND verified_at IS NOT NULL'+(' AND email_lookup_key=%s' if email is not None else '')+' ORDER BY created_at,id LIMIT 1 FOR SHARE'),
            (ctx.tenant_id,ctx.principal_id,email) if email is not None else (ctx.tenant_id,ctx.principal_id))).fetchone()
        return row[0] if row else None

    async def create_case(self,draft,case_id,message_id,owner_contact,followup_contact,request_id):
        # These ordered INSERTs need no intermediate result. Send them through
        # one bounded pipeline, inside the caller's existing transaction and
        # savepoint; synchronize/errors surface before returning the result.
        async with self._c.pipeline():
            return await self._insert_case_graph(draft,case_id,message_id,owner_contact,followup_contact,request_id)

    async def _insert_case_graph(self,draft,case_id,message_id,owner_contact,followup_contact,request_id):
        ctx=self._context;report=draft.payload;principal=ctx.principal_id;session=ctx.session_id if ctx.actor_kind=='anonymous' else None
        ref='CASE-'+case_id.hex
        columns='tenant_id,id,public_reference,origin_draft_id,origin_conversation_id,created_session_id,owner_principal_id,owner_anonymous_session_id,owner_contact_id,followup_contact_id,organization_id,queue_id,category,service_type,summary,customer_report,booking_reference,transaction_reference,language,initial_message_id'
        parameters=(ctx.tenant_id,case_id,ref,draft.id,draft.conversation_id,ctx.session_id,principal,session,owner_contact,followup_contact,
            draft.destination.organization_id,draft.destination.queue_id,report.category,report.service_type,report.summary,
            report.customer_report,report.booking_reference,report.transaction_reference,draft.language,message_id)
        if draft.source_evidence is not None:
            columns+=',source_observations';parameters+=(Jsonb(draft.source_evidence.to_records()),)
        if draft.resource_security_labels:
            columns+=',resource_security_labels';parameters+=(Jsonb(list(draft.resource_security_labels)),)
        await self._c.execute(self.query('INSERT INTO {s}.cases('+columns+') VALUES('+','.join(['%s']*len(parameters))+')'),parameters)
        label_column=',resource_security_labels' if draft.resource_security_labels else ''
        label_value=',%s' if draft.resource_security_labels else ''
        parameters=(ctx.tenant_id,message_id,case_id,principal,session,'customer',report.customer_report,draft.language,draft.id)
        if draft.resource_security_labels:parameters+=(Jsonb(list(draft.resource_security_labels)),)
        await self._c.execute(self.query('INSERT INTO {s}.messages(tenant_id,id,case_id,author_principal_id,author_session_id,author_type,body,language,client_message_id,sequence'+label_column+') VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1'+label_value+')'),parameters)
        await self._c.execute(self.query('INSERT INTO {s}.action_consents(tenant_id,draft_id,case_id,confirmed_version,payload_hash,actor_principal_id,actor_session_id,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)'),
            (ctx.tenant_id,draft.id,case_id,draft.version,draft.payload_hash,principal,session,request_id))
        if principal is not None:
            await self._c.execute(self.query('INSERT INTO {s}.case_participants(tenant_id,case_id,principal_id) VALUES(%s,%s,%s)'),(ctx.tenant_id,case_id,principal))
        await self._c.execute(self.query("INSERT INTO {s}.case_history(tenant_id,case_id,actor_principal_id,actor_session_id,event_type,previous_version,resulting_version) VALUES(%s,%s,%s,%s,'created',0,1)"),
            (ctx.tenant_id,case_id,principal,session))
        await self._c.execute(self.query("INSERT INTO {s}.audit_events(tenant_id,case_id,action_draft_id,event_type,actor_principal_id,actor_session_id,request_id) VALUES(%s,%s,%s,'case_confirmed',%s,%s,%s)"),
            (ctx.tenant_id,case_id,draft.id,principal,session,request_id))
        parameters=(ctx.tenant_id,case_id,draft.id,'case.created:'+str(draft.id),Jsonb(dict(case_id=str(case_id),draft_id=str(draft.id))))
        if draft.resource_security_labels:parameters+=(Jsonb(list(draft.resource_security_labels)),)
        await self._c.execute(self.query('INSERT INTO {s}.outbox_jobs(tenant_id,case_id,action_draft_id,dedupe_key,payload'+label_column+') VALUES(%s,%s,%s,%s,%s'+label_value+')'),parameters)
        return CommitResult(case_id=case_id,result_ref=ref,message_id=message_id)

    async def mark_committed(self,draft,result):
        row=await(await self._c.execute(self.query("UPDATE {s}.action_drafts SET state='committed',result_case_id=%s,result_message_id=%s,result_ref=%s,committed_at=clock_timestamp() WHERE tenant_id=%s AND id=%s AND state='pending' AND version=%s AND payload_hash=%s AND expires_at>clock_timestamp() RETURNING id"),
            (result.case_id,result.message_id,result.result_ref,draft.tenant_id,draft.id,draft.version,draft.payload_hash))).fetchone()
        return row is not None

    async def record_key(self,key,digest,draft,result):
        ctx=self._context
        await self._c.execute(self.query('INSERT INTO {s}.idempotency_records(tenant_id,actor_principal_id,actor_session_id,created_session_id,operation,key,request_hash,draft_id,result_case_id,result_message_id,result_ref) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'),
            (ctx.tenant_id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,ctx.session_id,'confirm_draft',key,digest,draft.id,result.case_id,result.message_id,result.result_ref))


    async def handoff_consent(self,draft,request_id):
        ctx=self._context
        await self._c.execute(self.query('INSERT INTO {s}.action_consents(tenant_id,draft_id,case_id,confirmed_version,payload_hash,actor_principal_id,actor_session_id,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)'),
            (ctx.tenant_id,draft.id,draft.case_id,draft.version,draft.payload_hash,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,request_id))
