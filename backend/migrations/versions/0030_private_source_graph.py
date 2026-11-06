"""Private conversation/draft/case/agent graph; legacy mail remains public-only."""
import hashlib
from alembic import context, op
from airport_assistant.infrastructure.database.provisioning import prepare_auth_owner

revision = '0030_private_source_graph'
down_revision = '0029_private_source_schema'
branch_labels = depends_on = None

TABLES = ('conversations', 'messages', 'action_drafts', 'cases', 'outbox_jobs',
          'agent_runs', 'ai_generations', 'staff_translation_drafts', 'case_history',
          'action_consents', 'audit_events', 'idempotency_records', 'conversation_events',
          'chat_transitions', 'attachments', 'email_reply_state', 'email_reply_challenges')
READS = {
    'conversations': 'tenant_id,id,case_id,customer_principal_id,handler_membership_id,mode,resource_security_labels',
    'cases': 'tenant_id,id,origin_draft_id,origin_conversation_id,organization_id,queue_id,assigned_membership_id,resource_security_labels,source_observations',
    'action_drafts': 'tenant_id,id,conversation_id,case_id,result_case_id,resource_security_labels,source_observations',
    'messages': 'tenant_id,id,conversation_id,case_id,resource_security_labels',
    'outbox_jobs': 'tenant_id,id,case_id,action_draft_id,source_message_id,reply_email_id,email_message_id,resource_security_labels',
    'agent_runs': 'tenant_id,id,conversation_id,result_draft_id,resource_security_labels',
    'ai_generations': 'tenant_id,id,conversation_id,resource_security_labels',
    'staff_translation_drafts': 'tenant_id,id,case_id,conversation_id,resource_security_labels',
    'case_history': 'tenant_id,id,case_id,resource_security_labels',
    'action_consents': 'tenant_id,id,case_id,draft_id,resource_security_labels',
    'audit_events': 'tenant_id,id,case_id,action_draft_id,resource_security_labels',
    'idempotency_records': 'tenant_id,id,result_case_id,draft_id,resource_security_labels',
    'conversation_events': 'tenant_id,conversation_id,sequence,case_id,message_id,resource_security_labels',
    'chat_transitions': 'tenant_id,id,case_id,conversation_id,resource_security_labels',
    'attachments': 'tenant_id,id,case_id,draft_id,resource_security_labels',
    'email_reply_state': 'tenant_id,email_id,case_id,app_message_id,resource_security_labels',
    'email_reply_challenges': 'tenant_id,id,case_id,email_id,resource_security_labels',
    'email_messages': 'tenant_id,id,case_id,job_id,app_message_id,resource_security_labels',
    'tenants':'id,active',
    'principals':'tenant_id,id,active',
    'case_participants':'tenant_id,case_id,principal_id,revoked_at',
}


def patch_function(schema, signature, replacements):
    definition = op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',
                                              (schema + '.' + signature,)).scalar_one()
    for old, new, count in replacements:
        if definition.count(old) != count:
            raise RuntimeError('Unexpected reviewed function definition: ' + signature)
        definition = definition.replace(old, new)
    # Database-owned definitions can contain literal colon-prefixed values
    # (e.g. ':mail'). They are SQL, not SQLAlchemy bind templates.
    op.get_bind().exec_driver_sql(definition,execution_options={'no_parameters':True})


