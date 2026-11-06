"""Scoped case storage; lifecycle decisions are in modules/cases/lifecycle."""
from contextlib import asynccontextmanager
from uuid import UUID
from airport_assistant.infrastructure.database.case_repository import PostgresCases,CaseSQL
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.identity.customer_transaction import CustomerSessionLost

FIELDS=('id','public_reference','owner_principal_id','owner_anonymous_session_id','organization_id','queue_id','category','service_type',
    'summary','customer_report','booking_reference','transaction_reference','language','status','assigned_membership_id','next_actor','next_action',
    'close_reason','resolution','duplicate_of_case_id','followup_evidence','closed_at','updated_at','version','resource_security_labels')
UPDATES=frozenset(('status','assigned_membership_id','next_actor','next_action','close_reason','resolution','duplicate_of_case_id','followup_evidence'))


class PostgresCaseLifecycle(PostgresCases):
    @asynccontextmanager
    async def transaction(self,context):
        try:
            async with self._database.transaction(context) as connection:yield self.unit(connection,context)
        except DatabaseError as error:
            if error.sqlstate in ('42501','23514'):
                # A guard may see its parent disappear under RLS when the
                # session expires during a write. Confirm loss after rollback;
                # a real integrity failure with a live session stays an error.
                async with self._database.transaction(context) as connection:
                    active=await self.unit(connection,context).session_active()
                if not active:raise CustomerSessionLost() from None
            raise

    def unit(self,connection,context):return LifecycleSQL(connection,self._schema,context)


