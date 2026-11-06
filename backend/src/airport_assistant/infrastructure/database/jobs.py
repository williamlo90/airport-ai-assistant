"""Scheduler-only credential; no customer-session privileges or body reads."""
from psycopg import sql
from airport_assistant.infrastructure.database.connections import RuntimeDatabase,DatabaseError
from airport_assistant.infrastructure.database.migrations import validate_schema
from airport_assistant.modules.jobs.service import Lease


PRIVATE_MAIL_FUNCTIONS=(
    'mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)',
    'mail_private_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',
    'mail_private_send_snapshot(uuid,uuid,uuid,uuid,bigint,uuid)',
    'mail_private_abort_unsent(uuid,uuid,uuid,uuid,bigint,uuid,text)',
    'mail_private_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text,text)',
    'email_private_reply_candidate(uuid,uuid,uuid,uuid,bigint)',
    'email_private_reply_apply(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',
    'mail_private_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)',
    'mail_private_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)')
LEGACY_WORKER_FUNCTIONS=(
    'job_claim(uuid,text[],integer)','job_renew(uuid,uuid,uuid,uuid,bigint,integer)',
    'job_finish(uuid,uuid,uuid,uuid,bigint,text,text)',
    'mail_snapshot(uuid,uuid,uuid,uuid,bigint)','mail_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',
    'mail_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text)',
    'mail_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)',
    'mail_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)',
    'email_inbox_snapshot(uuid,uuid,uuid,uuid,bigint)','email_inbox_bind(uuid,uuid,uuid,uuid,bigint)',
    'email_inbox_complete(uuid,uuid,uuid,uuid,bigint,jsonb,text)',
    'email_reply_snapshot(uuid,uuid,uuid,uuid,bigint)',
    'email_reply_apply(uuid,uuid,uuid,uuid,bigint,text,text,uuid,text)',
    'email_challenge_snapshot(uuid,uuid,uuid,uuid,bigint)','email_challenge_reserve(uuid,uuid,uuid,uuid,bigint)',
    'email_challenge_sent(uuid,uuid,uuid,uuid,bigint,text,text)')


class WorkerDatabase(RuntimeDatabase):
    def __init__(self,*args,schema='app',**kwargs):
        validate_schema(schema);self.schema=schema
        self.mail_delivery_required=False
        self.mail_inbox_required=False
        self.mail_reply_required=False
        self.private_mail_required=False
        super().__init__(*args,schema=schema,**kwargs)

    async def _validate_role(self,connection):
        await super()._validate_role(connection)
        checks=await(await connection.execute('''SELECT
            has_function_privilege(current_user,%s,'EXECUTE'),has_function_privilege(current_user,%s,'EXECUTE'),has_function_privilege(current_user,%s,'EXECUTE'),
            has_table_privilege(current_user,%s,'SELECT'),has_table_privilege(current_user,%s,'UPDATE'),
            has_table_privilege(current_user,%s,'SELECT'),has_table_privilege(current_user,%s,'SELECT')''',
            (self.schema+'.job_claim(uuid,text[],integer)',self.schema+'.job_renew(uuid,uuid,uuid,uuid,bigint,integer)',self.schema+'.job_finish(uuid,uuid,uuid,uuid,bigint,text,text)',
             self.schema+'.outbox_jobs',self.schema+'.outbox_jobs',self.schema+'.cases',self.schema+'.messages'))).fetchone()
        if checks!=(True,True,True,False,False,False,False):raise DatabaseError()
        allowed=[self.schema+'.'+name for name in (*LEGACY_WORKER_FUNCTIONS,*PRIVATE_MAIL_FUNCTIONS)]
        unsafe=await(await connection.execute('''SELECT has_schema_privilege(current_user,%s,'CREATE') OR EXISTS(
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN('r','p','v','m','f') AND(
              has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
            OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s AND p.prosecdef
              AND has_function_privilege(current_user,p.oid,'EXECUTE') AND NOT EXISTS(
                SELECT 1 FROM unnest(%s::text[]) permitted(signature) WHERE p.oid=to_regprocedure(permitted.signature)))''',
            (self.schema,self.schema,self.schema,allowed))).fetchone()
        if unsafe is None or unsafe[0]:raise DatabaseError()
        mail=await(await connection.execute('''SELECT count(*),count(*) FILTER(WHERE has_function_privilege(current_user,p.oid,'EXECUTE'))
            FROM pg_proc p WHERE p.oid IN(COALESCE(to_regprocedure(%s),0),COALESCE(to_regprocedure(%s),0),COALESCE(to_regprocedure(%s),0))''',
            (self.schema+'.mail_snapshot(uuid,uuid,uuid,uuid,bigint)',self.schema+'.mail_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',self.schema+'.mail_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text)'))).fetchone()
        if mail not in ((0,0),(3,3)):raise DatabaseError()
        delivery=await(await connection.execute("SELECT count(*),count(*) FILTER(WHERE has_function_privilege(current_user,p.oid,'EXECUTE')) FROM pg_proc p WHERE p.oid IN(COALESCE(to_regprocedure(%s),0),COALESCE(to_regprocedure(%s),0))",
            (self.schema+'.mail_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)',self.schema+'.mail_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)'))).fetchone()
        if delivery not in ((0,0),(2,2)):raise DatabaseError()
        if self.mail_delivery_required and (mail!=(3,3) or delivery!=(2,2)):raise DatabaseError()
        inbox=await(await connection.execute("SELECT count(*),count(*) FILTER(WHERE has_function_privilege(current_user,p.oid,'EXECUTE')) FROM pg_proc p WHERE p.oid IN(COALESCE(to_regprocedure(%s),0),COALESCE(to_regprocedure(%s),0),COALESCE(to_regprocedure(%s),0))",
            (self.schema+'.email_inbox_snapshot(uuid,uuid,uuid,uuid,bigint)',self.schema+'.email_inbox_bind(uuid,uuid,uuid,uuid,bigint)',self.schema+'.email_inbox_complete(uuid,uuid,uuid,uuid,bigint,jsonb,text)'))).fetchone()
        if inbox not in ((0,0),(3,3)) or (self.mail_inbox_required and inbox!=(3,3)):raise DatabaseError()
        reply=await(await connection.execute("SELECT count(*),count(*) FILTER(WHERE has_function_privilege(current_user,p.oid,'EXECUTE')) FROM pg_proc p WHERE p.oid IN("+','.join(['COALESCE(to_regprocedure(%s),0)']*5)+")",
          tuple(self.schema+'.'+v for v in ('email_reply_snapshot(uuid,uuid,uuid,uuid,bigint)','email_reply_apply(uuid,uuid,uuid,uuid,bigint,text,text,uuid,text)','email_challenge_snapshot(uuid,uuid,uuid,uuid,bigint)','email_challenge_reserve(uuid,uuid,uuid,uuid,bigint)','email_challenge_sent(uuid,uuid,uuid,uuid,bigint,text,text)')))).fetchone()
        if reply not in ((0,0),(5,5)) or (self.mail_reply_required and reply!=(5,5)):raise DatabaseError()


        private=await(await connection.execute("SELECT count(*),count(*) FILTER(WHERE has_function_privilege(current_user,p.oid,'EXECUTE')) FROM pg_proc p WHERE p.oid IN(SELECT to_regprocedure(name) FROM unnest(%s::text[]) names(name))",
            ([self.schema+'.'+name for name in PRIVATE_MAIL_FUNCTIONS],))).fetchone()
        if private not in ((0,0),(9,9)) or (self.private_mail_required and private!=(9,9)):raise DatabaseError()
        if private==(9,9):
            ready=await(await connection.execute(sql.SQL('SELECT {}.private_mail_ready()').format(sql.Identifier(self.schema)))).fetchone()
            if ready!=(True,):raise DatabaseError()


