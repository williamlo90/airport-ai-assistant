"""Run checkpoints on the same restricted customer transaction as pending drafts."""
from psycopg.types.json import Jsonb
from airport_assistant.infrastructure.database.draft_repository import PostgresDrafts,DraftSQL

FIELDS=('id','conversation_id','message_id','session_id','principal_id','request_key','request_hash',
        'conversation_version','generation_epoch','language','mode','parameters','phase','revision',
        'model_calls','read_calls','trace','pending_call','proposal','lease','lease_until','result_draft_id','reason',
        'tenant_id','resource_security_labels')


class PostgresAgentRuns(PostgresDrafts):
    def unit(self,connection,context):return AgentSQL(connection,self._schema,context)


class AgentSQL(DraftSQL):
    async def locate_run(self,id):
        row=await(await self._c.execute(self.query('SELECT conversation_id FROM {s}.agent_runs WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        return row[0] if row else None

    async def run(self,id):
        columns=','.join(FIELDS[:-1])+",coalesce(to_jsonb(agent_runs)->'resource_security_labels','[]'::jsonb)"
        row=await(await self._c.execute(self.query('SELECT '+columns+' FROM {s}.agent_runs WHERE tenant_id=%s AND id=%s FOR UPDATE'),(self._context.tenant_id,id))).fetchone()
        return dict(zip(FIELDS,row)) if row else None

    async def checkpoint_candidate(self,id):
        """Current row-locked candidate with ordered SQL, not cached authority.

        Pipeline statements still run in conversation -> run -> session order,
        each with its own READ COMMITTED snapshot. Fetch only after sync. A
        missing conversation fails closed; never retry its lock after run lock.
        """
        from .conversation_repository import FIELDS as CONVERSATION_FIELDS,conversation
        cid=await self.locate_run(id)
        if cid is None:return None
        columns=','.join(FIELDS[:-1])+",coalesce(to_jsonb(agent_runs)->'resource_security_labels','[]'::jsonb)"
        async with self._c.pipeline():
            parent=await self._c.execute(self.query('SELECT '+CONVERSATION_FIELDS+' FROM {s}.conversations WHERE tenant_id=%s AND id=%s FOR UPDATE'),(self._context.tenant_id,cid))
            run=await self._c.execute(self.query('SELECT '+columns+' FROM {s}.agent_runs WHERE tenant_id=%s AND id=%s FOR UPDATE'),(self._context.tenant_id,id))
            active=await self._c.execute(self.query('SELECT {s}.conversation_session_valid()'))
        parent_row=await parent.fetchone();run_row=await run.fetchone();active_row=await active.fetchone()
        return conversation(parent_row),dict(zip(FIELDS,run_row)) if run_row else None,active_row[0]

    async def run_result_context(self,id):
        fields='id,tenant_id,conversation_id,actor_principal_id,actor_session_id,created_session_id,payload_hash,version,resource_security_labels'
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.action_drafts WHERE tenant_id=%s AND id=%s'),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def request_run(self,conversation,key):
        row=await(await self._c.execute(self.query('SELECT id FROM {s}.agent_runs WHERE tenant_id=%s AND conversation_id=%s AND request_key=%s'),(self._context.tenant_id,conversation,key))).fetchone()
        return await self.run(row[0]) if row else None

    async def message_run(self,conversation,message,mode):
        row=await(await self._c.execute(self.query('SELECT id FROM {s}.agent_runs WHERE tenant_id=%s AND conversation_id=%s AND message_id=%s AND session_id=%s AND mode=%s'),(self._context.tenant_id,conversation,message,self._context.session_id,mode))).fetchone()
        return await self.run(row[0]) if row else None

    async def create_run(self,request,scope,digest,parameters):
        ctx=self._context
        columns='tenant_id,conversation_id,message_id,session_id,principal_id,request_key,request_hash,conversation_version,generation_epoch,language,mode,parameters'
        values=(ctx.tenant_id,scope.conversation_id,scope.message_id,ctx.session_id,ctx.principal_id,request.request_key,digest,
            scope.conversation_version,scope.generation_epoch,scope.language,request.mode,Jsonb(parameters))
        if scope.labels is not None:columns+=',resource_security_labels';values+=(Jsonb(scope.labels.to_records()),)
        row=await(await self._c.execute(self.query('INSERT INTO {s}.agent_runs('+columns+') VALUES('+','.join(['%s']*len(values))+') RETURNING id'),values)).fetchone()
        return await self.run(row[0])

    async def update_run(self,run,**changes):
        allowed={'phase','model_calls','read_calls','trace','pending_call','proposal','lease','lease_until','result_draft_id','reason','resource_security_labels'}
        if not changes or not set(changes)<=allowed:raise ValueError('Invalid checkpoint fields.')
        values=[Jsonb(v) if k in {'trace','pending_call','proposal','resource_security_labels'} and v is not None else v for k,v in changes.items()]
        row=await(await self._c.execute(self.query('UPDATE {s}.agent_runs SET '+','.join(k+'=%s' for k in changes)+',revision=revision+1 WHERE tenant_id=%s AND id=%s AND revision=%s RETURNING id'),
            (*values,self._context.tenant_id,run['id'],run['revision']))).fetchone()
        if row is None:raise ValueError('Checkpoint revision changed.')
        return await self.run(row[0])
