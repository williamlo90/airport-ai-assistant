"""Scheduler-only roles and persisted, lease-fenced outbox execution."""
from alembic import context,op
from airport_assistant.infrastructure.database.provisioning import prepare_roles

revision='0014_job_worker'
down_revision='0013_chat_publication'
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes
    prepare_roles(op.get_bind(),attrs['worker_role'],attrs['job_owner_role'])
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,worker,owner=(quote(attrs[k]) for k in ('schema','runtime_role','worker_role','job_owner_role'))
    op.execute(f"""ALTER TABLE {q}.outbox_jobs DROP CONSTRAINT outbox_jobs_state_check,DROP CONSTRAINT outbox_jobs_attempts_check,
      ADD COLUMN replay_policy text NOT NULL DEFAULT 'reconcile' CHECK(replay_policy IN('safe','reconcile')),
      ADD COLUMN lease_worker_id uuid,ADD COLUMN lease_token uuid,ADD COLUMN lease_generation bigint NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
      ADD COLUMN leased_at timestamptz,ADD COLUMN lease_until timestamptz,ADD COLUMN completed_at timestamptz,ADD COLUMN result_code text,
      ADD CONSTRAINT job_state CHECK(state IN('queued','leased','retry','succeeded','dead','blocked','unknown')),
      ADD CONSTRAINT job_attempts CHECK(attempts BETWEEN 0 AND max_attempts),
      ADD CONSTRAINT job_max_attempts CHECK(max_attempts BETWEEN 1 AND 10),
      ADD CONSTRAINT job_code CHECK(result_code IS NULL OR result_code IN('OK','RETRYABLE','PERMANENT','BLOCKED','UNKNOWN','HANDLER_ERROR','HANDLER_TIMEOUT','BAD_RESULT','LEASE_EXPIRED','RETRIES_EXHAUSTED')),
      ADD CONSTRAINT job_lease_shape CHECK((state='leased' AND lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND leased_at IS NOT NULL AND lease_until IS NOT NULL AND lease_until>leased_at AND lease_until<=leased_at+interval '300 seconds' AND completed_at IS NULL)
        OR(state<>'leased' AND lease_worker_id IS NULL AND lease_token IS NULL AND leased_at IS NULL AND lease_until IS NULL)),
      ADD CONSTRAINT job_terminal_shape CHECK((state IN('succeeded','dead','blocked','unknown'))=(completed_at IS NOT NULL))""")
    op.execute(f"CREATE INDEX outbox_due ON {q}.outbox_jobs(next_attempt_at,id) WHERE state IN('queued','retry')")
    op.execute(f"CREATE INDEX outbox_expired ON {q}.outbox_jobs(lease_until,id) WHERE state='leased'")
    op.execute(f"REVOKE INSERT ON {q}.outbox_jobs FROM {runtime}")
    op.execute(f"GRANT INSERT(tenant_id,id,case_id,action_draft_id,dedupe_key,payload) ON {q}.outbox_jobs TO {runtime}")
    op.execute(f"GRANT USAGE ON SCHEMA {q} TO {worker},{owner}")
    op.execute(f"GRANT SELECT ON {q}.outbox_jobs TO {owner}")
    op.execute(f"GRANT UPDATE(state,attempts,next_attempt_at,lease_worker_id,lease_token,lease_generation,leased_at,lease_until,completed_at,result_code) ON {q}.outbox_jobs TO {owner}")
    op.execute(f"CREATE POLICY scheduler_read ON {q}.outbox_jobs FOR SELECT TO {owner} USING(true)")
    op.execute(f"CREATE POLICY scheduler_update ON {q}.outbox_jobs FOR UPDATE TO {owner} USING(true) WITH CHECK(true)")
    op.execute(f"GRANT SELECT(id,active) ON {q}.tenants TO {owner}")
    op.execute(f"CREATE POLICY scheduler_tenant_read ON {q}.tenants FOR SELECT TO {owner} USING(true)")
    op.execute(f"GRANT CREATE ON SCHEMA {q} TO {owner}")
    functions=[('job_claim(uuid,text[],integer)',f"""
      CREATE FUNCTION {q}.job_claim(w uuid,kinds text[],seconds integer) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j {q}.outbox_jobs%ROWTYPE;r record;stamp timestamptz;BEGIN
        IF w IS NULL OR kinds IS NULL OR cardinality(kinds) NOT BETWEEN 1 AND 32 OR seconds IS NULL OR seconds NOT BETWEEN 3 AND 300
          OR EXISTS(SELECT 1 FROM unnest(kinds) k WHERE k IS NULL OR length(k) NOT BETWEEN 1 AND 80) THEN RAISE EXCEPTION 'Invalid scheduler input' USING ERRCODE='22023';END IF;
        FOR r IN SELECT x.tenant_id,x.id FROM {q}.outbox_jobs x JOIN {q}.tenants t ON t.id=x.tenant_id
          WHERE t.active AND x.kind=ANY(kinds) AND x.state='leased' AND x.lease_until<=clock_timestamp()
          ORDER BY x.lease_until,x.id LIMIT 100 FOR UPDATE OF x SKIP LOCKED LOOP
          UPDATE {q}.outbox_jobs SET state=CASE WHEN replay_policy='reconcile' THEN 'unknown' WHEN attempts>=max_attempts THEN 'dead' ELSE 'retry' END,
            result_code=CASE WHEN replay_policy='reconcile' THEN 'LEASE_EXPIRED' WHEN attempts>=max_attempts THEN 'RETRIES_EXHAUSTED' ELSE 'LEASE_EXPIRED' END,
            completed_at=CASE WHEN replay_policy='reconcile' OR attempts>=max_attempts THEN clock_timestamp() ELSE NULL END,
            next_attempt_at=clock_timestamp(),lease_worker_id=NULL,lease_token=NULL,leased_at=NULL,lease_until=NULL
            WHERE tenant_id=r.tenant_id AND id=r.id;
        END LOOP;
        SELECT x.* INTO j FROM {q}.outbox_jobs x JOIN {q}.tenants t ON t.id=x.tenant_id
          WHERE t.active AND x.kind=ANY(kinds) AND x.state IN('queued','retry') AND x.attempts<x.max_attempts AND x.next_attempt_at<=clock_timestamp()
          ORDER BY x.next_attempt_at,x.id LIMIT 1 FOR UPDATE OF x SKIP LOCKED;
        IF NOT FOUND THEN RETURN NULL;END IF;
        stamp:=clock_timestamp();
        UPDATE {q}.outbox_jobs SET state='leased',attempts=attempts+1,lease_worker_id=w,lease_token=gen_random_uuid(),lease_generation=lease_generation+1,
          leased_at=stamp,lease_until=stamp+make_interval(secs=>seconds),completed_at=NULL,result_code=NULL
          WHERE tenant_id=j.tenant_id AND id=j.id RETURNING * INTO j;
        RETURN jsonb_build_object('tenant_id',j.tenant_id,'id',j.id,'case_id',j.case_id,'draft_id',j.action_draft_id,'kind',j.kind,
          'attempts',j.attempts,'generation',j.lease_generation,'token',j.lease_token,'replay_policy',j.replay_policy);
      END $$"""),('job_renew(uuid,uuid,uuid,uuid,bigint,integer)',f"""
      CREATE FUNCTION {q}.job_renew(t uuid,jid uuid,w uuid,token uuid,generation bigint,seconds integer) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j {q}.outbox_jobs%ROWTYPE;stamp timestamptz;BEGIN
        IF seconds IS NULL OR seconds NOT BETWEEN 3 AND 300 THEN RAISE EXCEPTION 'Invalid renewal' USING ERRCODE='22023';END IF;
        SELECT * INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
        IF NOT FOUND OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w OR j.lease_token IS DISTINCT FROM token OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp()
          OR NOT EXISTS(SELECT 1 FROM {q}.tenants WHERE id=t AND active) THEN RETURN false;END IF;
        stamp:=clock_timestamp();
        UPDATE {q}.outbox_jobs SET leased_at=stamp,lease_until=stamp+make_interval(secs=>seconds)
          WHERE tenant_id=t AND id=jid AND lease_until>clock_timestamp();RETURN FOUND;
      END $$"""),('job_finish(uuid,uuid,uuid,uuid,bigint,text,text)',f"""
      CREATE FUNCTION {q}.job_finish(t uuid,jid uuid,w uuid,token uuid,generation bigint,outcome text,code text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j {q}.outbox_jobs%ROWTYPE;final_state text;BEGIN
        IF outcome IS NULL OR outcome NOT IN('succeeded','retry','dead','blocked','unknown') OR code IS NULL OR code NOT IN('OK','RETRYABLE','PERMANENT','BLOCKED','UNKNOWN','HANDLER_ERROR','HANDLER_TIMEOUT','BAD_RESULT') THEN RAISE EXCEPTION 'Invalid result' USING ERRCODE='22023';END IF;
        IF NOT((outcome='succeeded' AND code='OK') OR(outcome='retry' AND code='RETRYABLE') OR(outcome='dead' AND code='PERMANENT') OR(outcome='blocked' AND code='BLOCKED') OR(outcome='unknown' AND code IN('UNKNOWN','HANDLER_ERROR','HANDLER_TIMEOUT','BAD_RESULT'))) THEN RAISE EXCEPTION 'Invalid result pair' USING ERRCODE='22023';END IF;
        SELECT * INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
        IF NOT FOUND OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w OR j.lease_token IS DISTINCT FROM token OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN false;END IF;
        final_state:=CASE WHEN outcome='retry' AND j.attempts>=j.max_attempts THEN 'dead' ELSE outcome END;
        UPDATE {q}.outbox_jobs SET state=final_state,result_code=CASE WHEN outcome='retry' AND final_state='dead' THEN 'RETRIES_EXHAUSTED' ELSE code END,
          next_attempt_at=CASE WHEN final_state='retry' THEN clock_timestamp()+make_interval(secs=>least(300,power(2,j.attempts)::integer)) ELSE next_attempt_at END,
          completed_at=CASE WHEN final_state IN('succeeded','dead','blocked','unknown') THEN clock_timestamp() ELSE NULL END,
          lease_worker_id=NULL,lease_token=NULL,leased_at=NULL,lease_until=NULL WHERE tenant_id=t AND id=jid AND lease_until>clock_timestamp();RETURN FOUND;
      END $$""")]
    for signature,definition in functions:
        op.execute(definition)
        op.execute(f"REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION {q}.{signature} OWNER TO {owner}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{signature} TO {worker}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")


def downgrade():raise RuntimeError('Worker downgrade requires a reviewed recovery migration.')
