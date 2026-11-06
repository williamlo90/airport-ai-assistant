"""Account-bound email reply proofs, private challenges and case-only messages."""
import hashlib
from alembic import context,op
from airport_assistant.infrastructure.database.provisioning import prepare_auth_owner

revision='0018_email_replies'
down_revision='0017_email_inbox'
branch_labels=None
depends_on=None


def execute(statement):
    op.get_bind().exec_driver_sql(statement.replace("%","%%"))


def upgrade():
    a=context.config.attributes;s=a['schema'];quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    name='airport_assistant_reply_owner' if s=='app' else 'reply_owner_'+hashlib.sha256(s.encode()).hexdigest()[:24]
    prepare_auth_owner(op.get_bind(),name)
    q,owner,mail,worker,runtime=(quote(v) for v in (s,name,'airport_assistant_mail_owner' if s=='app' else 'mail_owner_'+hashlib.sha256(s.encode()).hexdigest()[:24],a['worker_role'],a['runtime_role']))
    execute(f"ALTER TABLE {q}.email_messages ADD COLUMN reply_token_hash text CHECK(reply_token_hash ~ '^[a-f0-9]{{64}}$'),ADD COLUMN inbound_to text,ADD COLUMN verification_challenge_id uuid,ADD COLUMN verification_response_hash text CHECK(verification_response_hash ~ '^[a-f0-9]{{64}}$')")
    execute(f'CREATE UNIQUE INDEX reply_token_unique ON {q}.email_messages(email_account_id,reply_token_hash) WHERE reply_token_hash IS NOT NULL')
    execute(f"""CREATE TABLE {q}.email_reply_state(tenant_id uuid NOT NULL,email_id uuid NOT NULL,case_id uuid,principal_id uuid,
      state text NOT NULL DEFAULT 'pending' CHECK(state IN('pending','publishing','published','review','rejected','challenged','verification_reply')),
      reason text,pending_text text CHECK(length(pending_text)<=4000),proof text CHECK(proof IN('authenticated_email','challenge')),app_message_id uuid,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,email_id),
      FOREIGN KEY(tenant_id,email_id) REFERENCES {q}.email_messages(tenant_id,id),FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
      FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,app_message_id) REFERENCES {q}.messages(tenant_id,id),
      CHECK((state='published')=(app_message_id IS NOT NULL)))""")
    execute(f"""CREATE TABLE {q}.email_reply_challenges(tenant_id uuid NOT NULL,id uuid NOT NULL,email_id uuid NOT NULL,case_id uuid NOT NULL,
      email_account_id uuid NOT NULL,principal_id uuid NOT NULL,contact_id uuid NOT NULL,recipient text NOT NULL,code_hash text NOT NULL CHECK(code_hash ~ '^[a-f0-9]{{64}}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '5 minutes',attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
      state text NOT NULL DEFAULT 'pending' CHECK(state IN('pending','consumed','failed')),send_state text NOT NULL DEFAULT 'queued' CHECK(send_state IN('queued','reserved','accepted','failed','delivery_unknown')),
      provider_message_id text,PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,email_id),FOREIGN KEY(tenant_id,email_id) REFERENCES {q}.email_messages(tenant_id,id),
      FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),FOREIGN KEY(tenant_id,email_account_id) REFERENCES {q}.email_accounts(tenant_id,id),
      FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,contact_id) REFERENCES {q}.contacts(tenant_id,id))""")
    for table in ('email_reply_state','email_reply_challenges'):
        execute(f'ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY');execute(f'ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY');execute(f'REVOKE ALL ON {q}.{table} FROM PUBLIC')
        execute(f'GRANT SELECT,INSERT,UPDATE ON {q}.{table} TO {owner}')
        execute(f'CREATE POLICY reply_owner_all ON {q}.{table} TO {owner} USING(true) WITH CHECK(true)')
    execute(f'GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}')
    for table in ('tenants','email_accounts','email_messages','cases','case_participants','contacts','principals','messages','case_history','conversations','action_drafts'):
        execute(f'GRANT SELECT ON {q}.{table} TO {owner}');execute(f'CREATE POLICY reply_read ON {q}.{table} FOR SELECT TO {owner} USING(true)')
    for table in ('tenants','email_accounts','case_participants','contacts','principals'):
        execute(f'GRANT UPDATE(id) ON {q}.{table} TO {owner}');execute(f'CREATE POLICY reply_lock ON {q}.{table} FOR UPDATE TO {owner} USING(true) WITH CHECK(false)')
    execute(f'GRANT SELECT ON {q}.outbox_jobs TO {owner}')
    execute(f'CREATE POLICY reply_job_read ON {q}.outbox_jobs FOR SELECT TO {owner} USING(true)')
    execute(f'GRANT UPDATE(lease_generation) ON {q}.outbox_jobs TO {owner}')
    execute(f'CREATE POLICY reply_job_lock ON {q}.outbox_jobs FOR UPDATE TO {owner} USING(true) WITH CHECK(false)')
    execute(f'GRANT UPDATE(case_id,app_message_id,recipient_contact_id,recipient_principal_id,verification_state) ON {q}.email_messages TO {owner}')
    execute(f'CREATE POLICY reply_email_update ON {q}.email_messages FOR UPDATE TO {owner} USING(true) WITH CHECK(true)')
    execute(f'GRANT UPDATE(status,assigned_membership_id,next_actor,next_action,close_reason,resolution,duplicate_of_case_id,followup_evidence,closed_at,updated_at,version) ON {q}.cases TO {owner}')
    execute(f'CREATE POLICY reply_case_update ON {q}.cases FOR UPDATE TO {owner} USING(true) WITH CHECK(true)')
    execute(f'GRANT INSERT ON {q}.messages,{q}.case_history TO {owner}')
    execute(f'CREATE POLICY reply_message_insert ON {q}.messages FOR INSERT TO {owner} WITH CHECK(true)')
    execute(f'CREATE POLICY reply_history_insert ON {q}.case_history FOR INSERT TO {owner} WITH CHECK(true)')
    execute(f'GRANT EXECUTE ON FUNCTION {q}.case_member_metadata(uuid,uuid,uuid) TO {owner}')
    execute(f"ALTER TABLE {q}.outbox_jobs DROP CONSTRAINT job_kind,DROP CONSTRAINT ordinary_job_shape,DROP CONSTRAINT job_payload,ADD COLUMN reply_email_id uuid,ADD COLUMN reply_challenge_id uuid,ADD COLUMN reply_account_id uuid,ADD FOREIGN KEY(tenant_id,reply_account_id) REFERENCES {q}.email_accounts(tenant_id,id),ADD FOREIGN KEY(tenant_id,reply_email_id) REFERENCES {q}.email_messages(tenant_id,id),ADD FOREIGN KEY(tenant_id,reply_challenge_id) REFERENCES {q}.email_reply_challenges(tenant_id,id)")
    execute(f"""ALTER TABLE {q}.outbox_jobs ADD CONSTRAINT job_kind CHECK(kind IN('case_created','email_send','email_reconcile','email_fetch','email_event','email_reply','email_verify_send')),
      ADD CONSTRAINT ordinary_job_shape CHECK((case_id IS NULL)=(action_draft_id IS NULL) AND (kind IN('email_fetch','email_event','email_reply') OR case_id IS NOT NULL) AND (kind NOT IN('email_fetch','email_reply') OR case_id IS NULL) AND (kind<>'email_event' OR (email_message_id IS NULL)=(case_id IS NULL))),
      ADD CONSTRAINT reply_job_shape CHECK((kind IN('email_reply','email_verify_send'))=(reply_email_id IS NOT NULL) AND (reply_email_id IS NULL)=(reply_account_id IS NULL) AND (kind='email_verify_send')=(reply_challenge_id IS NOT NULL)),
      ADD CONSTRAINT job_payload CHECK(payload=CASE WHEN kind IN('email_fetch','email_event') THEN jsonb_build_object('inbox_id',email_inbox_id::text) WHEN kind='email_reply' THEN jsonb_build_object('reply_id',reply_email_id::text) WHEN kind='email_verify_send' THEN jsonb_build_object('challenge_id',reply_challenge_id::text) ELSE jsonb_build_object('case_id',case_id::text,'draft_id',action_draft_id::text) END)""")
    execute(f"CREATE UNIQUE INDEX reply_job_once ON {q}.outbox_jobs(tenant_id,reply_email_id) WHERE kind='email_reply'")
    execute(f"CREATE UNIQUE INDEX challenge_job_once ON {q}.outbox_jobs(tenant_id,reply_challenge_id) WHERE kind='email_verify_send'")
    execute(f'GRANT INSERT(tenant_id,case_id,action_draft_id,kind,reply_email_id,reply_challenge_id,reply_account_id,dedupe_key,payload,replay_policy) ON {q}.outbox_jobs TO {owner}')
    execute(f"CREATE POLICY reply_job_insert ON {q}.outbox_jobs FOR INSERT TO {owner} WITH CHECK(kind IN('email_reply','email_verify_send'))")
    execute(f"""CREATE FUNCTION {q}.queue_email_reply() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.direction='inbound' THEN INSERT INTO {q}.email_reply_state(tenant_id,email_id) VALUES(NEW.tenant_id,NEW.id);
        INSERT INTO {q}.outbox_jobs(tenant_id,kind,reply_email_id,reply_account_id,dedupe_key,payload,replay_policy) VALUES(NEW.tenant_id,'email_reply',NEW.id,NEW.email_account_id,'reply:'||NEW.id::text,jsonb_build_object('reply_id',NEW.id::text),'safe');END IF;RETURN NEW;END $$""")
    execute(f'REVOKE ALL ON FUNCTION {q}.queue_email_reply() FROM PUBLIC');execute(f'ALTER FUNCTION {q}.queue_email_reply() OWNER TO {owner}')
    execute(f'CREATE TRIGGER queue_email_reply AFTER INSERT ON {q}.email_messages FOR EACH ROW EXECUTE FUNCTION {q}.queue_email_reply()')
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.job_claim(uuid,text[],integer)',)).scalar_one()
    if definition.count('route.active AND')!=2:raise RuntimeError('Unexpected account routing definition.')
    execute(definition.replace('route.active AND','route.active AND (x.reply_account_id IS NULL OR x.reply_account_id=route.id) AND'))
    # Redacted control responses and the actual delivered mailbox survive staging.
    fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.email_inbox_complete(uuid,uuid,uuid,uuid,bigint,jsonb,text)',)).scalar_one()
    fn=fn.replace('content_digest,attachment_count,inbound_authentication,verification_state','content_digest,attachment_count,inbound_authentication,inbound_to,verification_challenge_id,verification_response_hash,verification_state')
    fn=fn.replace("content->'authentication','pending'", "content->'authentication',content->>'delivered_to',(content->>'verification_challenge_id')::uuid,content->>'verification_response_hash','pending'")
    execute(fn)
    fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.mail_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',)).scalar_one()
    fn=fn.replace("OR envelope->>'reply_to' IS DISTINCT FROM current_snapshot->>'reply_to'", "OR (envelope->>'reply_to' IS DISTINCT FROM current_snapshot->>'reply_to' AND NOT(envelope->>'reply_token_hash' IS NOT NULL AND envelope->>'reply_to' ~ '^[^@]+\\+r-[A-Za-z0-9_-]{32}@[^@]+$' AND split_part(envelope->>'reply_to','+r-',1)=split_part(current_snapshot->>'reply_to','@',1) AND split_part(envelope->>'reply_to','@',2)=split_part(current_snapshot->>'reply_to','@',2) AND encode(sha256(convert_to(split_part(split_part(envelope->>'reply_to','+r-',2),'@',1),'UTF8')),'hex')=envelope->>'reply_token_hash'))")
    fn=fn.replace('requested_rfc_message_id,delivery_key,subject,body)', 'requested_rfc_message_id,delivery_key,subject,body,reply_token_hash)')
    fn=fn.replace("envelope->>'subject',envelope->>'text');", "envelope->>'subject',envelope->>'text',envelope->>'reply_token_hash');")
    execute(fn)
    # Expand inbound shape only when a verified original has an atomic app message.
    execute(f'ALTER TABLE {q}.email_messages DROP CONSTRAINT inbound_email_shape')
    execute(f"ALTER TABLE {q}.email_messages ADD CONSTRAINT inbound_email_shape CHECK(direction<>'inbound' OR (requested_rfc_message_id IS NULL AND delivery_key IS NULL AND delivery_status IS NULL AND completed_at IS NULL AND provider_message_id IS NOT NULL AND content_digest IS NOT NULL AND length(subject)<=998 AND length(body)<=65536 AND length(body_html)<=65536 AND NOT needs_review AND ((verification_state='pending' AND case_id IS NULL AND app_message_id IS NULL AND recipient_contact_id IS NULL AND recipient_principal_id IS NULL) OR (verification_state='verified' AND case_id IS NOT NULL AND app_message_id IS NOT NULL AND recipient_contact_id IS NOT NULL AND recipient_principal_id IS NOT NULL))))")
    shape=op.get_bind().exec_driver_sql("SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint WHERE conrelid=%s::regclass AND conname='message_author_shape'",(s+'.messages',)).scalar_one()
    execute(f"ALTER TABLE {q}.messages DROP CONSTRAINT message_author_shape,DROP CONSTRAINT messages_source_kind_check,DROP CONSTRAINT messages_channel_check,ADD COLUMN email_source_id uuid,ADD FOREIGN KEY(tenant_id,email_source_id) REFERENCES {q}.email_messages(tenant_id,id),ADD CONSTRAINT messages_source_kind_check CHECK(source_kind IN('user_report','staff_reply','ai_response','email_reply')),ADD CONSTRAINT messages_channel_check CHECK(channel IN('web','email')),ADD CONSTRAINT email_message_shape CHECK((source_kind='email_reply')=(email_source_id IS NOT NULL) AND (channel='email')=(source_kind='email_reply')),ADD CONSTRAINT message_author_shape CHECK(({shape}) OR (source_kind='email_reply' AND author_type='customer' AND author_principal_id IS NOT NULL AND author_session_id IS NULL AND conversation_id IS NULL AND case_id IS NOT NULL AND created_session_id IS NULL AND sender_membership_id IS NULL AND staff_request_key IS NULL AND request_hash IS NULL AND generation_id IS NULL AND basis_case_version IS NULL AND basis_conversation_version IS NULL AND basis_epoch IS NULL))")
    execute(f'CREATE UNIQUE INDEX app_email_once ON {q}.messages(tenant_id,email_source_id) WHERE email_source_id IS NOT NULL')
    execute(f"ALTER TABLE {q}.case_history DROP CONSTRAINT case_history_event_type_check,ADD CONSTRAINT case_history_event_type_check CHECK(event_type IN('created','identity_verified','assigned','status_changed','reopened','email_received'))")
    execute(f'ALTER TABLE {q}.case_history ADD COLUMN email_source_id uuid,ADD FOREIGN KEY(tenant_id,email_source_id) REFERENCES {q}.email_messages(tenant_id,id)')
    execute(f'CREATE UNIQUE INDEX email_reopen_once ON {q}.case_history(tenant_id,email_source_id) WHERE email_source_id IS NOT NULL')
    execute(f'GRANT SELECT(tenant_id,email_id,case_id,state,reason,app_message_id,created_at) ON {q}.email_reply_state TO {runtime}')
    execute(f"CREATE POLICY reply_staff_read ON {q}.email_reply_state FOR SELECT TO {runtime} USING(EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=email_reply_state.tenant_id AND c.id=email_reply_state.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false)))")
    execute(f'GRANT SELECT(tenant_id,email_id,expires_at,attempts,state,send_state) ON {q}.email_reply_challenges TO {runtime}')
    execute(f"CREATE POLICY challenge_staff_read ON {q}.email_reply_challenges FOR SELECT TO {runtime} USING(EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=email_reply_challenges.tenant_id AND c.id=email_reply_challenges.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false)))")
    execute(f"""CREATE FUNCTION {q}.review_stopped_reply() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.reply_email_id IS NOT NULL AND NEW.state IN('unknown','dead','blocked') THEN
        UPDATE {q}.email_reply_state SET state='review',reason='WORKER_STOPPED' WHERE tenant_id=NEW.tenant_id AND email_id=NEW.reply_email_id AND state IN('pending','challenged');
      END IF;RETURN NEW;END $$""")
    execute(f'REVOKE ALL ON FUNCTION {q}.review_stopped_reply() FROM PUBLIC')
    execute(f'ALTER FUNCTION {q}.review_stopped_reply() OWNER TO {owner}')
    execute(f'CREATE TRIGGER review_stopped_reply AFTER UPDATE ON {q}.outbox_jobs FOR EACH ROW EXECUTE FUNCTION {q}.review_stopped_reply()')
    _guards(q,owner,name,s)
    _functions(q,owner,worker,s)
    execute(f"INSERT INTO {q}.email_reply_state(tenant_id,email_id) SELECT tenant_id,id FROM {q}.email_messages WHERE direction='inbound' ON CONFLICT DO NOTHING")
    execute(f"INSERT INTO {q}.outbox_jobs(tenant_id,kind,reply_email_id,reply_account_id,dedupe_key,payload,replay_policy) SELECT tenant_id,'email_reply',id,email_account_id,'reply:'||id::text,jsonb_build_object('reply_id',id::text),'safe' FROM {q}.email_messages WHERE direction='inbound' ON CONFLICT(tenant_id,dedupe_key) DO NOTHING")
    execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def _guards(q,owner,name,s):
    literal="'"+name+"'"
    execute(f"""CREATE FUNCTION {q}.guard_email_app_message() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.source_kind='email_reply' AND (current_user<>{literal} OR NOT EXISTS(SELECT 1 FROM {q}.email_reply_state r WHERE r.tenant_id=NEW.tenant_id AND r.email_id=NEW.email_source_id AND r.case_id=NEW.case_id AND r.principal_id=NEW.author_principal_id AND r.state='publishing' AND r.proof IS NOT NULL)) THEN RAISE EXCEPTION 'Email proof required' USING ERRCODE='42501';END IF;RETURN NEW;END $$""")
    execute(f'CREATE TRIGGER guard_email_app_message BEFORE INSERT ON {q}.messages FOR EACH ROW EXECUTE FUNCTION {q}.guard_email_app_message()')
    execute(f"""CREATE FUNCTION {q}.validate_email_reply_graph() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE r record;BEGIN
      IF TG_TABLE_NAME='messages' THEN IF NEW.email_source_id IS NULL THEN RETURN NULL;END IF;SELECT * INTO r FROM {q}.email_reply_state WHERE tenant_id=NEW.tenant_id AND email_id=NEW.email_source_id;
      ELSE SELECT * INTO r FROM {q}.email_reply_state WHERE tenant_id=NEW.tenant_id AND email_id=NEW.email_id;END IF;
      IF r.state='publishing' OR (r.state='published' AND NOT EXISTS(SELECT 1 FROM {q}.email_messages e JOIN {q}.messages m ON m.tenant_id=e.tenant_id AND m.id=e.app_message_id WHERE e.tenant_id=r.tenant_id AND e.id=r.email_id AND e.verification_state='verified' AND e.case_id=r.case_id AND m.case_id=r.case_id AND m.email_source_id=e.id AND m.author_principal_id=r.principal_id AND m.source_kind='email_reply')) THEN RAISE EXCEPTION 'Incomplete email reply graph' USING ERRCODE='23514';END IF;RETURN NULL;END $$""")
    execute(f'REVOKE ALL ON FUNCTION {q}.validate_email_reply_graph() FROM PUBLIC');execute(f'ALTER FUNCTION {q}.validate_email_reply_graph() OWNER TO {owner}')
    for table in ('messages','email_reply_state'):execute(f'CREATE CONSTRAINT TRIGGER validate_email_reply_graph AFTER INSERT OR UPDATE ON {q}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_email_reply_graph()')
    fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.validate_case_commit()',)).scalar_one()
    execute(fn.replace('BEGIN',f"BEGIN IF current_user={literal} AND TG_TABLE_NAME IN('cases','case_history') THEN RETURN NULL;END IF; IF TG_TABLE_NAME='messages' THEN IF NEW.source_kind='email_reply' THEN RETURN NULL;END IF;END IF;",1))
    fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.guard_case_lifecycle()',)).scalar_one()
    marker='        IF current_user='
    # This private owner has no ownership/creation-field UPDATE grants.
    block=f"""IF current_user={literal} THEN
      IF OLD.status<>'closed' THEN IF (to_jsonb(NEW)-ARRAY['version','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['version','updated_at']) OR NOT EXISTS(SELECT 1 FROM {q}.email_reply_state r WHERE r.tenant_id=OLD.tenant_id AND r.case_id=OLD.id AND r.principal_id=OLD.owner_principal_id AND r.state='publishing' AND r.proof IS NOT NULL) THEN RAISE EXCEPTION 'Invalid email update' USING ERRCODE='23514';END IF;RETURN NEW;END IF;
      IF NEW.status NOT IN('new','in_progress') OR NEW.next_actor<>'staff' OR NEW.close_reason IS NOT NULL OR NEW.resolution IS NOT NULL OR NEW.closed_at IS NOT NULL
        OR NOT EXISTS(SELECT 1 FROM {q}.email_reply_state r WHERE r.tenant_id=OLD.tenant_id AND r.case_id=OLD.id AND r.principal_id=OLD.owner_principal_id AND r.state='publishing' AND r.proof IS NOT NULL)
        OR EXISTS(SELECT 1 FROM {q}.conversations v WHERE v.tenant_id=OLD.tenant_id AND v.case_id=OLD.id AND v.mode IN('waiting_staff','human')) THEN RAISE EXCEPTION 'Invalid email continuation' USING ERRCODE='23514';END IF;RETURN NEW;END IF;
    """
    pos=fn.index('IF current_user=');fn=fn[:pos]+block+fn[pos:];execute(fn)
    for function in ('validate_case_lifecycle','guard_lifecycle_history'):
        fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.'+function+'()',)).scalar_one()
        if function=='guard_lifecycle_history':
            check=f"IF NEW.email_source_id IS NOT NULL THEN IF current_user<>{literal} OR NEW.event_type NOT IN('reopened','email_received') OR NOT EXISTS(SELECT 1 FROM {q}.email_reply_state r WHERE r.tenant_id=NEW.tenant_id AND r.email_id=NEW.email_source_id AND r.case_id=NEW.case_id AND r.principal_id=NEW.actor_principal_id AND r.state='publishing') THEN RAISE EXCEPTION 'Email reopen proof missing' USING ERRCODE='23514';END IF;RETURN NEW;END IF;"
        else:
            check=f"IF current_user={literal} THEN IF NOT EXISTS(SELECT 1 FROM {q}.case_history h JOIN {q}.email_reply_state r ON r.tenant_id=h.tenant_id AND r.email_id=h.email_source_id WHERE h.tenant_id=NEW.tenant_id AND h.case_id=NEW.id AND h.previous_version=OLD.version AND h.resulting_version=NEW.version AND h.event_type IN('reopened','email_received') AND h.from_status=OLD.status AND h.to_status=NEW.status AND r.state='published' AND r.principal_id=NEW.owner_principal_id) THEN RAISE EXCEPTION 'Email history missing' USING ERRCODE='23514';END IF;RETURN NULL;END IF;"
        execute(fn.replace('BEGIN','BEGIN '+check,1))
    fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.validate_chat_transition()',)).scalar_one()
    execute(fn.replace('BEGIN',f"BEGIN IF TG_TABLE_NAME='cases' THEN IF current_user={literal} AND EXISTS(SELECT 1 FROM {q}.case_history h WHERE h.tenant_id=NEW.tenant_id AND h.case_id=NEW.id AND h.resulting_version=NEW.version AND h.email_source_id IS NOT NULL) THEN RETURN NULL;END IF;END IF;",1))


