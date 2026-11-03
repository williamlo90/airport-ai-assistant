"""Scoped case storage; lifecycle decisions are in modules/cases/lifecycle."""
from contextlib import asynccontextmanager
from uuid import UUID
from airport_assistant.infrastructure.database.case_repository import PostgresCases,CaseSQL
from airport_assistant.infrastructure.database.connections import DatabaseError
from airport_assistant.modules.identity.customer_transaction import CustomerSessionLost

FIELDS=('id','public_reference','owner_principal_id','owner_anonymous_session_id','organization_id','queue_id','category','service_type',
    'summary','customer_report','booking_reference','transaction_reference','language','status','assigned_membership_id','next_actor','next_action',
    'close_reason','resolution','duplicate_of_case_id','followup_evidence','closed_at','updated_at','version')
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

    async def history(self,before,after,event,*,command=None,digest=None):
        names=('assigned_membership_id','next_actor','next_action','close_reason','resolution','duplicate_of_case_id','followup_evidence')
        await self._c.execute(self.query('INSERT INTO {s}.case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version,from_status,to_status,'+
            ','.join(names)+',customer_message,client_request_id,request_hash) VALUES('+','.join(['%s']*18)+')'),
            (self._context.tenant_id,after['id'],self._context.principal_id,event,before['version'],after['version'],before['status'],after['status'],
             *(after[k] for k in names),command.message if command else None,command.client_request_id if command else None,digest))

    async def reopen_receipt(self,id,request):
        row=await(await self._c.execute(self.query("SELECT request_hash,resulting_version FROM {s}.case_history WHERE tenant_id=%s AND case_id=%s AND event_type='reopened' AND actor_principal_id=%s AND client_request_id=%s"),
            (self._context.tenant_id,id,self._context.principal_id,request))).fetchone()
        return dict(request_hash=row[0],version=row[1]) if row else None