class PostgresScheduler:
    def __init__(self,database,schema='app',*,mail_accounts=(),job_scope_ids=None):
        validate_schema(schema);self._database,self._schema=database,schema
        self.mail_accounts=tuple(mail_accounts)
        self.job_scope_ids=job_scope_ids

    @property
    def job_scope_ids(self):return self._job_scope_ids

    @job_scope_ids.setter
    def job_scope_ids(self,value):
        from uuid import UUID
        if value is None:self._job_scope_ids=None;return
        values=tuple(value)
        if len(values)>1000 or any(not isinstance(id,UUID) for id in values):raise ValueError('A bounded UUID job scope is required.')
        self._job_scope_ids=values

    def query(self,value):return sql.SQL(value).format(s=sql.Identifier(self._schema))

    async def claim(self,worker_id,kinds,seconds):
        if not kinds:return None
        import json
        async with self._database.transaction() as c:
            if self.job_scope_ids is not None:
                supported=(await(await c.execute(self.query('SELECT {s}.job_scope_supported()'))).fetchone())[0]
                if supported is not True:raise DatabaseError()
            scope='' if self.job_scope_ids is None else json.dumps([str(id) for id in self.job_scope_ids])
            await c.execute("SELECT set_config('app.job_scope_ids',%s,true)",(scope,))
            await c.execute("SELECT set_config('app.mail_accounts',%s,true)",(json.dumps(list(self.mail_accounts)),))
            row=await(await c.execute(self.query('SELECT {s}.job_claim(%s,%s,%s)'),(worker_id,list(kinds),seconds))).fetchone()
        value=row[0]
        if value is None:return None
        from uuid import UUID
        return Lease(UUID(value['tenant_id']),UUID(value['id']),UUID(value['case_id']) if value['case_id'] else None,UUID(value['draft_id']) if value['draft_id'] else None,value['kind'],value['attempts'],value['generation'],worker_id,UUID(value['token']),value['replay_policy'])

    async def renew(self,lease,seconds):
        async with self._database.transaction() as c:
            return (await(await c.execute(self.query('SELECT {s}.job_renew(%s,%s,%s,%s,%s,%s)'),(lease.tenant_id,lease.id,lease.worker_id,lease.token,lease.generation,seconds))).fetchone())[0]

    async def finish(self,lease,outcome):
        async with self._database.transaction() as c:
            return (await(await c.execute(self.query('SELECT {s}.job_finish(%s,%s,%s,%s,%s,%s,%s)'),(lease.tenant_id,lease.id,lease.worker_id,lease.token,lease.generation,outcome.state,outcome.code))).fetchone())[0]
