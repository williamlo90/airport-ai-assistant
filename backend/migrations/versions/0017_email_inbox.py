"""Authenticated durable inbox, quarantined received content and event jobs."""
import hashlib
from alembic import context,op
from airport_assistant.infrastructure.database.provisioning import prepare_auth_owner

revision='0017_email_inbox'
down_revision='0016_mail_delivery'
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes;schema=attrs['schema'];quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    suffix=hashlib.sha256(schema.encode()).hexdigest()[:24]
    ingress_name='airport_assistant_ingress_owner' if schema=='app' else 'email_ingress_'+suffix
    mail_name='airport_assistant_mail_owner' if schema=='app' else 'mail_owner_'+suffix
    prepare_auth_owner(op.get_bind(),ingress_name)
    q,ingress,mail,worker,runtime,scheduler=(quote(v) for v in (schema,ingress_name,mail_name,attrs['worker_role'],attrs['runtime_role'],attrs['job_owner_role']))
    op.execute(f"ALTER TABLE {q}.email_accounts ADD COLUMN account_alias text UNIQUE CHECK(account_alias ~ '^[a-z][a-z0-9-]{{0,62}}$'),ADD COLUMN inbound_address text CHECK(length(inbound_address) BETWEEN 3 AND 320 AND inbound_address !~ E'[\\r\\n]')")
    op.execute(f"""CREATE TABLE {q}.email_inbox(tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),email_account_id uuid NOT NULL,
      provider_event_id text NOT NULL CHECK(provider_event_id ~ '^[A-Za-z0-9_-]{{1,128}}$'),provider_message_id text CHECK(provider_message_id ~ '^[A-Za-z0-9_-]{{1,128}}$'),
      event_type text NOT NULL CHECK(length(event_type) BETWEEN 3 AND 80),event_at timestamptz,body_digest text NOT NULL CHECK(body_digest ~ '^[a-f0-9]{{64}}$'),
      signature_checked boolean NOT NULL DEFAULT true CHECK(signature_checked),received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      processing_state text NOT NULL CHECK(processing_state IN('received','processed','review','ignored')),email_message_id uuid,
      review_reason text CHECK(review_reason IN('FETCH_UNAVAILABLE','UNMATCHED_RECEIPT','CONTENT_CHANGED','WORKER_STOPPED')),
      PRIMARY KEY(tenant_id,id),UNIQUE(email_account_id,provider_event_id),FOREIGN KEY(tenant_id,email_account_id) REFERENCES {q}.email_accounts(tenant_id,id),
      FOREIGN KEY(tenant_id,email_message_id) REFERENCES {q}.email_messages(tenant_id,id),
      CHECK((processing_state='review')=(review_reason IS NOT NULL)),CHECK(processing_state='ignored' OR (provider_message_id IS NOT NULL AND event_at IS NOT NULL)))""")
    op.execute(f'ALTER TABLE {q}.email_inbox ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {q}.email_inbox FORCE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON {q}.email_inbox FROM PUBLIC')
    for owner in (ingress,mail):
        op.execute(f'GRANT USAGE ON SCHEMA {q} TO {owner}')
        op.execute(f'GRANT SELECT ON {q}.email_inbox TO {owner}')
        op.execute(f'CREATE POLICY inbox_{owner.strip(chr(34))}_read ON {q}.email_inbox FOR SELECT TO {owner} USING(true)')
    op.execute(f'GRANT INSERT ON {q}.email_inbox TO {ingress}')
    op.execute(f'CREATE POLICY ingress_insert ON {q}.email_inbox FOR INSERT TO {ingress} WITH CHECK(true)')
    op.execute(f'GRANT UPDATE(processing_state,email_message_id,review_reason) ON {q}.email_inbox TO {mail}')
    op.execute(f'CREATE POLICY inbox_update ON {q}.email_inbox FOR UPDATE TO {mail} USING(true) WITH CHECK(true)')
    op.execute(f'GRANT SELECT(id,tenant_id,provider_account_ref,account_alias,inbound_address,active) ON {q}.email_accounts TO {ingress}')
    op.execute(f'CREATE POLICY ingress_account_read ON {q}.email_accounts FOR SELECT TO {ingress} USING(true)')
    op.execute(f'GRANT SELECT(id,active) ON {q}.tenants TO {ingress}')
    op.execute(f'CREATE POLICY ingress_tenant_read ON {q}.tenants FOR SELECT TO {ingress} USING(true)')
    # Only inbox kinds may lack case/draft; all ordinary jobs retain their graph.
    for column in ('case_id','action_draft_id'):op.execute(f'ALTER TABLE {q}.outbox_jobs ALTER COLUMN {column} DROP NOT NULL')
    checks=op.get_bind().exec_driver_sql("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",(schema+'.outbox_jobs',)).all()
    payload=[n for n,d in checks if 'jsonb_build_object' in d and 'payload' in d]
    if len(payload)!=1:raise RuntimeError('Unexpected job payload shape.')
    op.execute(f"""ALTER TABLE {q}.outbox_jobs DROP CONSTRAINT {quote(payload[0])},DROP CONSTRAINT job_kind,DROP CONSTRAINT reconcile_job_shape,ADD COLUMN email_inbox_id uuid,ADD FOREIGN KEY(tenant_id,email_inbox_id) REFERENCES {q}.email_inbox(tenant_id,id),ADD CONSTRAINT job_kind CHECK(kind IN('case_created','email_send','email_reconcile','email_fetch','email_event')),
      ADD CONSTRAINT inbox_job_shape CHECK((kind IN('email_fetch','email_event'))=(email_inbox_id IS NOT NULL)),
      ADD CONSTRAINT ordinary_job_shape CHECK((case_id IS NULL)=(action_draft_id IS NULL) AND (kind IN('email_fetch','email_event') OR case_id IS NOT NULL) AND (kind<>'email_fetch' OR case_id IS NULL) AND (kind<>'email_event' OR (email_message_id IS NULL)=(case_id IS NULL))),
      ADD CONSTRAINT reconcile_job_shape CHECK((kind='email_reconcile' AND email_message_id IS NOT NULL) OR (kind='email_event') OR (kind NOT IN('email_reconcile','email_event') AND email_message_id IS NULL)),
      ADD CONSTRAINT job_payload CHECK(payload=CASE WHEN kind IN('email_fetch','email_event') THEN jsonb_build_object('inbox_id',email_inbox_id::text) ELSE jsonb_build_object('case_id',case_id::text,'draft_id',action_draft_id::text) END)""")
    op.execute(f'CREATE UNIQUE INDEX inbox_job_once ON {q}.outbox_jobs(tenant_id,email_inbox_id) WHERE email_inbox_id IS NOT NULL')
    op.execute(f'GRANT INSERT(tenant_id,kind,email_inbox_id,dedupe_key,payload,replay_policy,next_attempt_at) ON {q}.outbox_jobs TO {ingress}')
    op.execute(f"CREATE POLICY ingress_job_insert ON {q}.outbox_jobs FOR INSERT TO {ingress} WITH CHECK(kind IN('email_fetch','email_event') AND EXISTS(SELECT 1 FROM {q}.email_inbox i WHERE i.tenant_id=outbox_jobs.tenant_id AND i.id=outbox_jobs.email_inbox_id))")
    op.execute(f'GRANT SELECT(email_inbox_id) ON {q}.outbox_jobs TO {mail}')
    op.execute(f'GRANT UPDATE(case_id,action_draft_id,email_message_id) ON {q}.outbox_jobs TO {mail}')
    op.execute(f"CREATE POLICY bind_event_job ON {q}.outbox_jobs FOR UPDATE TO {mail} USING(kind='email_event') WITH CHECK(kind='email_event')")
    op.execute(f'GRANT SELECT(tenant_id,id,email_account_id,provider_message_id) ON {q}.email_inbox TO {scheduler}')
    op.execute(f'CREATE POLICY inbox_scheduler_metadata ON {q}.email_inbox FOR SELECT TO {scheduler} USING(true)')
    op.execute(f'GRANT SELECT(id) ON {q}.email_accounts TO {scheduler}')
    op.execute(f'GRANT SELECT(email_account_id,direction,provider_message_id) ON {q}.email_messages TO {scheduler}')
    # Preserve outbound NOT NULL/shape guarantees while adding a private inbound stage.
    for col in ('case_id','app_message_id','recipient_contact_id','recipient_principal_id','reply_address','requested_rfc_message_id','delivery_key','delivery_status'):
        op.execute(f'ALTER TABLE {q}.email_messages ALTER COLUMN {col} DROP NOT NULL')
    checks=op.get_bind().exec_driver_sql("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",(schema+'.email_messages',)).all()
    drop=[n for n,d in checks if any(key in d for key in ('direction =','verification_state =','length(subject)','length(body)','completed_at IS NULL','delivery_status <>'))]
    for name in drop:op.execute(f'ALTER TABLE {q}.email_messages DROP CONSTRAINT {quote(name)}')
    op.execute(f"""ALTER TABLE {q}.email_messages ADD COLUMN body_html text,ADD COLUMN inbound_authentication jsonb,ADD COLUMN in_reply_to text,ADD COLUMN references_header text,ADD COLUMN inbound_reply_to text,
      ADD COLUMN content_digest text CHECK(content_digest ~ '^[a-f0-9]{{64}}$'),ADD COLUMN attachment_count integer NOT NULL DEFAULT 0 CHECK(attachment_count BETWEEN 0 AND 1000),
      ADD CONSTRAINT email_subject_header CHECK(subject !~ E'[\\r\\n]'),ADD CONSTRAINT message_direction CHECK(direction IN('inbound','outbound')),
      ADD CONSTRAINT outbound_email_shape CHECK(direction<>'outbound' OR (case_id IS NOT NULL AND app_message_id IS NOT NULL AND recipient_contact_id IS NOT NULL AND recipient_principal_id IS NOT NULL AND reply_address IS NOT NULL AND requested_rfc_message_id IS NOT NULL AND delivery_key IS NOT NULL AND verification_state='verified' AND delivery_status IS NOT NULL AND ((delivery_status='sending')=(completed_at IS NULL)) AND length(subject) BETWEEN 1 AND 200 AND length(body) BETWEEN 1 AND 8000 AND body_html IS NULL AND (delivery_status NOT IN('accepted','delivered','bounced') OR provider_message_id IS NOT NULL) AND (delivery_status<>'sending' OR provider_message_id IS NULL))),
      ADD CONSTRAINT inbound_email_shape CHECK(direction<>'inbound' OR (case_id IS NULL AND app_message_id IS NULL AND recipient_contact_id IS NULL AND recipient_principal_id IS NULL AND requested_rfc_message_id IS NULL AND delivery_key IS NULL AND verification_state='pending' AND delivery_status IS NULL AND completed_at IS NULL AND provider_message_id IS NOT NULL AND content_digest IS NOT NULL AND length(subject)<=998 AND length(body)<=65536 AND length(body_html)<=65536 AND NOT needs_review))""")
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.queue_email_reconcile()',)).scalar_one()
    op.execute(definition.replace('BEGIN',"BEGIN IF NEW.direction<>'outbound' THEN RETURN NEW;END IF;",1))
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {ingress},{mail}')
    op.execute(f"""CREATE FUNCTION {q}.email_webhook_accept(alias text,account_ref text,event_id text,notice jsonb) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE a record;previous record;i uuid;kind text;BEGIN
      IF event_id IS NULL OR event_id !~ '^[A-Za-z0-9_-]{{1,128}}$' OR notice->>'digest' IS NULL OR notice->>'digest' !~ '^[a-f0-9]{{64}}$' THEN RAISE EXCEPTION 'Invalid inbox metadata' USING ERRCODE='22023';END IF;
      SELECT x.id,x.tenant_id,x.inbound_address INTO a FROM {q}.email_accounts x JOIN {q}.tenants t ON t.id=x.tenant_id AND t.active
        WHERE x.account_alias=alias AND x.provider_account_ref=account_ref AND x.active;
      IF NOT FOUND THEN RETURN 'unavailable';END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(a.id::text||':'||event_id,0));
      SELECT body_digest INTO previous FROM {q}.email_inbox WHERE email_account_id=a.id AND provider_event_id=event_id;
      IF FOUND THEN RETURN CASE WHEN previous.body_digest=notice->>'digest' THEN 'accepted' ELSE 'conflict' END;END IF;
      kind:=CASE WHEN notice->>'event_type'='email.received' THEN 'email_fetch' WHEN notice->>'event_type' IN('email.sent','email.delivered','email.bounced','email.failed','email.suppressed','email.delivery_delayed') THEN 'email_event' END;
      IF kind='email_fetch' AND a.inbound_address IS NULL THEN RETURN 'unavailable';END IF;
      INSERT INTO {q}.email_inbox(tenant_id,email_account_id,provider_event_id,provider_message_id,event_type,event_at,body_digest,processing_state)
        VALUES(a.tenant_id,a.id,event_id,notice->>'provider_message_id',notice->>'event_type',(notice->>'event_at')::timestamptz,notice->>'digest',CASE WHEN kind IS NULL THEN 'ignored' ELSE 'received' END) RETURNING id INTO i;
      IF kind IS NOT NULL THEN INSERT INTO {q}.outbox_jobs(tenant_id,kind,email_inbox_id,dedupe_key,payload,replay_policy,next_attempt_at)
        VALUES(a.tenant_id,kind,i,'inbox:'||i::text,jsonb_build_object('inbox_id',i::text),'safe',clock_timestamp()+CASE WHEN kind='email_event' THEN interval '5 seconds' ELSE interval '0 seconds' END);END IF;
      RETURN 'accepted';END $$""")
    op.execute(f'REVOKE ALL ON FUNCTION {q}.email_webhook_accept(text,text,text,jsonb) FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.email_webhook_accept(text,text,text,jsonb) OWNER TO {ingress}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.email_webhook_accept(text,text,text,jsonb) TO {runtime}')
    fence=f"""SELECT tenant_id,id,case_id,action_draft_id,kind,state,lease_worker_id,lease_token,lease_generation,lease_until,email_inbox_id,attempts,max_attempts INTO j FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid;
      IF NOT FOUND OR j.kind NOT IN('email_fetch','email_event') OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w OR j.lease_token IS DISTINCT FROM token OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN NULL;END IF;"""
    functions=[('email_inbox_snapshot(uuid,uuid,uuid,uuid,bigint)',f"""CREATE FUNCTION {q}.email_inbox_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE j record;result jsonb;BEGIN {fence}
      SELECT jsonb_build_object('id',i.id,'event_type',i.event_type,'event_at',i.event_at,'provider_message_id',i.provider_message_id,'processing_state',i.processing_state,
        'provider_account_ref',a.provider_account_ref,'inbound_address',a.inbound_address,'attempts',j.attempts,'max_attempts',j.max_attempts,
        'existing_message_id',(SELECT e.id FROM {q}.email_messages e WHERE e.tenant_id=t AND e.email_account_id=a.id AND e.direction='inbound' AND e.provider_message_id=i.provider_message_id)) INTO result
      FROM {q}.email_inbox i JOIN {q}.email_accounts a ON a.tenant_id=i.tenant_id AND a.id=i.email_account_id AND a.active JOIN {q}.tenants tn ON tn.id=i.tenant_id AND tn.active WHERE i.tenant_id=t AND i.id=j.email_inbox_id;RETURN result;END $$"""),
      ('email_inbox_bind(uuid,uuid,uuid,uuid,bigint)',f"""CREATE FUNCTION {q}.email_inbox_bind(t uuid,jid uuid,w uuid,token uuid,generation bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE snap jsonb;i record;e record;BEGIN
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      snap:={q}.email_inbox_snapshot(t,jid,w,token,generation);IF snap IS NULL THEN RETURN NULL;END IF;
      SELECT * INTO i FROM {q}.email_inbox WHERE tenant_id=t AND id=(snap->>'id')::uuid FOR UPDATE;
      SELECT m.id,m.case_id,src.action_draft_id INTO e FROM {q}.email_messages m JOIN {q}.outbox_jobs src ON src.tenant_id=m.tenant_id AND src.id=m.job_id
        WHERE m.tenant_id=t AND m.email_account_id=i.email_account_id AND m.provider_message_id=i.provider_message_id AND m.direction='outbound';
      IF NOT FOUND OR i.event_type='email.received' THEN RETURN NULL;END IF;
      UPDATE {q}.outbox_jobs SET case_id=e.case_id,action_draft_id=e.action_draft_id,email_message_id=e.id WHERE tenant_id=t AND id=jid AND kind='email_event';
      UPDATE {q}.email_inbox SET email_message_id=e.id WHERE tenant_id=t AND id=i.id;
      IF {q}.email_inbox_snapshot(t,jid,w,token,generation) IS NULL THEN RAISE EXCEPTION 'Inbox lease expired' USING ERRCODE='Z0017';END IF;
      RETURN jsonb_build_object('case_id',e.case_id,'draft_id',e.action_draft_id);END $$"""),
      ('email_inbox_complete(uuid,uuid,uuid,uuid,bigint,jsonb,text)',f"""CREATE FUNCTION {q}.email_inbox_complete(t uuid,jid uuid,w uuid,token uuid,generation bigint,content jsonb,reason text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE snap jsonb;i record;e record;message uuid;BEGIN
      IF reason IS NOT NULL AND reason NOT IN('FETCH_UNAVAILABLE','UNMATCHED_RECEIPT') THEN RAISE EXCEPTION 'Invalid review' USING ERRCODE='22023';END IF;
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      snap:={q}.email_inbox_snapshot(t,jid,w,token,generation);IF snap IS NULL THEN RETURN false;END IF;
      SELECT * INTO i FROM {q}.email_inbox WHERE tenant_id=t AND id=(snap->>'id')::uuid FOR UPDATE;
      IF {q}.email_inbox_snapshot(t,jid,w,token,generation) IS NULL THEN RETURN false;END IF;
      IF i.processing_state IN('processed','review','ignored') THEN RETURN true;END IF;
      IF reason IS NULL AND i.event_type='email.received' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(i.email_account_id::text||':inbound:'||i.provider_message_id,0));
        SELECT id,content_digest INTO e FROM {q}.email_messages WHERE tenant_id=t AND email_account_id=i.email_account_id AND direction='inbound' AND provider_message_id=i.provider_message_id;
        IF FOUND THEN message:=e.id;IF content IS NOT NULL AND content->>'digest' IS DISTINCT FROM e.content_digest THEN reason:='CONTENT_CHANGED';END IF;
        ELSIF content IS NULL THEN RETURN false;
        ELSE
          IF content->>'recipient' IS DISTINCT FROM snap->>'inbound_address' OR content->>'sender' IS NULL OR content->>'digest' IS NULL THEN RETURN false;END IF;
          INSERT INTO {q}.email_messages(tenant_id,case_id,app_message_id,email_account_id,job_id,direction,recipient_contact_id,recipient_principal_id,
            sender_address,recipient_address,reply_address,requested_rfc_message_id,delivery_key,subject,body,body_html,rfc_message_id,in_reply_to,references_header,inbound_reply_to,content_digest,attachment_count,inbound_authentication,verification_state,delivery_status,provider_message_id)
          VALUES(t,NULL,NULL,i.email_account_id,jid,'inbound',NULL,NULL,content->>'sender',content->>'recipient',NULL,NULL,NULL,content->>'subject',COALESCE(content->>'text',''),content->>'html',
            content->>'rfc_message_id',content->>'in_reply_to',content->>'references',content->>'reply_to',content->>'digest',(content->>'attachment_count')::integer,content->'authentication','pending',NULL,i.provider_message_id) RETURNING id INTO message;
        END IF;
      ELSIF reason IS NULL THEN
        SELECT id INTO e FROM {q}.email_messages WHERE tenant_id=t AND id=i.email_message_id AND email_account_id=i.email_account_id AND direction='outbound' AND provider_message_id=i.provider_message_id;
        IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM {q}.email_delivery_events d WHERE d.tenant_id=t AND d.email_message_id=e.id AND d.provider_event_at=i.event_at AND d.state=CASE i.event_type WHEN 'email.sent' THEN 'accepted' WHEN 'email.delivery_delayed' THEN 'accepted' WHEN 'email.delivered' THEN 'delivered' WHEN 'email.bounced' THEN 'bounced' ELSE 'failed' END) THEN RETURN false;END IF;
        message:=e.id;
      END IF;
      UPDATE {q}.email_inbox SET processing_state=CASE WHEN reason IS NULL THEN 'processed' ELSE 'review' END,review_reason=reason,email_message_id=COALESCE(message,email_message_id) WHERE tenant_id=t AND id=i.id;
      IF {q}.email_inbox_snapshot(t,jid,w,token,generation) IS NULL THEN RAISE EXCEPTION 'Inbox lease expired' USING ERRCODE='Z0017';END IF;RETURN true;END $$""")]
    for signature,definition in functions:
        op.execute(definition);op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC');op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {mail}');op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {worker}')
    # Event jobs bind a known account-scoped receipt, not the unique polling child.
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.mail_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)',)).scalar_one()
    op.execute(definition.replace("j.kind<>'email_reconcile'","j.kind NOT IN('email_reconcile','email_event')"))
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.job_claim(uuid,text[],integer)',)).scalar_one()
    routing=f"route.active AND (x.email_inbox_id IS NULL OR EXISTS(SELECT 1 FROM {q}.email_inbox ni WHERE ni.tenant_id=x.tenant_id AND ni.id=x.email_inbox_id AND ni.email_account_id=route.id)) AND"
    if definition.count('route.active AND')!=2:raise RuntimeError('Unexpected routing filter.')
    definition=definition.replace('route.active AND',routing)
    marker='x.kind=ANY(kinds)'
    busy=f"x.kind=ANY(kinds) AND (x.kind<>'email_event' OR NOT EXISTS(SELECT 1 FROM {q}.email_inbox bi JOIN {q}.email_messages bm ON bm.tenant_id=bi.tenant_id AND bm.email_account_id=bi.email_account_id AND bm.provider_message_id=bi.provider_message_id AND bm.direction='outbound' JOIN {q}.outbox_jobs bs ON bs.tenant_id=bm.tenant_id AND bs.id=bm.job_id WHERE bi.tenant_id=x.tenant_id AND bi.id=x.email_inbox_id AND bs.state='leased' AND bs.lease_until>clock_timestamp()))"
    op.execute(definition.replace(marker,busy))
    op.execute(f"""CREATE FUNCTION {q}.review_stopped_inbox() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.email_inbox_id IS NOT NULL AND NEW.state IN('unknown','dead','blocked') THEN UPDATE {q}.email_inbox SET processing_state='review',review_reason='WORKER_STOPPED' WHERE tenant_id=NEW.tenant_id AND id=NEW.email_inbox_id AND processing_state='received';END IF;RETURN NEW;END $$""")
    op.execute(f'REVOKE ALL ON FUNCTION {q}.review_stopped_inbox() FROM PUBLIC');op.execute(f'ALTER FUNCTION {q}.review_stopped_inbox() OWNER TO {mail}')
    op.execute(f'CREATE TRIGGER review_stopped_inbox AFTER UPDATE OF state ON {q}.outbox_jobs FOR EACH ROW EXECUTE FUNCTION {q}.review_stopped_inbox()')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {ingress},{mail}')


def downgrade():raise RuntimeError('Inbox data requires a reviewed recovery migration.')