def _functions(q,owner,worker,s):
    fence=f"""SELECT * INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid;
      IF NOT FOUND OR j.kind NOT IN('email_reply','email_verify_send') OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w OR j.lease_token IS DISTINCT FROM token OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN NULL;END IF;"""
    snapshot=f"""CREATE FUNCTION {q}.email_reply_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j {q}.outbox_jobs%ROWTYPE;e {q}.email_messages%ROWTYPE;r {q}.email_reply_state%ROWTYPE;c {q}.cases%ROWTYPE;ct record;ch record;token_value text;refs text[];candidates uuid[];token_cases uuid[];cid uuid;why text;eligible boolean:=false;BEGIN
      {fence}
      SELECT x.* INTO e FROM {q}.email_messages x JOIN {q}.email_accounts a ON a.tenant_id=x.tenant_id AND a.id=x.email_account_id AND a.active JOIN {q}.tenants tn ON tn.id=x.tenant_id AND tn.active WHERE x.tenant_id=t AND x.id=j.reply_email_id AND x.direction='inbound';IF NOT FOUND THEN RETURN NULL;END IF;
      SELECT * INTO r FROM {q}.email_reply_state WHERE tenant_id=t AND email_id=e.id;
      IF e.verification_challenge_id IS NOT NULL THEN
        SELECT * INTO ch FROM {q}.email_reply_challenges WHERE tenant_id=t AND id=e.verification_challenge_id AND email_account_id=e.email_account_id;
        IF FOUND AND (position('+v-' in COALESCE(e.inbound_to,''))=0 OR substring(e.inbound_to from '\\+v-([a-f0-9]{{32}})@')=replace(ch.id::text,'-','')) THEN cid:=ch.case_id;ELSE why:='UNKNOWN_CHALLENGE';END IF;
      ELSE
        token_value:=substring(e.inbound_to from '\\+r-([A-Za-z0-9_-]{{32}})@');
        IF position('+r-' in COALESCE(e.inbound_to,''))>0 THEN
          SELECT array_agg(DISTINCT case_id) INTO token_cases FROM {q}.email_messages WHERE tenant_id=t AND email_account_id=e.email_account_id AND direction='outbound' AND reply_token_hash=encode(sha256(convert_to(COALESCE(token_value,''),'UTF8')),'hex');
          IF cardinality(token_cases) IS DISTINCT FROM 1 THEN why:='INVALID_THREAD';END IF;
        END IF;
        SELECT array_agg(m[1]) INTO refs FROM regexp_matches(COALESCE(e.in_reply_to,'')||' '||COALESCE(e.references_header,''),'<[^<>[:space:]]+>','g') m;
        IF COALESCE(cardinality(refs),0)>20 THEN why:='AMBIGUOUS_THREAD';END IF;
        SELECT array_agg(DISTINCT case_id) INTO candidates FROM {q}.email_messages WHERE tenant_id=t AND email_account_id=e.email_account_id AND direction='outbound' AND (rfc_message_id=ANY(COALESCE(refs,ARRAY[]::text[])) OR case_id=ANY(COALESCE(token_cases,ARRAY[]::uuid[])));
        IF cardinality(candidates)=1 AND why IS NULL THEN cid:=candidates[1];ELSE why:=COALESCE(why,'AMBIGUOUS_THREAD');END IF;
      END IF;
      IF cid IS NOT NULL THEN
        SELECT * INTO c FROM {q}.cases WHERE tenant_id=t AND id=cid;
        SELECT x.id,x.principal_id,x.email_address INTO ct FROM {q}.contacts x JOIN {q}.principals p ON p.tenant_id=x.tenant_id AND p.id=x.principal_id AND p.active
          JOIN {q}.case_participants cp ON cp.tenant_id=x.tenant_id AND cp.principal_id=x.principal_id AND cp.case_id=cid AND cp.revoked_at IS NULL
          WHERE x.tenant_id=t AND x.id=c.followup_contact_id AND x.id=c.owner_contact_id AND x.principal_id=c.owner_principal_id AND x.verified_at IS NOT NULL;
        eligible:=FOUND AND c.resource_security_labels='[]'::jsonb AND ct.email_address=e.sender_address;
        IF NOT eligible THEN why:='SENDER_OR_ACCESS_REJECTED';END IF;
      END IF;
      IF r.state='pending' THEN UPDATE {q}.email_reply_state SET case_id=cid,principal_id=c.owner_principal_id,reason=why WHERE tenant_id=t AND email_id=e.id;END IF;
      RETURN jsonb_build_object('state',r.state,'eligible',eligible,'body',e.body,'body_html',e.body_html,'authentication',e.inbound_authentication,'verification_challenge_id',e.verification_challenge_id);END $$"""
    apply=f"""CREATE FUNCTION {q}.email_reply_apply(t uuid,jid uuid,w uuid,token uuid,generation bigint,action text,plain text,new_challenge uuid,new_hash text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j {q}.outbox_jobs%ROWTYPE;e {q}.email_messages%ROWTYPE;source {q}.email_messages%ROWTYPE;r {q}.email_reply_state%ROWTYPE;c {q}.cases%ROWTYPE;before {q}.cases%ROWTYPE;ch {q}.email_reply_challenges%ROWTYPE;ct record;snap jsonb;mid uuid;seq bigint;meta jsonb;assignee uuid;first_line text;reopening boolean:=false;review text;proof_kind text;BEGIN
      IF action NOT IN('reject','review','challenge','verify','publish') THEN RAISE EXCEPTION 'Invalid reply command' USING ERRCODE='22023';END IF;
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      snap:={q}.email_reply_snapshot(t,jid,w,token,generation);IF snap IS NULL THEN RETURN false;END IF;
      SELECT * INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid;
      SELECT * INTO e FROM {q}.email_messages WHERE tenant_id=t AND id=j.reply_email_id;
      SELECT * INTO r FROM {q}.email_reply_state WHERE tenant_id=t AND email_id=e.id;
      IF r.state IN('published','review','rejected','challenged','verification_reply') THEN RETURN true;END IF;
      IF r.case_id IS NOT NULL THEN SELECT * INTO c FROM {q}.cases WHERE tenant_id=t AND id=r.case_id FOR UPDATE;END IF;
      SELECT * INTO e FROM {q}.email_messages WHERE tenant_id=t AND id=j.reply_email_id FOR UPDATE;
      SELECT * INTO r FROM {q}.email_reply_state WHERE tenant_id=t AND email_id=e.id FOR UPDATE;
      IF r.state IN('published','review','rejected','challenged','verification_reply') THEN RETURN true;END IF;
      IF action IN('reject','review') OR c.id IS NULL THEN UPDATE {q}.email_reply_state SET state=CASE WHEN action='reject' THEN 'rejected' ELSE 'review' END,reason=COALESCE(reason,'REPLY_REVIEW') WHERE tenant_id=t AND email_id=e.id;RETURN true;END IF;
      PERFORM id FROM {q}.tenants WHERE id=t AND active FOR SHARE;
      IF NOT FOUND THEN RETURN false;END IF;
      PERFORM id FROM {q}.email_accounts WHERE tenant_id=t AND id=e.email_account_id AND active FOR SHARE;
      IF NOT FOUND THEN RETURN false;END IF;
      SELECT x.id,x.principal_id,x.email_address INTO ct FROM {q}.contacts x JOIN {q}.principals p ON p.tenant_id=x.tenant_id AND p.id=x.principal_id AND p.active
        JOIN {q}.case_participants cp ON cp.tenant_id=x.tenant_id AND cp.principal_id=x.principal_id AND cp.case_id=c.id AND cp.revoked_at IS NULL
        WHERE x.tenant_id=t AND x.id=c.followup_contact_id AND x.id=c.owner_contact_id AND x.principal_id=c.owner_principal_id AND x.verified_at IS NOT NULL FOR SHARE OF x,p,cp;
      IF NOT FOUND OR ct.email_address<>e.sender_address OR c.resource_security_labels<>'[]'::jsonb THEN UPDATE {q}.email_reply_state SET state='rejected',reason='SENDER_OR_ACCESS_REJECTED' WHERE tenant_id=t AND email_id=e.id;RETURN true;END IF;
      source:=e;
      IF action='challenge' THEN
        IF new_challenge IS NULL OR new_hash IS NULL OR new_hash !~ '^[a-f0-9]{{64}}$' THEN RETURN false;END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(t::text||':mail-verify:'||ct.id::text,0));
        IF (SELECT count(*) FROM {q}.email_reply_challenges WHERE tenant_id=t AND contact_id=ct.id AND created_at>clock_timestamp()-interval '15 minutes')>=3 THEN UPDATE {q}.email_reply_state SET state='review',reason='CHALLENGE_RATE_LIMIT' WHERE tenant_id=t AND email_id=e.id;RETURN true;END IF;
        INSERT INTO {q}.email_reply_challenges(tenant_id,id,email_id,case_id,email_account_id,principal_id,contact_id,recipient,code_hash) VALUES(t,new_challenge,e.id,c.id,e.email_account_id,ct.principal_id,ct.id,ct.email_address,new_hash);
        INSERT INTO {q}.outbox_jobs(tenant_id,case_id,action_draft_id,kind,reply_email_id,reply_challenge_id,reply_account_id,dedupe_key,payload,replay_policy) VALUES(t,c.id,c.origin_draft_id,'email_verify_send',e.id,new_challenge,e.email_account_id,'verify:'||new_challenge::text,jsonb_build_object('challenge_id',new_challenge::text),'reconcile');
        UPDATE {q}.email_reply_state SET state='challenged',pending_text=plain,reason='EMAIL_VERIFICATION_REQUIRED' WHERE tenant_id=t AND email_id=e.id;
      ELSE
        IF action='verify' THEN
          SELECT * INTO ch FROM {q}.email_reply_challenges WHERE tenant_id=t AND id=e.verification_challenge_id AND email_account_id=e.email_account_id FOR UPDATE;
          IF NOT FOUND OR ch.state<>'pending' OR ch.case_id<>c.id OR ch.contact_id<>ct.id OR ch.principal_id<>ct.principal_id OR ch.recipient<>e.sender_address OR ch.expires_at<=clock_timestamp() OR ch.attempts>=5 THEN UPDATE {q}.email_reply_state SET state='review',reason='CHALLENGE_INVALID' WHERE tenant_id=t AND email_id=e.id;RETURN true;END IF;
          UPDATE {q}.email_reply_challenges SET attempts=attempts+1 WHERE tenant_id=t AND id=ch.id;
          IF e.verification_response_hash IS DISTINCT FROM ch.code_hash THEN IF ch.attempts+1>=5 THEN UPDATE {q}.email_reply_challenges SET state='failed' WHERE tenant_id=t AND id=ch.id;UPDATE {q}.email_reply_state SET state='review',reason='CHALLENGE_ATTEMPTS_EXHAUSTED' WHERE tenant_id=t AND email_id=ch.email_id;END IF;UPDATE {q}.email_reply_state SET state='review',reason='CHALLENGE_INVALID' WHERE tenant_id=t AND email_id=e.id;RETURN true;END IF;
          SELECT * INTO source FROM {q}.email_messages WHERE tenant_id=t AND id=ch.email_id FOR UPDATE;
          IF source.sender_address<>ct.email_address OR source.email_account_id<>e.email_account_id THEN RETURN false;END IF;
          SELECT pending_text INTO plain FROM {q}.email_reply_state WHERE tenant_id=t AND email_id=source.id FOR UPDATE;proof_kind:='challenge';
        ELSE
          IF e.inbound_authentication->>'dkim' IS DISTINCT FROM 'pass' OR e.inbound_authentication->>'dmarc' IS DISTINCT FROM 'pass' THEN RETURN false;END IF;
          proof_kind:='authenticated_email';
        END IF;
        IF length(plain) NOT BETWEEN 1 AND 4000 OR plain !~ '[^[:space:]]' OR (source.body<>'' AND source.body<>plain) THEN UPDATE {q}.email_reply_state SET state='review',reason='BODY_REVIEW' WHERE tenant_id=t AND email_id=source.id;RETURN true;END IF;
        first_line:=upper(btrim(split_part(btrim(plain),E'\\n',1)));
        IF first_line IN('NEW ISSUE','MASALAH BARU') THEN UPDATE {q}.email_reply_state SET state='review',reason='NEW_ISSUE_REQUIRES_CONFIRMATION' WHERE tenant_id=t AND email_id=source.id;RETURN true;END IF;
        reopening:=c.status='closed' AND first_line IN('CONTINUE SAME ISSUE','LANJUTKAN MASALAH YANG SAMA');
        review:=CASE WHEN c.status='closed' AND NOT reopening AND lower(btrim(plain)) NOT IN('thanks','thank you','terima kasih') THEN 'CLOSED_REPLY_AMBIGUOUS' ELSE NULL END;
        UPDATE {q}.email_reply_state SET state='publishing',proof=proof_kind,case_id=c.id,principal_id=ct.principal_id WHERE tenant_id=t AND email_id=source.id;
        SELECT COALESCE(max(sequence),0)+1 INTO seq FROM {q}.messages WHERE tenant_id=t AND case_id=c.id;
        mid:=gen_random_uuid();
        INSERT INTO {q}.messages(tenant_id,id,case_id,author_principal_id,author_type,channel,source_kind,body,language,client_message_id,sequence,email_source_id) VALUES(t,mid,c.id,ct.principal_id,'customer','email','email_reply',plain,c.language,source.id,seq,source.id);
        before:=c;
        IF reopening OR c.status<>'closed' THEN
          PERFORM set_config('app.tenant_id',t::text,true);
          IF reopening THEN
            meta:={q}.case_member_metadata(c.assigned_membership_id,c.organization_id,c.queue_id);
            assignee:=CASE WHEN meta IS NOT NULL THEN c.assigned_membership_id ELSE NULL END;
            UPDATE {q}.cases SET status=CASE WHEN assignee IS NULL THEN 'new' ELSE 'in_progress' END,assigned_membership_id=assignee,next_actor='staff',next_action='Customer continued by email.',close_reason=NULL,resolution=NULL,duplicate_of_case_id=NULL,followup_evidence=NULL,closed_at=NULL,version=version+1 WHERE tenant_id=t AND id=c.id RETURNING * INTO c;
          ELSE UPDATE {q}.cases SET version=version+1 WHERE tenant_id=t AND id=c.id RETURNING * INTO c;END IF;
          INSERT INTO {q}.case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version,from_status,to_status,assigned_membership_id,next_actor,next_action,close_reason,resolution,duplicate_of_case_id,followup_evidence,customer_message,client_request_id,request_hash,email_source_id)
            VALUES(t,c.id,ct.principal_id,CASE WHEN reopening THEN 'reopened' ELSE 'email_received' END,before.version,c.version,before.status,c.status,c.assigned_membership_id,c.next_actor,c.next_action,c.close_reason,c.resolution,c.duplicate_of_case_id,c.followup_evidence,plain,source.id,source.content_digest,source.id);
        END IF;
        UPDATE {q}.email_messages SET case_id=c.id,app_message_id=mid,recipient_contact_id=ct.id,recipient_principal_id=ct.principal_id,verification_state='verified' WHERE tenant_id=t AND id=source.id;
        UPDATE {q}.email_reply_state SET state='published',app_message_id=mid,reason=review WHERE tenant_id=t AND email_id=source.id;
        IF action='verify' THEN
          IF ch.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Challenge expired during publication' USING ERRCODE='Z0018';END IF;UPDATE {q}.email_reply_challenges SET state='consumed' WHERE tenant_id=t AND id=ch.id AND state='pending';
          UPDATE {q}.email_reply_state SET state='verification_reply',reason=NULL WHERE tenant_id=t AND email_id=e.id;
        END IF;
      END IF;
      SET CONSTRAINTS ALL IMMEDIATE;
      IF action='verify' AND ch.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Challenge expired during publication' USING ERRCODE='Z0018';END IF;
      IF {q}.email_reply_snapshot(t,jid,w,token,generation) IS NULL THEN RAISE EXCEPTION 'Reply lease expired' USING ERRCODE='Z0018';END IF;
      SET CONSTRAINTS ALL DEFERRED;RETURN true;END $$"""
    challenge=f"""CREATE FUNCTION {q}.email_challenge_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE j {q}.outbox_jobs%ROWTYPE;result jsonb;BEGIN {fence}
      IF j.kind<>'email_verify_send' THEN RETURN NULL;END IF;
      SELECT jsonb_build_object('id',ch.id,'code_hash',ch.code_hash,'send_state',ch.send_state,'app_message_id',c.initial_message_id,'account_id',a.id,'sender',a.sending_identity,'recipient',ct.email_address,'reply_address',a.reply_address,'provider_account_ref',a.provider_account_ref) INTO result
      FROM {q}.email_reply_challenges ch JOIN {q}.cases c ON c.tenant_id=ch.tenant_id AND c.id=ch.case_id AND c.owner_principal_id=ch.principal_id AND c.followup_contact_id=ch.contact_id AND c.owner_contact_id=ch.contact_id
      JOIN {q}.contacts ct ON ct.tenant_id=ch.tenant_id AND ct.id=ch.contact_id AND ct.principal_id=ch.principal_id AND ct.verified_at IS NOT NULL AND ct.email_address=ch.recipient
      JOIN {q}.principals p ON p.tenant_id=ct.tenant_id AND p.id=ct.principal_id AND p.active
      JOIN {q}.case_participants cp ON cp.tenant_id=c.tenant_id AND cp.case_id=c.id AND cp.principal_id=ch.principal_id AND cp.revoked_at IS NULL
      JOIN {q}.email_accounts a ON a.tenant_id=ch.tenant_id AND a.id=ch.email_account_id AND a.active JOIN {q}.tenants tn ON tn.id=ch.tenant_id AND tn.active
      WHERE ch.tenant_id=t AND ch.id=j.reply_challenge_id AND ch.email_id=j.reply_email_id AND ch.state='pending' AND ch.expires_at>clock_timestamp() AND c.resource_security_labels='[]'::jsonb;RETURN result;END $$"""
    functions=[('email_reply_snapshot(uuid,uuid,uuid,uuid,bigint)',snapshot),('email_reply_apply(uuid,uuid,uuid,uuid,bigint,text,text,uuid,text)',apply),('email_challenge_snapshot(uuid,uuid,uuid,uuid,bigint)',challenge)]
    functions.append(('email_challenge_reserve(uuid,uuid,uuid,uuid,bigint)',f"""CREATE FUNCTION {q}.email_challenge_reserve(t uuid,jid uuid,w uuid,token uuid,generation bigint) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE snap jsonb;BEGIN
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      snap:={q}.email_challenge_snapshot(t,jid,w,token,generation);IF snap IS NULL THEN RETURN false;END IF;
      PERFORM 1 FROM {q}.email_reply_challenges WHERE tenant_id=t AND id=(snap->>'id')::uuid FOR UPDATE;
      IF {q}.email_challenge_snapshot(t,jid,w,token,generation) IS NULL THEN RETURN false;END IF;
      UPDATE {q}.email_reply_challenges SET send_state='reserved' WHERE tenant_id=t AND id=(snap->>'id')::uuid AND send_state='queued';RETURN FOUND;END $$"""))
    functions.append(('email_challenge_sent(uuid,uuid,uuid,uuid,bigint,text,text)',f"""CREATE FUNCTION {q}.email_challenge_sent(t uuid,jid uuid,w uuid,token uuid,generation bigint,result text,receipt text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE snap jsonb;BEGIN
      IF result NOT IN('accepted','failed','delivery_unknown') OR (result='accepted') IS DISTINCT FROM (receipt IS NOT NULL) THEN RAISE EXCEPTION 'Invalid send result' USING ERRCODE='22023';END IF;
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      snap:={q}.email_challenge_snapshot(t,jid,w,token,generation);IF snap IS NULL THEN RETURN false;END IF;
      PERFORM 1 FROM {q}.email_reply_challenges WHERE tenant_id=t AND id=(snap->>'id')::uuid FOR UPDATE;
      IF {q}.email_challenge_snapshot(t,jid,w,token,generation) IS NULL THEN RETURN false;END IF;
      UPDATE {q}.email_reply_challenges SET send_state=result,provider_message_id=receipt WHERE tenant_id=t AND id=(snap->>'id')::uuid AND send_state='reserved';RETURN FOUND;END $$"""))
    for signature,definition in functions:
        if signature.startswith(('email_reply_snapshot','email_reply_apply','email_challenge_reserve','email_challenge_sent')):
            final=f"IF NOT EXISTS(SELECT 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'Reply lease expired' USING ERRCODE='Z0018';END IF;"
            definition=definition.replace('RETURN true;',final+'RETURN true;').replace('RETURN FOUND;', 'IF NOT FOUND THEN RETURN false;END IF;'+final+'RETURN true;').replace("RETURN jsonb_build_object('state'",final+"RETURN jsonb_build_object('state'")
        execute(definition);execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC');execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {owner}');execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {worker}')


def downgrade():raise RuntimeError('Email reply proofs require a reviewed recovery migration.')