class LifecycleSQL(CaseSQL):
    async def mail_history_context(self,id,view):
        if view not in {'replies','deliveries'}:raise ValueError('Explicit mail history view required.')
        return (await(await self._c.execute(self.query('SELECT {s}.mail_private_history_context(%s,%s)'),(id,view))).fetchone())[0]

    async def queue_workspace(self,membership,queue_id):
        return (await(await self._c.execute(self.query('SELECT {s}.case_staff_queue_workspace(%s,%s)'),(membership,queue_id))).fetchone())[0]

    async def queue_clock(self):
        return (await(await self._c.execute('SELECT clock_timestamp()')).fetchone())[0]

    async def customer_case_page(self,status,watermark,position,limit):
        fields=('id','public_reference','status','summary','next_actor','next_action','version','created_at','updated_at')
        where='c.tenant_id=%s AND c.owner_principal_id=%s AND c.created_at<=%s'
        params=[self._context.tenant_id,self._context.principal_id,watermark]
        if status is not None:where+=' AND c.status=%s';params.append(status)
        if position is not None:where+=' AND(c.created_at,c.id)>(%s,%s)';params.extend(position)
        params.append(limit)
        rows=await(await self._c.execute(self.query('SELECT '+','.join('c.'+f for f in fields)+
            ',{s}.customer_case_ancestry(c.id) FROM {s}.cases c WHERE '+where+' ORDER BY c.created_at,c.id LIMIT %s'),params)).fetchall()
        return [dict(zip((*fields,'resource_security_labels'),row)) for row in rows]

    async def queue_page(self,organization,queues,membership,view,status,watermark,position,limit):
        fields=('id','public_reference','status','summary','assigned_membership_id','next_actor','next_action','version','updated_at','queue_id','created_at')
        where='tenant_id=%s AND organization_id=%s AND queue_id=ANY(%s::uuid[]) AND created_at<=%s'
        params=[self._context.tenant_id,organization,queues,watermark]
        if view=='mine':where+=' AND assigned_membership_id=%s';params.append(membership)
        elif view=='unassigned':where+=' AND assigned_membership_id IS NULL'
        if status is not None:where+=' AND status=%s';params.append(status)
        if position is not None:where+=' AND (created_at,id)>(%s,%s)';params.extend(position)
        params.append(limit)
        rows=await(await self._c.execute(self.query('SELECT '+','.join(fields)+' FROM {s}.cases WHERE '+where+' ORDER BY created_at,id LIMIT %s'),params)).fetchall()
        return [dict(zip(fields,row)) for row in rows]

    async def staff_workspace(self,id,membership):
        return (await(await self._c.execute(self.query('SELECT {s}.case_staff_workspace(%s,%s)'),(id,membership))).fetchone())[0]

    async def staff_assignees(self,id,membership,after,limit):
        return (await(await self._c.execute(self.query('SELECT {s}.case_staff_assignees(%s,%s,%s,%s)'),(id,membership,after,limit))).fetchone())[0]

    async def duplicate_candidate(self,reference,case,membership):
        # Existing forced RLS handles tenant/principal ACL; the workspace RPC
        # additionally requires this exact membership for the candidate queue.
        # No target case lock: reciprocal lookups cannot lock cases in reverse order.
        row=await(await self._c.execute(self.query('''SELECT id,public_reference FROM {s}.cases
            WHERE tenant_id=%s AND public_reference=%s AND id<>%s AND organization_id=%s
              AND owner_principal_id IS NOT DISTINCT FROM %s
              AND owner_anonymous_session_id IS NOT DISTINCT FROM %s
              AND {s}.case_staff_workspace(id,%s) IS NOT NULL LIMIT 1'''),
            (self._context.tenant_id,reference,case['id'],case['organization_id'],case['owner_principal_id'],case['owner_anonymous_session_id'],membership))).fetchone()
        return dict(case_id=row[0],public_reference=row[1]) if row else None

    async def session_active(self):
        if self._context.actor_kind!='staff':return await super().session_active()
        return (await(await self._c.execute(self.query('SELECT {s}.case_staff_session_valid()'))).fetchone())[0]

    async def find_case(self,id,*,lock=False):
        row=await(await self._c.execute(self.query('SELECT '+','.join(FIELDS)+' FROM {s}.cases WHERE tenant_id=%s AND id=%s'+(' FOR UPDATE' if lock else '')),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(FIELDS,row)) if row else None

    async def public_case(self,case):
        fields=('id','public_reference','category','service_type','summary','customer_report','booking_reference','transaction_reference','language',
            'status','assigned_membership_id','next_actor','next_action','close_reason','resolution','closed_at','updated_at','version')
        result={k:case[k] for k in fields}
        result['related_case_ref']=None
        if case['duplicate_of_case_id'] is not None:
            related=await(await self._c.execute(self.query('SELECT public_reference FROM {s}.cases WHERE tenant_id=%s AND id=%s'),
                (self._context.tenant_id,case['duplicate_of_case_id']))).fetchone()
            if related:result['related_case_ref']=related[0]
        rows=await(await self._c.execute(self.query('''SELECT event_type,previous_version,resulting_version,from_status,to_status,next_actor,next_action,
            close_reason,resolution,customer_message,created_at FROM {s}.case_history WHERE tenant_id=%s AND case_id=%s AND resulting_version<=%s ORDER BY resulting_version'''),
            (self._context.tenant_id,case['id'],case['version']))).fetchall()
        result['history']=[dict(zip(('event_type','previous_version','resulting_version','from_status','to_status','next_actor','next_action','close_reason','resolution','customer_message','created_at'),r)) for r in rows]
        return result

    async def related_source_context(self,id):
        fields='id,version,resource_security_labels'
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.cases WHERE tenant_id=%s AND id=%s'),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def continuation_ancestry(self,id):
        exists=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.customer_case_ancestry(uuid)',))).fetchone())[0]
        if exists:
            return (await(await self._c.execute(self.query('SELECT {s}.customer_case_ancestry(%s)'),(id,))).fetchone())[0]
        # Legacy public CASE-03 schemas predate source provenance. Never treat a
        # private legacy parent as public because the newer helper is absent.
        row=await(await self._c.execute(self.query('''SELECT c.resource_security_labels FROM {s}.cases c
            WHERE c.tenant_id=%s AND c.id=%s AND c.owner_principal_id=%s
            AND c.resource_security_labels='[]'::jsonb AND EXISTS(SELECT 1 FROM {s}.case_participants p
              WHERE p.tenant_id=c.tenant_id AND p.case_id=c.id AND p.principal_id=%s AND p.revoked_at IS NULL)'''),
            (self._context.tenant_id,id,self._context.principal_id,self._context.principal_id))).fetchone()
        return row[0] if row else None

    async def email_replies(self,id):
        rows=await(await self._c.execute(self.query('''SELECT r.email_id,r.state,r.reason,r.app_message_id,r.created_at,m.body,
            CASE WHEN ch.state='pending' AND ch.expires_at<=clock_timestamp() THEN 'expired' ELSE ch.state END,
            ch.send_state,ch.attempts
            FROM {s}.email_reply_state r LEFT JOIN {s}.messages m ON m.tenant_id=r.tenant_id AND m.id=r.app_message_id
            LEFT JOIN {s}.email_reply_challenges ch ON ch.tenant_id=r.tenant_id AND ch.email_id=r.email_id
            WHERE r.tenant_id=%s AND r.case_id=%s ORDER BY r.created_at,r.email_id LIMIT 100'''),(self._context.tenant_id,id))).fetchall()
        keys=('email_id','state','reason','app_message_id','created_at','body','verification_state','send_state','attempts')
        return [dict(zip(keys,row)) for row in rows]

    async def queue(self,status):
        # Stable bounded first page. Cursor/filter expansion belongs to COMPLETE-02.
        rows=await(await self._c.execute(self.query('''SELECT id,public_reference,status,summary,assigned_membership_id,next_actor,next_action,version,updated_at
            FROM {s}.cases WHERE tenant_id=%s'''+(' AND status=%s' if status else '')+' ORDER BY updated_at,id LIMIT 100'),
            (self._context.tenant_id,status) if status else (self._context.tenant_id,))).fetchall()
        return [dict(zip(('id','public_reference','status','summary','assigned_membership_id','next_actor','next_action','version','updated_at'),r)) for r in rows]

    async def supervisor(self,case):
        return (await(await self._c.execute(self.query('SELECT {s}.case_staff_scope(%s,%s,true)'),(case['organization_id'],case['queue_id']))).fetchone())[0]

    async def target_membership(self,id,case):
        if id is None:return None
        return (await(await self._c.execute(self.query('SELECT {s}.case_target_membership(%s,%s,%s)'),(id,case['organization_id'],case['queue_id']))).fetchone())[0]

    async def current_assignee(self,id):
        return (await(await self._c.execute(self.query('SELECT {s}.case_current_assignee(%s)'),(id,))).fetchone())[0]

    async def update_case(self,case,values):
        if not values or not values.keys()<=UPDATES:raise ValueError('Unsupported lifecycle columns.')
        sets=','.join(k+'=%s' for k in values)
        closed=",closed_at=clock_timestamp()" if values.get('status')=='closed' else ',closed_at=NULL' if 'status' in values else ''
        row=await(await self._c.execute(self.query('UPDATE {s}.cases SET '+sets+closed+',version=version+1 WHERE tenant_id=%s AND id=%s AND version=%s RETURNING '+','.join(FIELDS)),
            (*values.values(),self._context.tenant_id,case['id'],case['version']))).fetchone()
        if row is None:
            from airport_assistant.modules.drafts.models import DraftError
            raise DraftError(409,'VERSION_CONFLICT')
        return dict(zip(FIELDS,row))

    async def history(self,before,after,event,*,command=None,digest=None,transition=None):
        names=('assigned_membership_id','next_actor','next_action','close_reason','resolution','duplicate_of_case_id','followup_evidence')
        await self._c.execute(self.query('INSERT INTO {s}.case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version,from_status,to_status,'+
            ','.join(names)+',customer_message,client_request_id,request_hash'+(',chat_transition_id' if transition else '')+') VALUES('+','.join(['%s']*(19 if transition else 18))+')'),
            (self._context.tenant_id,after['id'],self._context.principal_id,event,before['version'],after['version'],before['status'],after['status'],
             *(after[k] for k in names),command.message if command else None,command.client_request_id if command else None,digest,*((transition,) if transition else ())))

    async def reopen_receipt(self,id,request):
        row=await(await self._c.execute(self.query("SELECT request_hash,resulting_version FROM {s}.case_history WHERE tenant_id=%s AND case_id=%s AND event_type='reopened' AND actor_principal_id=%s AND client_request_id=%s"),
            (self._context.tenant_id,id,self._context.principal_id,request))).fetchone()
        return dict(request_hash=row[0],version=row[1]) if row else None


    async def active_chat(self,id,*,lock=False):
        row=await(await self._c.execute(self.query("SELECT id,case_id,mode,to_jsonb(c)->>'handler_membership_id',version,generation_epoch FROM {s}.conversations c WHERE tenant_id=%s AND case_id=%s AND mode IN('waiting_staff','human')"+(' FOR UPDATE' if lock else '')),(self._context.tenant_id,id))).fetchone()
        return self.chat_row(row)

    @staticmethod
    def chat_row(row):
        if row is None:return None
        return dict(id=row[0],case_id=row[1],mode=row[2],handler_membership_id=UUID(row[3]) if row[3] else None,version=row[4],generation_epoch=row[5])

    async def chat_case(self,id):
        row=await(await self._c.execute(self.query('SELECT case_id FROM {s}.conversations WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        return row[0] if row else None

    async def chat(self,id,*,lock=False):
        row=await(await self._c.execute(self.query("SELECT id,case_id,mode,to_jsonb(c)->>'handler_membership_id',version,generation_epoch FROM {s}.conversations c WHERE tenant_id=%s AND id=%s"+(' FOR UPDATE' if lock else '')),(self._context.tenant_id,id))).fetchone()
        return self.chat_row(row)

    async def move_chat(self,chat,mode,handler,transition):
        row=await(await self._c.execute(self.query("UPDATE {s}.conversations c SET mode=%s,handler_membership_id=%s,last_transition_id=%s,version=version+1,generation_epoch=generation_epoch+1 WHERE tenant_id=%s AND id=%s AND version=%s RETURNING id,case_id,mode,to_jsonb(c)->>'handler_membership_id',version,generation_epoch"),
            (mode,handler,transition,self._context.tenant_id,chat['id'],chat['version']))).fetchone()
        if row is None:
            from airport_assistant.modules.drafts.models import DraftError
            raise DraftError(409,'VERSION_CONFLICT')
        return self.chat_row(row)

    async def accept_key(self,key):
        import json
        await self._c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(json.dumps(['chat_accept',str(self._context.tenant_id),str(self._context.principal_id),key]),))

    async def accept_receipt(self,key):
        fields='request_hash,conversation_id,case_id,result_case_version,result_conversation_version,result_epoch,to_mode,handler_membership_id'
        row=await(await self._c.execute(self.query('SELECT '+fields+" FROM {s}.chat_transitions WHERE tenant_id=%s AND actor_principal_id=%s AND kind='accept' AND request_key=%s"),(self._context.tenant_id,self._context.principal_id,key))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    @staticmethod
    def accept_view(row):
        return dict(state='accepted',conversation_id=row['conversation_id'],case_id=row['case_id'],case_version=row['result_case_version'],version=row['result_conversation_version'],generation_epoch=row['result_epoch'],mode=row['to_mode'],handler_membership_id=row['handler_membership_id'])

    async def transition(self,id,kind,before,after,old,current,*,key=None,digest=None):
        row=await(await self._c.execute(self.query('''INSERT INTO {s}.chat_transitions(tenant_id,id,conversation_id,case_id,actor_principal_id,kind,request_key,request_hash,
            previous_case_version,result_case_version,previous_conversation_version,result_conversation_version,previous_epoch,result_epoch,from_mode,to_mode,handler_membership_id,previous_assignee_id)
            VALUES('''+','.join(['%s']*18)+') ON CONFLICT DO NOTHING RETURNING id'),
            (self._context.tenant_id,id,old['id'],before['id'],self._context.principal_id,kind,key,digest,before['version'],after['version'],old['version'],current['version'],old['generation_epoch'],current['generation_epoch'],old['mode'],current['mode'],current['handler_membership_id'],before['assigned_membership_id']))).fetchone()
        if row is None:
            from airport_assistant.modules.drafts.models import DraftError
            raise DraftError(409,'IDEMPOTENCY_CONFLICT')
        return dict(request_hash=digest,conversation_id=old['id'],case_id=before['id'],result_case_version=after['version'],result_conversation_version=current['version'],result_epoch=current['generation_epoch'],to_mode=current['mode'],handler_membership_id=current['handler_membership_id'])