def upgrade():
    a = context.config.attributes
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    schema = a['schema']; q = quote(schema); r = quote(a['runtime_role'])
    name = 'source_graph_' + hashlib.sha256(schema.encode()).hexdigest()[:24]
    prepare_auth_owner(op.get_bind(), name)
    owner = quote(name)
    op.execute(f'GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}')
    for signature in ('source_labels_union(jsonb[])', 'source_labels_valid(jsonb)',
                      'source_labels_contains(jsonb,jsonb)', 'source_evidence_requirements(jsonb)'):
        op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {owner}')
    op.execute(f'''ALTER TABLE {q}.chat_transitions ADD COLUMN resource_security_labels jsonb NOT NULL DEFAULT '[]',
      ADD CONSTRAINT private_labels_canonical CHECK({q}.source_labels_valid(resource_security_labels))''')
    op.execute(f'''CREATE TRIGGER private_labels_monotone BEFORE UPDATE OF resource_security_labels ON {q}.chat_transitions
      FOR EACH ROW EXECUTE FUNCTION {q}.guard_source_labels_monotone()''')
    for table, columns in READS.items():
        op.execute(f'GRANT SELECT({columns}) ON {q}.{table} TO {owner}')
        op.execute(f'CREATE POLICY source_graph_metadata ON {q}.{table} FOR SELECT TO {owner} USING(true)')
    op.execute(f'GRANT UPDATE(resource_security_labels) ON {q}.conversations TO {owner}')
    op.execute(f'CREATE POLICY source_graph_promotion ON {q}.conversations FOR UPDATE TO {owner} USING(true) WITH CHECK(true)')
    _graph(q, owner)
    # Exactly these storage constraints are superseded by canonical + actual
    # graph guards. Attachments/email bodies/challenge capabilities stay closed.
    for table in ('conversations', 'messages', 'action_drafts', 'cases', 'outbox_jobs'):
        op.execute(f'ALTER TABLE {q}.{table} DROP CONSTRAINT {table}_resource_security_labels_check')
    for table in ('agent_runs', 'ai_generations', 'staff_translation_drafts', 'case_history',
                  'action_consents', 'audit_events', 'idempotency_records', 'conversation_events'):
        op.execute(f'ALTER TABLE {q}.{table} DROP CONSTRAINT private_labels_hold')
    op.execute(f'ALTER TABLE {q}.cases DROP CONSTRAINT cases_source_observations_check')
    op.execute(f'ALTER TABLE {q}.action_drafts DROP CONSTRAINT private_evidence_hold')
    for table in ('messages', 'action_drafts', 'cases', 'outbox_jobs', 'agent_runs',
                  'ai_generations', 'staff_translation_drafts'):
        op.execute(f'GRANT INSERT(resource_security_labels) ON {q}.{table} TO {r}')
    op.execute(f'GRANT INSERT(source_observations) ON {q}.action_drafts,{q}.cases TO {r}')
    op.execute(f'GRANT INSERT(source_context_hash) ON {q}.staff_translation_drafts TO {r}')
    op.execute(f'GRANT UPDATE(resource_security_labels) ON {q}.agent_runs TO {r}')
    _policies(q, r)
    _recipient(q, owner, r)
    _agent(q, r)
    patch_function(schema, 'translation_guard()', [
        ("OR c.resource_security_labels<>'[]'::jsonb OR v.resource_security_labels<>'[]'::jsonb", '', 1),
        ("IF NEW.state<>'failed' THEN", f"""IF NEW.resource_security_labels<>'[]'::jsonb AND NEW.source_context_hash IS NULL
          THEN RAISE EXCEPTION 'Private translation context required' USING ERRCODE='23514';END IF;
          IF NEW.state<>'failed' THEN""", 1)])
    _legacy_mail(q, schema, owner)
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def _graph(q, owner):
    # Deferred validation always uses the final persisted metadata, including
    # relationship IDs. Earlier trigger images may predate a permitted relink.
    reloads=[]
    for table in TABLES:
        keys=('conversation_id','sequence') if table=='conversation_events' else ('email_id',) if table=='email_reply_state' else ('id',)
        predicate=' AND '.join(f"m.{key}=(to_jsonb(NEW)->>'{key}')::{('bigint' if key=='sequence' else 'uuid')}" for key in keys)
        reloads.append(f"WHEN TG_TABLE_NAME='{table}' THEN SELECT to_jsonb(m) INTO rowdata FROM (SELECT {READS[table]} FROM {q}.{table}) m WHERE m.tenant_id=NEW.tenant_id AND {predicate};")
    reload_sql=' '.join(reloads)
    op.execute(f'''CREATE FUNCTION {q}.inherit_private_source_graph() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE rowdata jsonb;requirements jsonb;parent jsonb;cid uuid;vid uuid;did uuid;mid uuid;linked uuid;BEGIN
        rowdata:=to_jsonb(NEW);requirements:=NEW.resource_security_labels;
        IF NOT {q}.source_labels_valid(requirements) THEN RAISE EXCEPTION 'Invalid source classification' USING ERRCODE='23514';END IF;
        cid:=COALESCE((rowdata->>'case_id')::uuid,(rowdata->>'result_case_id')::uuid);
        IF TG_TABLE_NAME='cases' THEN cid:=NULL;END IF;
        IF cid IS NOT NULL THEN
          SELECT resource_security_labels INTO parent FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=cid;
          IF NOT FOUND THEN RAISE EXCEPTION 'Source parent unavailable' USING ERRCODE='23514';END IF;
          requirements:={q}.source_labels_union(ARRAY[requirements,parent]);
        END IF;
        vid:=COALESCE((rowdata->>'conversation_id')::uuid,(rowdata->>'origin_conversation_id')::uuid);
        IF TG_TABLE_NAME='conversations' THEN vid:=NULL;END IF;
        IF vid IS NOT NULL THEN
          SELECT resource_security_labels,case_id INTO parent,linked FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=vid;
          IF NOT FOUND THEN RAISE EXCEPTION 'Source conversation unavailable' USING ERRCODE='23514';END IF;
          requirements:={q}.source_labels_union(ARRAY[requirements,parent]);
          IF linked IS NOT NULL THEN
            SELECT resource_security_labels INTO parent FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=linked;
            IF NOT FOUND THEN RAISE EXCEPTION 'Source case unavailable' USING ERRCODE='23514';END IF;
            requirements:={q}.source_labels_union(ARRAY[requirements,parent]);
          END IF;
        END IF;
        did:=COALESCE((rowdata->>'action_draft_id')::uuid,(rowdata->>'draft_id')::uuid,(rowdata->>'origin_draft_id')::uuid,(rowdata->>'result_draft_id')::uuid);
        IF did IS NOT NULL THEN
          SELECT resource_security_labels INTO parent FROM {q}.action_drafts WHERE tenant_id=NEW.tenant_id AND id=did;
          IF NOT FOUND THEN RAISE EXCEPTION 'Source draft unavailable' USING ERRCODE='23514';END IF;
          requirements:={q}.source_labels_union(ARRAY[requirements,parent]);
        END IF;
        mid:=COALESCE((rowdata->>'source_message_id')::uuid,(rowdata->>'message_id')::uuid,(rowdata->>'app_message_id')::uuid);
        IF mid IS NOT NULL AND TG_TABLE_NAME NOT IN('messages','cases') THEN
          SELECT resource_security_labels INTO parent FROM {q}.messages WHERE tenant_id=NEW.tenant_id AND id=mid;
          IF NOT FOUND THEN RAISE EXCEPTION 'Source message unavailable' USING ERRCODE='23514';END IF;
          requirements:={q}.source_labels_union(ARRAY[requirements,parent]);
        END IF;
        IF TG_TABLE_NAME IN('action_drafts','cases') THEN
          requirements:={q}.source_labels_union(ARRAY[requirements,{q}.source_evidence_requirements(NEW.source_observations)]);
        END IF;
        IF requirements<>'[]'::jsonb AND
          ((TG_TABLE_NAME='conversations' AND rowdata->>'customer_principal_id' IS NULL)
          OR(TG_TABLE_NAME='action_drafts' AND rowdata->>'actor_principal_id' IS NULL)
          OR(TG_TABLE_NAME='cases' AND rowdata->>'owner_principal_id' IS NULL)
          OR(TG_TABLE_NAME IN('agent_runs','ai_generations') AND rowdata->>'principal_id' IS NULL))
        THEN RAISE EXCEPTION 'Private source requires customer identity' USING ERRCODE='23514';END IF;
        IF vid IS NOT NULL THEN
          IF linked IS NOT NULL THEN
            -- Case source/consent is immutable. New case facts require the
            -- explicit additional-information feature, never silent promotion.
            SELECT resource_security_labels INTO parent FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=linked;
            IF NOT {q}.source_labels_contains(parent,requirements) THEN RAISE EXCEPTION 'Case source context changed' USING ERRCODE='23514';END IF;
          END IF;
          UPDATE {q}.conversations SET resource_security_labels={q}.source_labels_union(ARRAY[resource_security_labels,requirements])
            WHERE tenant_id=NEW.tenant_id AND id=vid AND NOT {q}.source_labels_contains(resource_security_labels,requirements);
        END IF;
        NEW.resource_security_labels:=requirements;
        RETURN NEW;
      END $$''')
    op.execute(f'''CREATE FUNCTION {q}.validate_private_source_graph() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE rowdata jsonb;actual jsonb;parent jsonb;cid uuid;vid uuid;did uuid;snapshot jsonb;BEGIN
        CASE {reload_sql} ELSE RAISE EXCEPTION 'Unsupported private graph table' USING ERRCODE='23514';END CASE;
        actual:=rowdata->'resource_security_labels';
        IF actual IS NULL THEN RAISE EXCEPTION 'Private graph row unavailable' USING ERRCODE='23514';END IF;
        cid:=COALESCE((rowdata->>'case_id')::uuid,(rowdata->>'result_case_id')::uuid);
        IF TG_TABLE_NAME='cases' THEN cid:=NULL;END IF;
        vid:=COALESCE((rowdata->>'conversation_id')::uuid,(rowdata->>'origin_conversation_id')::uuid);
        IF TG_TABLE_NAME='conversations' THEN vid:=NULL;END IF;
        IF cid IS NOT NULL THEN
          SELECT resource_security_labels INTO parent FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=cid;
          IF parent IS NULL OR NOT {q}.source_labels_contains(parent,actual) THEN RAISE EXCEPTION 'Private child exceeds case aggregate' USING ERRCODE='23514';END IF;
        END IF;
        IF vid IS NOT NULL THEN
          SELECT resource_security_labels INTO parent FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=vid;
          IF parent IS NULL OR NOT {q}.source_labels_contains(parent,actual) THEN RAISE EXCEPTION 'Private child exceeds conversation aggregate' USING ERRCODE='23514';END IF;
        END IF;
        IF TG_TABLE_NAME='cases' THEN
          SELECT resource_security_labels,source_observations INTO parent,snapshot FROM {q}.action_drafts
            WHERE tenant_id=NEW.tenant_id AND id=(rowdata->>'origin_draft_id')::uuid;
          IF actual IS DISTINCT FROM parent OR rowdata->'source_observations' IS DISTINCT FROM snapshot
          THEN RAISE EXCEPTION 'Case source does not match reviewed draft' USING ERRCODE='23514';END IF;
        END IF;
        RETURN NULL;
      END $$''')
    for function in ('inherit_private_source_graph', 'validate_private_source_graph'):
        op.execute(f'REVOKE ALL ON FUNCTION {q}.{function}() FROM PUBLIC')
        op.execute(f'ALTER FUNCTION {q}.{function}() OWNER TO {owner}')
    for table in TABLES:
        # Historical draft/consent/classification stays immutable on UPDATE.
        # INSERT inherits the current aggregate; other writes only grow labels.
        events = 'INSERT' if table in ('action_drafts','action_consents','audit_events','idempotency_records') else 'INSERT OR UPDATE'
        op.execute(f'''CREATE TRIGGER a_private_source_inherit BEFORE {events} ON {q}.{table}
          FOR EACH ROW EXECUTE FUNCTION {q}.inherit_private_source_graph()''')
        op.execute(f'''CREATE CONSTRAINT TRIGGER private_source_graph AFTER INSERT OR UPDATE ON {q}.{table}
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_private_source_graph()''')


