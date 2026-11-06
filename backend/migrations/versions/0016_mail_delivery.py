"""Monotone delivery evidence, durable review and read-only reconcile jobs."""
import hashlib
from alembic import context, op

revision='0016_mail_delivery'
down_revision='0015_outbound_mail'
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes;schema=attrs['schema']
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    name='airport_assistant_mail_owner' if schema=='app' else 'mail_owner_'+hashlib.sha256(schema.encode()).hexdigest()[:24]
    q,owner,worker,runtime,scheduler=(quote(v) for v in (schema,name,attrs['worker_role'],attrs['runtime_role'],attrs['job_owner_role']))
    checks=op.get_bind().exec_driver_sql('SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype=\'c\'',(schema+'.email_messages',)).all()
    receipt=[n for n,d in checks if 'provider_message_id IS NOT NULL' in d and 'delivery_status' in d]
    if len(receipt)!=1:raise RuntimeError('Unexpected receipt constraint.')
    op.execute(f'ALTER TABLE {q}.email_messages DROP CONSTRAINT {quote(receipt[0])},DROP CONSTRAINT email_messages_delivery_status_check')
    op.execute(f"""ALTER TABLE {q}.email_messages
      ADD CONSTRAINT email_delivery_state CHECK(delivery_status IN('sending','accepted','delivered','bounced','failed','delivery_unknown')),
      ADD CONSTRAINT email_receipt_shape CHECK((delivery_status NOT IN('accepted','delivered','bounced') OR provider_message_id IS NOT NULL) AND (delivery_status<>'sending' OR provider_message_id IS NULL)),
      ADD COLUMN delivery_version bigint NOT NULL DEFAULT 1 CHECK(delivery_version>0),
      ADD COLUMN provider_status_at timestamptz,ADD COLUMN last_checked_at timestamptz,
      ADD COLUMN needs_review boolean NOT NULL DEFAULT false,ADD COLUMN review_reason text,
      ADD CONSTRAINT email_review_reason CHECK(review_reason IN('NO_RECEIPT','LOOKUP_UNAVAILABLE','PENDING_DELIVERY','DELIVERY_FAILED','CONFLICTING_EVIDENCE')),
      ADD CONSTRAINT email_review_shape CHECK(needs_review=(review_reason IS NOT NULL)),ADD UNIQUE(tenant_id,id,case_id)""")
    op.execute(f"UPDATE {q}.email_messages SET needs_review=true,review_reason=CASE WHEN delivery_status='failed' THEN 'DELIVERY_FAILED' ELSE 'NO_RECEIPT' END WHERE delivery_status IN('delivery_unknown','failed')")
    op.execute(f"ALTER TABLE {q}.outbox_jobs DROP CONSTRAINT job_kind,ADD CONSTRAINT job_kind CHECK(kind IN('case_created','email_send','email_reconcile')),ADD COLUMN email_message_id uuid,ADD CONSTRAINT reconcile_job_shape CHECK((kind='email_reconcile')=(email_message_id IS NOT NULL)),ADD FOREIGN KEY(tenant_id,email_message_id,case_id) REFERENCES {q}.email_messages(tenant_id,id,case_id)")
    op.execute(f"CREATE UNIQUE INDEX email_reconcile_job ON {q}.outbox_jobs(tenant_id,email_message_id) WHERE kind='email_reconcile'")
    op.execute(f"GRANT SELECT(action_draft_id,attempts,max_attempts,email_message_id) ON {q}.outbox_jobs TO {owner}")
    op.execute(f"GRANT INSERT(tenant_id,case_id,action_draft_id,kind,email_message_id,dedupe_key,payload,replay_policy,next_attempt_at) ON {q}.outbox_jobs TO {owner}")
    op.execute(f"CREATE POLICY mail_reconcile_insert ON {q}.outbox_jobs FOR INSERT TO {owner} WITH CHECK(kind='email_reconcile')")
    op.execute(f"GRANT UPDATE(rfc_message_id,delivery_version,provider_status_at,last_checked_at,needs_review,review_reason) ON {q}.email_messages TO {owner}")
    op.execute(f"""CREATE TABLE {q}.email_delivery_events(tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),case_id uuid NOT NULL,email_message_id uuid NOT NULL,
      observation_key text NOT NULL CHECK(observation_key ~ '^[a-f0-9]{{64}}$'),state text NOT NULL CHECK(state IN('accepted','delivered','bounced','failed','delivery_unknown')),
      provider_event_at timestamptz,observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),review_reason text,
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,email_message_id,observation_key),FOREIGN KEY(tenant_id,email_message_id,case_id) REFERENCES {q}.email_messages(tenant_id,id,case_id),
      CHECK(review_reason IS NULL OR review_reason IN('NO_RECEIPT','LOOKUP_UNAVAILABLE','PENDING_DELIVERY','DELIVERY_FAILED','CONFLICTING_EVIDENCE')))""")
    op.execute(f'ALTER TABLE {q}.email_delivery_events ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {q}.email_delivery_events FORCE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON {q}.email_delivery_events FROM PUBLIC')
    op.execute(f'GRANT SELECT,INSERT ON {q}.email_delivery_events TO {owner}')
    op.execute(f'CREATE POLICY mail_event_read ON {q}.email_delivery_events FOR SELECT TO {owner} USING(true)')
    op.execute(f'CREATE POLICY mail_event_insert ON {q}.email_delivery_events FOR INSERT TO {owner} WITH CHECK(true)')
    op.execute(f'GRANT SELECT(tenant_id,id,job_id) ON {q}.email_messages TO {scheduler}')
    op.execute(f'CREATE POLICY scheduler_email_metadata ON {q}.email_messages FOR SELECT TO {scheduler} USING(true)')
    # Staff status visibility follows current case scope; no recipient/body/receipt exposure.
    op.execute(f'GRANT SELECT(tenant_id,id,case_id,job_id,app_message_id,delivery_status,delivery_version,needs_review,review_reason,provider_status_at,last_checked_at) ON {q}.email_messages TO {runtime}')
    op.execute(f'CREATE POLICY staff_email_status_read ON {q}.email_messages FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND {q}.case_staff_session_valid() AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=email_messages.tenant_id AND c.id=email_messages.case_id))')
    op.execute(f"CREATE POLICY staff_delivery_job_read ON {q}.outbox_jobs FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND {q}.case_staff_session_valid() AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=outbox_jobs.tenant_id AND c.id=outbox_jobs.case_id))")
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {owner}')
    op.execute(f"""CREATE FUNCTION {q}.queue_email_reconcile() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      INSERT INTO {q}.outbox_jobs(tenant_id,case_id,action_draft_id,kind,email_message_id,dedupe_key,payload,replay_policy,next_attempt_at)
        SELECT NEW.tenant_id,NEW.case_id,j.action_draft_id,'email_reconcile',NEW.id,'delivery:'||NEW.id::text,
          jsonb_build_object('case_id',NEW.case_id::text,'draft_id',j.action_draft_id::text),'safe',clock_timestamp()+interval '60 seconds'
        FROM {q}.outbox_jobs j WHERE j.tenant_id=NEW.tenant_id AND j.id=NEW.job_id;
      IF NOT FOUND THEN RAISE EXCEPTION 'Missing send origin' USING ERRCODE='23514';END IF;RETURN NEW;END $$""")
    op.execute(f'REVOKE ALL ON FUNCTION {q}.queue_email_reconcile() FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.queue_email_reconcile() OWNER TO {owner}')
    op.execute(f'CREATE TRIGGER queue_email_reconcile AFTER INSERT ON {q}.email_messages FOR EACH ROW EXECUTE FUNCTION {q}.queue_email_reconcile()')
    op.execute(f"""INSERT INTO {q}.outbox_jobs(tenant_id,case_id,action_draft_id,kind,email_message_id,dedupe_key,payload,replay_policy,next_attempt_at)
      SELECT e.tenant_id,e.case_id,j.action_draft_id,'email_reconcile',e.id,'delivery:'||e.id::text,
        jsonb_build_object('case_id',e.case_id::text,'draft_id',j.action_draft_id::text),'safe',clock_timestamp()+interval '60 seconds'
      FROM {q}.email_messages e JOIN {q}.outbox_jobs j ON j.tenant_id=e.tenant_id AND j.id=e.job_id""")
    # A reconcile job is invisible to claim/reaper while the original POST lease is live.
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.job_claim(uuid,text[],integer)',)).scalar_one()
    selector=f"x.kind=ANY(kinds) AND (x.kind<>'email_reconcile' OR EXISTS(SELECT 1 FROM {q}.email_messages em JOIN {q}.outbox_jobs src ON src.tenant_id=em.tenant_id AND src.id=em.job_id WHERE em.tenant_id=x.tenant_id AND em.id=x.email_message_id AND (src.state<>'leased' OR src.lease_until<=clock_timestamp())))"
    if definition.count('x.kind=ANY(kinds)')!=2:raise RuntimeError('Unexpected account-scoped scheduler.')
    op.execute(definition.replace('x.kind=ANY(kinds)',selector))
    fence=f"""SELECT tenant_id,id,case_id,kind,state,lease_worker_id,lease_token,lease_generation,lease_until,email_message_id,attempts,max_attempts INTO j
      FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid;
      IF NOT FOUND OR j.kind<>'email_reconcile' OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w OR j.lease_token IS DISTINCT FROM token
        OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN NULL;END IF;"""
    functions=[('mail_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)',f"""CREATE FUNCTION {q}.mail_delivery_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE j record;result jsonb;BEGIN {fence}
      SELECT jsonb_build_object('tenant_id',e.tenant_id,'case_id',e.case_id,'id',e.id,'delivery_status',e.delivery_status,'delivery_version',e.delivery_version,'provider_status_at',e.provider_status_at,
        'provider_message_id',e.provider_message_id,'provider_account_ref',a.provider_account_ref,'sender',e.sender_address,'recipient',e.recipient_address,
        'attempts',j.attempts,'max_attempts',j.max_attempts,'source_busy',src.state='leased' AND src.lease_until>clock_timestamp()) INTO result
      FROM {q}.email_messages e JOIN {q}.email_accounts a ON a.tenant_id=e.tenant_id AND a.id=e.email_account_id AND a.active
      JOIN {q}.tenants tn ON tn.id=e.tenant_id AND tn.active JOIN {q}.outbox_jobs src ON src.tenant_id=e.tenant_id AND src.id=e.job_id
      WHERE e.tenant_id=t AND e.id=j.email_message_id AND e.case_id=j.case_id;RETURN result;END $$"""),
      ('mail_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)',f"""CREATE FUNCTION {q}.mail_delivery_observe(t uuid,jid uuid,w uuid,token uuid,generation bigint,eid uuid,expected_version bigint,evidence jsonb,reason text)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE snap jsonb;e {q}.email_messages%ROWTYPE;incoming text;event_time timestamptz;key text;next_state text;conflict boolean:=false;BEGIN
      IF reason IS NOT NULL AND reason NOT IN('NO_RECEIPT','LOOKUP_UNAVAILABLE','PENDING_DELIVERY') THEN RAISE EXCEPTION 'Invalid review reason' USING ERRCODE='22023';END IF;
      PERFORM 1 FROM {q}.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
      snap:={q}.mail_delivery_snapshot(t,jid,w,token,generation);
      IF snap IS NULL OR (snap->>'id')::uuid IS DISTINCT FROM eid OR (snap->>'source_busy')::boolean THEN RETURN false;END IF;
      SELECT * INTO e FROM {q}.email_messages WHERE tenant_id=t AND id=eid FOR UPDATE;
      snap:={q}.mail_delivery_snapshot(t,jid,w,token,generation);
      IF snap IS NULL OR (snap->>'id')::uuid IS DISTINCT FROM eid OR (snap->>'source_busy')::boolean OR e.delivery_version IS DISTINCT FROM expected_version THEN RETURN false;END IF;
      IF evidence IS NOT NULL THEN
        IF evidence->>'provider_message_id' IS DISTINCT FROM e.provider_message_id OR evidence->>'state' IS NULL OR evidence->>'state' NOT IN('accepted','delivered','bounced','failed')
          OR evidence->>'key' IS NULL OR evidence->>'key' !~ '^[a-f0-9]{{64}}$' THEN RAISE EXCEPTION 'Invalid receipt evidence' USING ERRCODE='22023';END IF;
        incoming:=evidence->>'state';event_time:=(evidence->>'event_at')::timestamptz;key:=evidence->>'key';
        IF event_time>clock_timestamp()+interval '60 seconds' OR (evidence->>'rfc_message_id' IS NOT NULL AND (length(evidence->>'rfc_message_id')>998 OR evidence->>'rfc_message_id' !~ '^<[^<>[:space:][:cntrl:]]+>$')) THEN RAISE EXCEPTION 'Invalid provider metadata' USING ERRCODE='22023';END IF;
      ELSE
        IF reason IS NULL OR (reason='NO_RECEIPT') IS DISTINCT FROM (e.provider_message_id IS NULL) THEN RETURN false;END IF;
        incoming:=CASE WHEN e.delivery_status='sending' THEN 'delivery_unknown' ELSE e.delivery_status END;
        key:=encode(sha256(convert_to(e.id::text||':'||reason,'UTF8')),'hex');
      END IF;
      next_state:=incoming;
      IF event_time IS NOT NULL AND e.provider_status_at IS NOT NULL AND event_time<e.provider_status_at THEN next_state:=e.delivery_status;reason:=e.review_reason;
      ELSIF e.delivery_status IN('delivered','bounced','failed') THEN
        next_state:=e.delivery_status;conflict:=incoming IN('delivered','bounced','failed') AND incoming<>e.delivery_status;
        reason:=CASE WHEN conflict THEN 'CONFLICTING_EVIDENCE' ELSE e.review_reason END;
      ELSIF e.delivery_status='accepted' AND incoming='delivery_unknown' THEN next_state:='accepted';
      END IF;
      IF evidence IS NOT NULL AND e.rfc_message_id IS NOT NULL AND evidence->>'rfc_message_id' IS NOT NULL AND e.rfc_message_id<>evidence->>'rfc_message_id' THEN reason:='CONFLICTING_EVIDENCE';END IF;
      IF next_state IN('bounced','failed') AND reason IS NULL THEN reason:='DELIVERY_FAILED';END IF;
      IF reason IS NULL AND e.needs_review AND (next_state NOT IN('delivered','bounced','failed') OR e.review_reason='CONFLICTING_EVIDENCE') THEN reason:=e.review_reason;END IF;
      IF reason IS NOT NULL AND reason IS DISTINCT FROM e.review_reason AND EXISTS(SELECT 1 FROM {q}.email_delivery_events WHERE tenant_id=t AND email_message_id=eid AND observation_key=key) THEN key:=encode(sha256(convert_to('review:'||key||':'||reason,'UTF8')),'hex');END IF;
      INSERT INTO {q}.email_delivery_events(tenant_id,case_id,email_message_id,observation_key,state,provider_event_at,review_reason) VALUES(t,e.case_id,eid,key,incoming,event_time,reason) ON CONFLICT(tenant_id,email_message_id,observation_key) DO NOTHING;
      UPDATE {q}.email_messages SET delivery_status=next_state,completed_at=COALESCE(completed_at,clock_timestamp()),delivery_version=delivery_version+1,
        rfc_message_id=COALESCE(rfc_message_id,evidence->>'rfc_message_id'),last_checked_at=clock_timestamp(),
        provider_status_at=CASE WHEN next_state=incoming AND event_time IS NOT NULL AND (provider_status_at IS NULL OR event_time>provider_status_at) THEN event_time ELSE provider_status_at END,
        needs_review=reason IS NOT NULL,review_reason=reason WHERE tenant_id=t AND id=eid;
      IF {q}.mail_delivery_snapshot(t,jid,w,token,generation) IS NULL THEN RAISE EXCEPTION 'Delivery lease expired' USING ERRCODE='Z0016';END IF;
      RETURN true;END $$""")]
    for signature,definition in functions:
        op.execute(definition);op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC')
        op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {owner}');op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {worker}')
    # Failed/uncertain POST outcomes remain visible before the reconcile worker runs.
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.mail_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text)',)).scalar_one()
    original='SET delivery_status=result,provider_message_id=receipt,completed_at=clock_timestamp()'
    if original not in definition:raise RuntimeError('Unexpected completion definition.')
    op.execute(definition.replace(original,"SET delivery_status=result,provider_message_id=receipt,completed_at=clock_timestamp(),delivery_version=delivery_version+1,needs_review=result<>'accepted',review_reason=CASE result WHEN 'failed' THEN 'DELIVERY_FAILED' WHEN 'delivery_unknown' THEN 'NO_RECEIPT' ELSE NULL END"))
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def downgrade():raise RuntimeError('Delivery state requires a reviewed recovery migration.')
