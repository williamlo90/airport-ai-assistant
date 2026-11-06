"""Session-scoped durable agent runs; draft receipts share the business transaction."""
from alembic import context,op
revision='0026_agent_runs'
down_revision='0025_knowledge_vectors'
branch_labels=depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,r=(quote(context.config.attributes[k]) for k in ('schema','runtime_role'))
    op.execute(f'''CREATE TABLE {q}.agent_runs(
      tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),conversation_id uuid NOT NULL,
      message_id uuid NOT NULL,session_id uuid NOT NULL,principal_id uuid,request_key uuid NOT NULL,
      request_hash text NOT NULL CHECK(request_hash~'^[a-f0-9]{{64}}$'),
      conversation_version bigint NOT NULL CHECK(conversation_version>0),generation_epoch bigint NOT NULL CHECK(generation_epoch>0),
      language text NOT NULL CHECK(language IN('en','id')),mode text NOT NULL CHECK(mode IN('assist','draft')),
      parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object' AND octet_length(parameters::text)<=16000
        AND parameters ?& ARRAY['booking_references','transaction_references','knowledge','contact_email','action_type']
        AND parameters-ARRAY['booking_references','transaction_references','knowledge','contact_email','action_type']='{{}}'::jsonb),
      phase text NOT NULL DEFAULT 'ready' CHECK(phase IN('ready','planning','tool','reading','draft','waiting_user','done','review','superseded')),
      revision integer NOT NULL DEFAULT 1 CHECK(revision>0),model_calls integer NOT NULL DEFAULT 0 CHECK(model_calls BETWEEN 0 AND 3),
      read_calls integer NOT NULL DEFAULT 0 CHECK(read_calls BETWEEN 0 AND 12),
      trace jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(trace)='array' AND jsonb_array_length(trace)<=3 AND octet_length(trace::text)<=12000),
      pending_call jsonb CHECK(pending_call IS NULL OR(jsonb_typeof(pending_call)='object' AND octet_length(pending_call::text)<=4000)),
      proposal jsonb CHECK(proposal IS NULL OR(jsonb_typeof(proposal)='object' AND octet_length(proposal::text)<=2000
        AND proposal ?& ARRAY['category','service_type','summary'] AND proposal-ARRAY['category','service_type','summary']='{{}}'::jsonb)),
      lease uuid,lease_until timestamptz,result_draft_id uuid,reason text NOT NULL DEFAULT 'new',
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,conversation_id,request_key),
      FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
      FOREIGN KEY(tenant_id,message_id) REFERENCES {q}.messages(tenant_id,id),
      FOREIGN KEY(tenant_id,session_id) REFERENCES {q}.sessions(tenant_id,id),
      FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,result_draft_id) REFERENCES {q}.action_drafts(tenant_id,id),
      CHECK((phase IN('planning','reading') AND lease IS NOT NULL AND lease_until IS NOT NULL)
         OR(phase NOT IN('planning','reading') AND lease IS NULL AND lease_until IS NULL)),
      CHECK((phase='done')=(result_draft_id IS NOT NULL)),
      CHECK(phase NOT IN('tool','reading') OR pending_call IS NOT NULL),CHECK(phase<>'draft' OR(proposal IS NOT NULL AND mode='draft')),
      CHECK(result_draft_id IS NULL OR mode='draft'),CHECK(proposal IS NULL OR mode='draft'),
      CHECK(mode<>'draft' OR(trace='[]'::jsonb AND pending_call IS NULL)))''')
    op.execute(f'ALTER TABLE {q}.agent_runs ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {q}.agent_runs FORCE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON {q}.agent_runs FROM PUBLIC')
    op.execute(f'GRANT SELECT,INSERT ON {q}.agent_runs TO {r}')
    op.execute(f'''GRANT UPDATE(phase,revision,model_calls,read_calls,trace,pending_call,proposal,lease,lease_until,result_draft_id,reason,updated_at)
        ON {q}.agent_runs TO {r}''')
    gate=f'''tenant_id={q}.current_tenant_id() AND session_id={q}.current_session_id()
      AND principal_id IS NOT DISTINCT FROM {q}.current_principal_id() AND {q}.conversation_session_valid()
      AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=agent_runs.tenant_id AND c.id=agent_runs.conversation_id
        AND c.resource_security_labels='[]'::jsonb AND c.case_id IS NULL)'''
    for operation in ('SELECT','INSERT','UPDATE'):
        clause=f'WITH CHECK({gate})' if operation=='INSERT' else f'USING({gate})'+(f' WITH CHECK({gate})' if operation=='UPDATE' else '')
        op.execute(f'CREATE POLICY agent_runs_{operation.lower()} ON {q}.agent_runs FOR {operation} TO {r} {clause}')
    op.execute(f'''CREATE FUNCTION {q}.agent_run_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE c record;m record;BEGIN
        SELECT * INTO c FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id FOR UPDATE;
        IF NOT FOUND OR NEW.tenant_id IS DISTINCT FROM {q}.current_tenant_id() OR NEW.session_id IS DISTINCT FROM {q}.current_session_id()
          OR NEW.principal_id IS DISTINCT FROM {q}.current_principal_id() OR NOT {q}.conversation_session_valid()
        THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Agent access denied';END IF;
        IF TG_OP='INSERT' THEN
          SELECT * INTO m FROM {q}.messages WHERE tenant_id=NEW.tenant_id AND id=NEW.message_id AND conversation_id=NEW.conversation_id;
          IF NOT FOUND OR m.author_type<>'customer' OR m.resource_security_labels<>'[]'::jsonb OR m.sequence<>c.last_sequence
            OR c.mode<>'ai' OR NEW.conversation_version<>c.version OR NEW.generation_epoch<>c.generation_epoch OR NEW.language<>c.language
            OR NEW.phase<>'ready' OR NEW.revision<>1 OR NEW.model_calls<>0 OR NEW.read_calls<>0 OR NEW.trace<>'[]'::jsonb
            OR NEW.pending_call IS NOT NULL OR NEW.proposal IS NOT NULL OR NEW.result_draft_id IS NOT NULL
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid agent run';END IF;
        ELSE
          IF (to_jsonb(NEW)-ARRAY['phase','revision','model_calls','read_calls','trace','pending_call','proposal','lease','lease_until','result_draft_id','reason','updated_at'])
             IS DISTINCT FROM(to_jsonb(OLD)-ARRAY['phase','revision','model_calls','read_calls','trace','pending_call','proposal','lease','lease_until','result_draft_id','reason','updated_at'])
            OR NEW.revision<>OLD.revision+1 OR NEW.model_calls<OLD.model_calls OR NEW.read_calls<OLD.read_calls
            OR NEW.model_calls>OLD.model_calls+1 OR NEW.read_calls>OLD.read_calls+1
            OR OLD.phase IN('done','review','superseded','waiting_user')
            OR NOT ((OLD.phase='ready' AND NEW.phase IN('planning','review','superseded'))
              OR (OLD.phase='planning' AND NEW.phase IN('planning','tool','draft','waiting_user','review','superseded'))
              OR (OLD.phase='tool' AND NEW.phase IN('reading','review','superseded'))
              OR (OLD.phase='reading' AND NEW.phase IN('ready','tool','review','superseded'))
              OR (OLD.phase='draft' AND NEW.phase IN('done','superseded')))
            OR (NEW.phase<>'superseded' AND (c.mode<>'ai' OR c.version<>NEW.conversation_version
              OR c.generation_epoch<>NEW.generation_epoch OR c.language<>NEW.language))
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid agent transition';END IF;
        END IF;
        IF NEW.result_draft_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM {q}.action_drafts d
          WHERE d.tenant_id=NEW.tenant_id AND d.id=NEW.result_draft_id AND d.conversation_id=NEW.conversation_id
            AND d.created_session_id=NEW.session_id AND d.state='pending' AND d.resource_security_labels='[]'::jsonb)
        THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid draft receipt';END IF;
        NEW.updated_at:=clock_timestamp();RETURN NEW;END $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.agent_run_guard() FROM PUBLIC')
    op.execute(f'CREATE TRIGGER agent_run_guard BEFORE INSERT OR UPDATE ON {q}.agent_runs FOR EACH ROW EXECUTE FUNCTION {q}.agent_run_guard()')


def downgrade():raise RuntimeError('Agent recovery requires a reviewed forward migration.')