def _policies(q, runtime):
    customer = f"{q}.current_actor_kind()='customer' AND customer_principal_id={q}.current_principal_id()"
    anon = f"{q}.current_actor_kind()='anonymous' AND anonymous_session_id={q}.current_session_id() AND resource_security_labels='[]'::jsonb"
    own = f'''tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid() AND(({customer}) OR({anon}))
      AND(case_id IS NULL OR EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=conversations.tenant_id AND c.id=conversations.case_id))'''
    for action in ('select','insert','update'):
        condition = own + (" AND case_id IS NULL AND mode='ai'" if action=='insert' else '')
        op.execute(f'ALTER POLICY runtime_{action} ON {q}.conversations '+
                   (f'USING({condition}) ' if action!='insert' else '')+(f'WITH CHECK({condition})' if action!='select' else ''))
    event_owner = f'tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid() AND(({customer}) OR({anon}))'
    op.execute(f'ALTER POLICY event_owner_read ON {q}.conversations USING({event_owner})')
    op.execute(f'ALTER POLICY event_owner_write ON {q}.conversations USING({event_owner}) WITH CHECK({event_owner})')
    staff = f'''tenant_id={q}.current_tenant_id() AND case_id IS NOT NULL AND EXISTS(SELECT 1 FROM {q}.cases c
      WHERE c.tenant_id=conversations.tenant_id AND c.id=conversations.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false))'''
    op.execute(f'ALTER POLICY chat_staff_read ON {q}.conversations USING({staff})')
    op.execute(f'ALTER POLICY chat_staff_update ON {q}.conversations USING({staff}) WITH CHECK({staff})')
    op.execute(f'ALTER POLICY event_owner_staff_read ON {q}.conversations USING({staff})')
    op.execute(f'ALTER POLICY event_owner_staff_write ON {q}.conversations USING({staff}) WITH CHECK({staff})')
    draft = f'''tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid() AND created_session_id={q}.current_session_id()
      AND(({q}.current_actor_kind()='customer' AND actor_principal_id={q}.current_principal_id() AND actor_session_id IS NULL)
        OR({q}.current_actor_kind()='anonymous' AND actor_session_id={q}.current_session_id() AND actor_principal_id IS NULL AND resource_security_labels='[]'::jsonb))
      AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=action_drafts.tenant_id AND c.id=action_drafts.conversation_id)
      AND(case_id IS NULL OR EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=action_drafts.tenant_id AND c.id=action_drafts.case_id))'''
    for action in ('select','insert','update'):
        op.execute(f'ALTER POLICY runtime_{action} ON {q}.action_drafts '+(f'USING({draft}) ' if action!='insert' else '')+(f'WITH CHECK({draft})' if action!='select' else ''))
    case = f'''tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid()
      AND(({q}.current_actor_kind()='customer' AND owner_principal_id={q}.current_principal_id())
      OR({q}.current_actor_kind()='anonymous' AND owner_anonymous_session_id={q}.current_session_id() AND resource_security_labels='[]'::jsonb AND source_observations='[]'::jsonb))'''
    op.execute(f'ALTER POLICY runtime_read ON {q}.cases USING({case})')
    insert = case + f''' AND created_session_id={q}.current_session_id() AND EXISTS(SELECT 1 FROM {q}.action_drafts d
      WHERE d.tenant_id=cases.tenant_id AND d.id=cases.origin_draft_id AND d.created_session_id={q}.current_session_id() AND d.state='pending')'''
    op.execute(f'ALTER POLICY runtime_insert ON {q}.cases WITH CHECK({insert})')
    staff = f'tenant_id={q}.current_tenant_id() AND {q}.case_staff_scope(organization_id,queue_id,false)'
    op.execute(f'ALTER POLICY lifecycle_staff_read ON {q}.cases USING({staff})')
    update = f"({staff}) OR(tenant_id={q}.current_tenant_id() AND {q}.current_actor_kind()='customer' AND {q}.conversation_session_valid() AND owner_principal_id={q}.current_principal_id())"
    op.execute(f'ALTER POLICY lifecycle_update ON {q}.cases USING({update}) WITH CHECK({update})')
    # Adoption/anonymous lock policies remain empty-only; they cannot acquire
    # source grants by rotating an anonymous identity into a customer session.


