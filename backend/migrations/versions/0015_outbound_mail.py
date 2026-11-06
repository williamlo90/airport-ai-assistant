"""Lease-scoped outbound notifications, isolated from the metadata scheduler."""
import hashlib
from alembic import context, op
from airport_assistant.infrastructure.database.provisioning import prepare_auth_owner

revision = '0015_outbound_mail'
down_revision = '0014_job_worker'
branch_labels = None
depends_on = None


def upgrade():
    attrs = context.config.attributes
    schema = attrs['schema']
    owner_name = 'airport_assistant_mail_owner' if schema == 'app' else 'mail_owner_' + hashlib.sha256(schema.encode()).hexdigest()[:24]
    prepare_auth_owner(op.get_bind(), owner_name)
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, worker, runtime, owner = (quote(v) for v in (schema, attrs['worker_role'], attrs['runtime_role'], owner_name))
    op.execute(f"ALTER TABLE {q}.messages ADD COLUMN delivery_channel text NOT NULL DEFAULT 'chat' CHECK(delivery_channel IN('chat','email'))")
    op.execute(f"GRANT INSERT(delivery_channel) ON {q}.messages TO {runtime}")
    op.execute(f"ALTER TABLE {q}.outbox_jobs DROP CONSTRAINT outbox_jobs_kind_check,DROP CONSTRAINT outbox_jobs_tenant_id_case_id_kind_key,ADD COLUMN source_message_id uuid,ADD CONSTRAINT job_kind CHECK(kind IN('case_created','email_send')),ADD CONSTRAINT email_job_shape CHECK((kind='email_send')=(source_message_id IS NOT NULL)),ADD CONSTRAINT job_source_message FOREIGN KEY(tenant_id,source_message_id) REFERENCES {q}.messages(tenant_id,id)")
    op.execute(f"CREATE UNIQUE INDEX case_created_job ON {q}.outbox_jobs(tenant_id,case_id) WHERE kind='case_created'")
    op.execute(f"CREATE UNIQUE INDEX email_send_job ON {q}.outbox_jobs(tenant_id,source_message_id) WHERE kind='email_send'")
    op.execute(f"CREATE POLICY staff_email_job_read ON {q}.outbox_jobs FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND kind='email_send' AND {q}.case_staff_session_valid() AND EXISTS(SELECT 1 FROM {q}.messages m WHERE m.tenant_id=outbox_jobs.tenant_id AND m.id=outbox_jobs.source_message_id AND m.author_principal_id={q}.current_principal_id()))")
    op.execute(f"GRANT INSERT(kind,source_message_id) ON {q}.outbox_jobs TO {runtime}")
    op.execute(f"""CREATE FUNCTION {q}.guard_email_job_graph() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF TG_TABLE_NAME='messages' THEN
        IF NEW.delivery_channel='email' AND (NEW.author_type<>'staff' OR NOT EXISTS(SELECT 1 FROM {q}.outbox_jobs j WHERE j.tenant_id=NEW.tenant_id AND j.source_message_id=NEW.id AND j.kind='email_send')) THEN
          RAISE EXCEPTION 'Missing outbound job' USING ERRCODE='23514';END IF;
      ELSIF NEW.kind='email_send' THEN
        IF NOT EXISTS(SELECT 1 FROM {q}.messages m JOIN {q}.conversations v ON v.tenant_id=m.tenant_id AND v.id=m.conversation_id JOIN {q}.cases c ON c.tenant_id=v.tenant_id AND c.id=v.case_id
          WHERE m.tenant_id=NEW.tenant_id AND m.id=NEW.source_message_id AND m.author_type='staff' AND m.delivery_channel='email' AND c.id=NEW.case_id AND c.origin_draft_id=NEW.action_draft_id) THEN
          RAISE EXCEPTION 'Invalid outbound graph' USING ERRCODE='23514';END IF;
      END IF;RETURN NULL;END $$""")
    for table in ('messages','outbox_jobs'):
        op.execute(f'CREATE CONSTRAINT TRIGGER email_job_graph AFTER INSERT ON {q}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.guard_email_job_graph()')
    common = f'tenant_id uuid NOT NULL REFERENCES {q}.tenants(id),id uuid NOT NULL DEFAULT gen_random_uuid(),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,id)'
    op.execute(f"""CREATE TABLE {q}.email_accounts({common},provider text NOT NULL CHECK(provider='resend'),
      provider_account_ref text NOT NULL UNIQUE CHECK(provider_account_ref ~ '^[A-Za-z0-9_-]{{1,80}}$'),
      sending_identity text NOT NULL CHECK(length(sending_identity) BETWEEN 3 AND 320 AND sending_identity !~ E'[\\r\\n]'),
      reply_address text NOT NULL CHECK(length(reply_address) BETWEEN 3 AND 320 AND reply_address !~ E'[\\r\\n]'),
      active boolean NOT NULL DEFAULT false,UNIQUE(tenant_id,provider))""")
    op.execute(f"""CREATE TABLE {q}.email_messages({common},case_id uuid NOT NULL,app_message_id uuid NOT NULL,
      email_account_id uuid NOT NULL,job_id uuid NOT NULL,direction text NOT NULL DEFAULT 'outbound' CHECK(direction='outbound'),
      recipient_contact_id uuid NOT NULL,recipient_principal_id uuid NOT NULL,
      sender_address text NOT NULL,recipient_address text NOT NULL,reply_address text NOT NULL,
      rfc_message_id text,requested_rfc_message_id text NOT NULL CHECK(requested_rfc_message_id ~ '^<[a-f0-9]{{32}}@[A-Za-z0-9.-]+>$'),delivery_key text NOT NULL,
      subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 200 AND subject !~ E'[\\r\\n]'),
      body text NOT NULL CHECK(length(body) BETWEEN 1 AND 8000),
      resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
      verification_state text NOT NULL DEFAULT 'verified' CHECK(verification_state='verified'),
      delivery_status text NOT NULL DEFAULT 'sending' CHECK(delivery_status IN('sending','accepted','failed','delivery_unknown')),
      provider_message_id text CHECK(provider_message_id ~ '^[A-Za-z0-9_-]{{1,128}}$'),
      completed_at timestamptz,
      CHECK((delivery_status='accepted')=(provider_message_id IS NOT NULL)),
      CHECK((delivery_status='sending')=(completed_at IS NULL)),
      UNIQUE(tenant_id,job_id),UNIQUE(tenant_id,app_message_id),UNIQUE(email_account_id,delivery_key),
      FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
      FOREIGN KEY(tenant_id,app_message_id) REFERENCES {q}.messages(tenant_id,id),
      FOREIGN KEY(tenant_id,email_account_id) REFERENCES {q}.email_accounts(tenant_id,id),
      FOREIGN KEY(tenant_id,job_id) REFERENCES {q}.outbox_jobs(tenant_id,id),
      FOREIGN KEY(tenant_id,recipient_contact_id) REFERENCES {q}.contacts(tenant_id,id),
      FOREIGN KEY(tenant_id,recipient_principal_id) REFERENCES {q}.principals(tenant_id,id))""")
    op.execute(f"CREATE UNIQUE INDEX email_provider_receipt ON {q}.email_messages(email_account_id,direction,provider_message_id) WHERE provider_message_id IS NOT NULL")
    scheduler_owner=quote(attrs['job_owner_role'])
    op.execute(f'GRANT SELECT(tenant_id,provider_account_ref,active) ON {q}.email_accounts TO {scheduler_owner}')
    op.execute(f'CREATE POLICY scheduler_account_read ON {q}.email_accounts FOR SELECT TO {scheduler_owner} USING(true)')
    # Account routing is a claim filter, not a post-claim permanent rejection.
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.job_claim(uuid,text[],integer)',)).scalar_one()
    selector=f"x.kind=ANY(kinds) AND EXISTS(SELECT 1 FROM {q}.email_accounts route WHERE route.tenant_id=x.tenant_id AND route.active AND COALESCE(NULLIF(current_setting('app.mail_accounts',true),''),'[]')::jsonb ? route.provider_account_ref)"
    if definition.count('x.kind=ANY(kinds)')!=2:raise RuntimeError('Unexpected scheduler definition.')
    op.execute(definition.replace('x.kind=ANY(kinds)',selector))
    op.execute(f'GRANT USAGE ON SCHEMA {q} TO {owner}')
    # Separate mail projection owner. The JOB scheduler owner retains metadata only.
    reads = {
        'tenants': 'id,active', 'outbox_jobs': 'tenant_id,id,case_id,kind,state,source_message_id,lease_worker_id,lease_token,lease_generation,lease_until',
        'cases': 'tenant_id,id,version,public_reference,initial_message_id,owner_principal_id,followup_contact_id,language,resource_security_labels',
        'contacts': 'tenant_id,id,principal_id,email_address,verified_at', 'principals': 'tenant_id,id,active',
        'messages': 'tenant_id,id,body,language,delivery_channel',
        'case_participants': 'tenant_id,case_id,principal_id,revoked_at',
    }
    for table, columns in reads.items():
        op.execute(f'GRANT SELECT({columns}) ON {q}.{table} TO {owner}')
        op.execute(f'CREATE POLICY mail_projection_read ON {q}.{table} FOR SELECT TO {owner} USING(true)')
    for table in ('email_accounts', 'email_messages'):
        op.execute(f'ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON {q}.{table} FROM PUBLIC')
        op.execute(f'GRANT SELECT ON {q}.{table} TO {owner}')
        op.execute(f'CREATE POLICY mail_owner_read ON {q}.{table} FOR SELECT TO {owner} USING(true)')
    op.execute(f'GRANT SELECT ON {q}.email_accounts TO {runtime}')
    op.execute(f'CREATE POLICY runtime_account_read ON {q}.email_accounts FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND {q}.case_staff_session_valid())')
    op.execute(f'GRANT INSERT,UPDATE(delivery_status,provider_message_id,completed_at) ON {q}.email_messages TO {owner}')
    op.execute(f'CREATE POLICY mail_owner_insert ON {q}.email_messages FOR INSERT TO {owner} WITH CHECK(true)')
    op.execute(f'CREATE POLICY mail_owner_update ON {q}.email_messages FOR UPDATE TO {owner} USING(true) WITH CHECK(true)')
    # Lock-only column grant is needed for fenced reservation, not job mutations.
    op.execute(f'GRANT UPDATE(lease_generation) ON {q}.outbox_jobs TO {owner}')
    op.execute(f'CREATE POLICY mail_job_lock ON {q}.outbox_jobs FOR UPDATE TO {owner} USING(true) WITH CHECK(false)')
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {owner}')
    fence = f"""SELECT * INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid;
      IF NOT FOUND OR j.kind NOT IN('case_created','email_send') OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w
        OR j.lease_token IS DISTINCT FROM token OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN NULL;END IF;"""
    # SELECT * above would require grants beyond the minimal projection. Use a record.
    fence = fence.replace('SELECT *', 'SELECT tenant_id,id,case_id,kind,state,source_message_id,lease_worker_id,lease_token,lease_generation,lease_until')
    functions = [
      ('mail_snapshot(uuid,uuid,uuid,uuid,bigint)', f"""CREATE FUNCTION {q}.mail_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE j record;result jsonb;BEGIN
      {fence}
      SELECT jsonb_build_object('tenant_id',c.tenant_id,'case_id',c.id,'case_version',c.version,'public_reference',c.public_reference,
        'app_message_id',COALESCE(j.source_message_id,c.initial_message_id),'body',CASE WHEN j.kind='email_send' THEN m.body ELSE NULL END,'kind',j.kind,'owner_principal_id',c.owner_principal_id,'principal_id',ct.principal_id,
        'followup_contact_id',c.followup_contact_id,'contact_id',ct.id,'recipient',ct.email_address,'verified',ct.verified_at IS NOT NULL,
        'principal_active',p.active,'participant_active',cp.revoked_at IS NULL AND cp.principal_id IS NOT NULL,
        'tenant_active',tn.active,'language',CASE WHEN j.kind='email_send' THEN m.language ELSE c.language END,'labels',c.resource_security_labels,'account_id',a.id,
        'provider_account_ref',a.provider_account_ref,'sender',a.sending_identity,'reply_to',a.reply_address,'account_active',a.active,
        'delivery_status',e.delivery_status) INTO result
      FROM {q}.cases c JOIN {q}.tenants tn ON tn.id=c.tenant_id
      JOIN {q}.contacts ct ON ct.tenant_id=c.tenant_id AND ct.id=c.followup_contact_id
      JOIN {q}.principals p ON p.tenant_id=ct.tenant_id AND p.id=ct.principal_id
      LEFT JOIN {q}.case_participants cp ON cp.tenant_id=c.tenant_id AND cp.case_id=c.id AND cp.principal_id=ct.principal_id
      JOIN {q}.email_accounts a ON a.tenant_id=c.tenant_id AND a.provider='resend'
      LEFT JOIN {q}.messages m ON m.tenant_id=c.tenant_id AND m.id=j.source_message_id
      LEFT JOIN {q}.email_messages e ON e.tenant_id=c.tenant_id AND e.job_id=j.id
      WHERE c.tenant_id=t AND c.id=j.case_id;RETURN result;END $$"""),
      ('mail_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)', f"""CREATE FUNCTION {q}.mail_begin(t uuid,jid uuid,w uuid,token uuid,generation bigint,expected jsonb,envelope jsonb)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE current_snapshot jsonb;BEGIN
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      current_snapshot:={q}.mail_snapshot(t,jid,w,token,generation);
      IF current_snapshot IS NULL OR current_snapshot IS DISTINCT FROM expected OR current_snapshot->>'delivery_status' IS NOT NULL THEN RETURN false;END IF;
      IF current_snapshot->'labels' IS DISTINCT FROM '[]'::jsonb OR current_snapshot->>'tenant_active' IS DISTINCT FROM 'true'
        OR current_snapshot->>'account_active' IS DISTINCT FROM 'true' OR current_snapshot->>'principal_active' IS DISTINCT FROM 'true'
        OR current_snapshot->>'verified' IS DISTINCT FROM 'true' OR current_snapshot->>'participant_active' IS DISTINCT FROM 'true'
        OR current_snapshot->>'principal_id' IS DISTINCT FROM current_snapshot->>'owner_principal_id' THEN RETURN false;END IF;
      IF envelope->>'sender' IS DISTINCT FROM current_snapshot->>'sender' OR envelope->>'recipient' IS DISTINCT FROM current_snapshot->>'recipient'
        OR envelope->>'reply_to' IS DISTINCT FROM current_snapshot->>'reply_to'
        OR envelope->>'app_message_id' IS DISTINCT FROM current_snapshot->>'app_message_id'
        OR envelope->>'account_id' IS DISTINCT FROM current_snapshot->>'account_id' THEN RETURN false;END IF;
      INSERT INTO {q}.email_messages(tenant_id,id,case_id,app_message_id,email_account_id,job_id,recipient_contact_id,recipient_principal_id,
        sender_address,recipient_address,reply_address,requested_rfc_message_id,delivery_key,subject,body)
      VALUES(t,(envelope->>'id')::uuid,(current_snapshot->>'case_id')::uuid,(envelope->>'app_message_id')::uuid,(envelope->>'account_id')::uuid,jid,
        (current_snapshot->>'contact_id')::uuid,(current_snapshot->>'principal_id')::uuid,envelope->>'sender',envelope->>'recipient',envelope->>'reply_to',
        envelope->>'rfc_message_id',envelope->>'delivery_key',envelope->>'subject',envelope->>'text');
      IF {q}.mail_snapshot(t,jid,w,token,generation) IS NULL THEN RAISE EXCEPTION 'Reservation expired' USING ERRCODE='Z0015';END IF;
      RETURN true;END $$"""),
      ('mail_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text)', f"""CREATE FUNCTION {q}.mail_complete(t uuid,jid uuid,w uuid,token uuid,generation bigint,eid uuid,result text,receipt text)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE current_snapshot jsonb;BEGIN
      IF result IS NULL OR result NOT IN('accepted','failed','delivery_unknown') OR (result='accepted') IS DISTINCT FROM (receipt IS NOT NULL) THEN RAISE EXCEPTION 'Invalid mail outcome' USING ERRCODE='22023';END IF;
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      PERFORM 1 FROM {q}.email_messages WHERE tenant_id=t AND id=eid AND job_id=jid FOR UPDATE;
      current_snapshot:={q}.mail_snapshot(t,jid,w,token,generation);
      IF current_snapshot IS NULL THEN RETURN false;END IF;
      UPDATE {q}.email_messages SET delivery_status=result,provider_message_id=receipt,completed_at=clock_timestamp()
        WHERE tenant_id=t AND id=eid AND job_id=jid AND delivery_status='sending';RETURN FOUND;END $$"""),
    ]
    for signature, definition in functions:
        op.execute(definition)
        op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC')
        op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {owner}')
        op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {worker}')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def downgrade():
    raise RuntimeError('Outbound mail requires a reviewed recovery migration.')