def _recipient(q, owner, runtime):
    # Staff cannot SELECT another customer's principal row through self RLS.
    # Expose only recipient eligibility, under current handler/queue/session ACL.
    for signature in ('current_tenant_id()','current_principal_id()',
                      'case_staff_scope(uuid,uuid,boolean)','case_target_membership(uuid,uuid,uuid)'):
        op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {owner}')
    op.execute(f'''CREATE FUNCTION {q}.source_chat_recipient(case_uuid uuid,conversation_uuid uuid) RETURNS jsonb
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        SELECT jsonb_build_object('tenant_id',c.tenant_id,'principal_id',v.customer_principal_id,
          'tenant_active',t.active,'principal_active',p.active,'participant_active',cp.principal_id IS NOT NULL AND cp.revoked_at IS NULL)
        FROM {q}.cases c JOIN {q}.conversations v ON(v.tenant_id,v.case_id)=(c.tenant_id,c.id)
        JOIN {q}.tenants t ON t.id=c.tenant_id
        LEFT JOIN {q}.principals p ON(p.tenant_id,p.id)=(v.tenant_id,v.customer_principal_id)
        LEFT JOIN {q}.case_participants cp ON(cp.tenant_id,cp.case_id,cp.principal_id)=(c.tenant_id,c.id,v.customer_principal_id)
        WHERE c.tenant_id={q}.current_tenant_id() AND c.id=case_uuid AND v.id=conversation_uuid
          AND {q}.case_staff_scope(c.organization_id,c.queue_id,false) AND v.mode='human'
          AND c.assigned_membership_id=v.handler_membership_id
          AND({q}.case_target_membership(v.handler_membership_id,c.organization_id,c.queue_id)->>'principal_id')::uuid={q}.current_principal_id()
      $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.source_chat_recipient(uuid,uuid) FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.source_chat_recipient(uuid,uuid) OWNER TO {owner}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.source_chat_recipient(uuid,uuid) TO {runtime}')


def _agent(q, runtime):
    # Replace only the explicitly identified proposal CHECK, preserving all
    # mode, phase, budget, lease, immutable input and receipt constraints.
    checks = op.get_bind().exec_driver_sql('SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype=\'c\'',
                                         (context.config.attributes['schema']+'.agent_runs',)).all()
    proposal = [name for name, value in checks if 'octet_length' in value and 'proposal' in value]
    if len(proposal)!=1:raise RuntimeError('Unexpected agent proposal constraint.')
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    op.execute(f'ALTER TABLE {q}.agent_runs DROP CONSTRAINT {quote(proposal[0])}')
    op.execute(f'''ALTER TABLE {q}.agent_runs ADD CONSTRAINT agent_proposal_shape CHECK(proposal IS NULL OR(
      jsonb_typeof(proposal)='object' AND octet_length(proposal::text)<=2000 AND(
      (proposal ?& ARRAY['category','service_type','summary'] AND proposal-ARRAY['category','service_type','summary']='{{}}'::jsonb)
      OR(proposal ?& ARRAY['draft','source_fact_hash'] AND proposal-ARRAY['draft','source_fact_hash']='{{}}'::jsonb
        AND jsonb_typeof(proposal->'draft')='object' AND(proposal->'draft') ?& ARRAY['category','service_type','summary']
        AND(proposal->'draft')-ARRAY['category','service_type','summary']='{{}}'::jsonb
        AND jsonb_typeof(proposal->'source_fact_hash')='string' AND(proposal->>'source_fact_hash')~'^[a-f0-9]{{64}}$'
        AND resource_security_labels<>'[]'::jsonb))))''')
    gate = f'''tenant_id={q}.current_tenant_id() AND session_id={q}.current_session_id()
      AND principal_id IS NOT DISTINCT FROM {q}.current_principal_id() AND {q}.conversation_session_valid()
      AND(resource_security_labels='[]'::jsonb OR {q}.current_actor_kind()='customer')
      AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=agent_runs.tenant_id AND c.id=agent_runs.conversation_id AND c.case_id IS NULL)'''
    for action in ('select','insert','update'):
        op.execute(f'ALTER POLICY agent_runs_{action} ON {q}.agent_runs '+(f'USING({gate}) ' if action!='insert' else '')+(f'WITH CHECK({gate})' if action!='select' else ''))
    op.execute(f'''CREATE OR REPLACE FUNCTION {q}.agent_run_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE c record;m record;reservation boolean:=false;BEGIN
        SELECT * INTO c FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id FOR UPDATE;
        IF NOT FOUND OR NEW.tenant_id IS DISTINCT FROM {q}.current_tenant_id() OR NEW.session_id IS DISTINCT FROM {q}.current_session_id()
          OR NEW.principal_id IS DISTINCT FROM {q}.current_principal_id() OR NOT {q}.conversation_session_valid()
        THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Agent access denied';END IF;
        IF TG_OP='INSERT' THEN
          SELECT * INTO m FROM {q}.messages WHERE tenant_id=NEW.tenant_id AND id=NEW.message_id AND conversation_id=NEW.conversation_id;
          IF NOT FOUND OR m.author_type<>'customer' OR m.sequence<>c.last_sequence
            OR c.mode<>'ai' OR NEW.conversation_version<>c.version OR NEW.generation_epoch<>c.generation_epoch OR NEW.language<>c.language
            OR NEW.phase<>'ready' OR NEW.revision<>1 OR NEW.model_calls<>0 OR NEW.read_calls<>0 OR NEW.trace<>'[]'::jsonb
            OR NEW.pending_call IS NOT NULL OR NEW.proposal IS NOT NULL OR NEW.result_draft_id IS NOT NULL
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid agent run';END IF;
        ELSE
          reservation:=OLD.phase IN('planning','reading','draft','waiting_user') AND NEW.phase=OLD.phase
            AND NEW.read_calls=OLD.read_calls+1 AND NEW.model_calls=OLD.model_calls
            AND(to_jsonb(NEW)-ARRAY['revision','read_calls','updated_at','resource_security_labels'])
              =(to_jsonb(OLD)-ARRAY['revision','read_calls','updated_at','resource_security_labels']);
          IF(to_jsonb(NEW)-ARRAY['phase','revision','model_calls','read_calls','trace','pending_call','proposal','lease','lease_until','result_draft_id','reason','updated_at','resource_security_labels'])
            IS DISTINCT FROM(to_jsonb(OLD)-ARRAY['phase','revision','model_calls','read_calls','trace','pending_call','proposal','lease','lease_until','result_draft_id','reason','updated_at','resource_security_labels'])
            OR NEW.revision<>OLD.revision+1 OR NEW.model_calls<OLD.model_calls OR NEW.read_calls<OLD.read_calls
            OR NEW.model_calls>OLD.model_calls+1 OR NEW.read_calls>OLD.read_calls+1
            OR OLD.phase IN('done','review','superseded') OR(OLD.phase='waiting_user' AND NOT reservation)
            OR NOT(reservation OR(OLD.phase='ready' AND NEW.phase IN('planning','review','superseded'))
              OR(OLD.phase='planning' AND NEW.phase IN('tool','draft','waiting_user','review','superseded'))
              OR(OLD.phase='tool' AND NEW.phase IN('reading','review','superseded'))
              OR(OLD.phase='reading' AND NEW.phase IN('ready','tool','review','superseded'))
              OR(OLD.phase='draft' AND NEW.phase IN('done','review','superseded')))
            OR(NEW.phase<>'superseded' AND(c.mode<>'ai' OR c.version<>NEW.conversation_version
              OR c.generation_epoch<>NEW.generation_epoch OR c.language<>NEW.language))
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid agent transition';END IF;
        END IF;
        IF NEW.result_draft_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM {q}.action_drafts d
          WHERE d.tenant_id=NEW.tenant_id AND d.id=NEW.result_draft_id AND d.conversation_id=NEW.conversation_id
          AND d.created_session_id=NEW.session_id AND d.state='pending'
          AND {q}.source_labels_contains(NEW.resource_security_labels,d.resource_security_labels))
        THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid draft receipt';END IF;
        NEW.updated_at:=clock_timestamp();RETURN NEW;END $$''')


def _legacy_mail(q, schema, owner):
    op.execute(f'''CREATE FUNCTION {q}.legacy_source_job_public(t uuid,jid uuid) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE j record;labels jsonb;BEGIN
        SELECT * INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid;
        IF NOT FOUND THEN RETURN false;END IF;
        IF j.resource_security_labels<>'[]'::jsonb THEN RETURN false;END IF;
        IF j.case_id IS NOT NULL AND EXISTS(SELECT 1 FROM {q}.cases c JOIN {q}.action_drafts d ON(d.tenant_id,d.id)=(c.tenant_id,c.origin_draft_id)
          JOIN {q}.conversations v ON(v.tenant_id,v.id)=(c.tenant_id,c.origin_conversation_id)
          WHERE c.tenant_id=t AND c.id=j.case_id AND(c.resource_security_labels<>'[]'::jsonb OR d.resource_security_labels<>'[]'::jsonb OR v.resource_security_labels<>'[]'::jsonb))
        THEN RETURN false;END IF;
        IF EXISTS(SELECT 1 FROM {q}.messages m WHERE m.tenant_id=t AND m.id=j.source_message_id AND m.resource_security_labels<>'[]'::jsonb)
          OR EXISTS(SELECT 1 FROM {q}.email_messages e WHERE e.tenant_id=t AND(e.id=j.reply_email_id OR e.id=j.email_message_id OR e.job_id=j.id) AND e.resource_security_labels<>'[]'::jsonb)
          OR EXISTS(SELECT 1 FROM {q}.email_reply_state r WHERE r.tenant_id=t AND r.email_id=j.reply_email_id AND r.resource_security_labels<>'[]'::jsonb)
        THEN RETURN false;END IF;
        RETURN true;END $$''')
    # The helper needs only job fields explicitly granted in READS, not body or
    # transport data. Do not use SELECT * in an owner with metadata-only grants.
    patch_function(schema, 'legacy_source_job_public(uuid,uuid)', [
        ('SELECT * INTO j', 'SELECT tenant_id,id,case_id,source_message_id,reply_email_id,email_message_id,resource_security_labels INTO j', 1)])
    suffix=hashlib.sha256(schema.encode()).hexdigest()[:24]
    mail='airport_assistant_mail_owner' if schema=='app' else 'mail_owner_'+suffix
    reply='airport_assistant_reply_owner' if schema=='app' else 'reply_owner_'+suffix
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    op.execute(f'REVOKE ALL ON FUNCTION {q}.legacy_source_job_public(uuid,uuid) FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.legacy_source_job_public(uuid,uuid) OWNER TO {owner}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.legacy_source_job_public(uuid,uuid) TO {quote(mail)},{quote(reply)}')
    signatures = {
        'mail_snapshot(uuid,uuid,uuid,uuid,bigint)': 'NULL',
        'mail_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)': 'false',
        'mail_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)': 'NULL',
        'email_reply_snapshot(uuid,uuid,uuid,uuid,bigint)': 'NULL',
        'email_reply_apply(uuid,uuid,uuid,uuid,bigint,text,text,uuid,text)': 'false',
        'email_challenge_snapshot(uuid,uuid,uuid,uuid,bigint)': 'NULL',
        'email_challenge_reserve(uuid,uuid,uuid,uuid,bigint)': 'false',
    }
    for signature, denied in signatures.items():
        definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.'+signature,)).scalar_one()
        start=definition.index('BEGIN')+len('BEGIN')
        op.get_bind().exec_driver_sql(definition[:start]+f' IF NOT {q}.legacy_source_job_public(t,jid) THEN RETURN {denied};END IF; '+definition[start:],execution_options={'no_parameters':True})


def downgrade():
    raise RuntimeError('Private graph recovery requires a reviewed forward migration.')
