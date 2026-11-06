CREATE FUNCTION @SCHEMA@.mail_private_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j record;result jsonb;required jsonb;BEGIN
  SELECT tenant_id,id,case_id,action_draft_id,kind,state,source_message_id,lease_worker_id,lease_token,lease_generation,lease_until,resource_security_labels
    INTO j FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid;
  IF NOT FOUND OR j.kind NOT IN('case_created','email_send') OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w
    OR j.lease_token IS DISTINCT FROM token OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN NULL;END IF;
  SELECT jsonb_build_object('classification_version',2,'tenant_id',t,'case_id',c.id,'job_id',j.id,'draft_id',j.action_draft_id,
    'worker_id',w,'lease_token',token,'lease_generation',generation,'lease_valid',true,'job_kind',j.kind,
    'server_now',clock_timestamp(),'lease_until',j.lease_until,'case_version',c.version,'source_message_version',m.sequence,
    'public_reference',c.public_reference,'language',CASE WHEN j.kind='email_send' THEN m.language ELSE c.language END,
    'app_message_id',m.id,'body',CASE WHEN j.kind='email_send' THEN m.body ELSE NULL END,
    'message_hash',CASE WHEN j.kind='email_send' THEN encode(sha256(convert_to(m.body,'UTF8')),'hex') ELSE NULL END,
    'owner_principal_id',c.owner_principal_id,'followup_contact_id',c.followup_contact_id,'contact_id',ct.id,'principal_id',ct.principal_id,
    'recipient',ct.email_address,'sender',a.sending_identity,'reply_to',a.reply_address,'account_id',a.id,'provider_account_ref',a.provider_account_ref,
    'tenant_active',tn.active,'account_active',a.active,'principal_active',p.active,'verified',ct.verified_at IS NOT NULL,
    'participant_active',cp.principal_id IS NOT NULL AND cp.revoked_at IS NULL,
    'case_labels',c.resource_security_labels,'conversation_labels',@SCHEMA@.source_labels_union(ARRAY[v.resource_security_labels,coalesce(mv.resource_security_labels,'[]')]),
    'draft_labels',d.resource_security_labels,'message_labels',m.resource_security_labels,'job_labels',j.resource_security_labels,
    'email_labels',coalesce(e.resource_security_labels,'[]'),'reconcile_labels',coalesce(r.resource_security_labels,'[]'),
    'email_id',e.id,'delivery_status',e.delivery_status,'delivery_version',CASE WHEN e.id IS NOT NULL THEN e.delivery_version ELSE NULL END,
    'envelope_hash',e.envelope_hash,'reconcile_job_id',r.id,'reconcile_email_id',r.email_message_id)
    INTO result
    FROM @SCHEMA@.cases c JOIN @SCHEMA@.tenants tn ON tn.id=c.tenant_id
    JOIN @SCHEMA@.action_drafts d ON(d.tenant_id,d.id)=(c.tenant_id,c.origin_draft_id)
    JOIN @SCHEMA@.conversations v ON(v.tenant_id,v.id)=(c.tenant_id,c.origin_conversation_id)
    JOIN @SCHEMA@.messages m ON(m.tenant_id,m.id)=(c.tenant_id,coalesce(j.source_message_id,c.initial_message_id))
    LEFT JOIN @SCHEMA@.conversations mv ON(mv.tenant_id,mv.id)=(m.tenant_id,m.conversation_id)
    JOIN @SCHEMA@.contacts ct ON(ct.tenant_id,ct.id)=(c.tenant_id,c.followup_contact_id) AND ct.id=c.owner_contact_id
    JOIN @SCHEMA@.principals p ON(p.tenant_id,p.id)=(ct.tenant_id,ct.principal_id)
    LEFT JOIN @SCHEMA@.case_participants cp ON(cp.tenant_id,cp.case_id,cp.principal_id)=(c.tenant_id,c.id,ct.principal_id)
    JOIN @SCHEMA@.email_accounts a ON a.tenant_id=c.tenant_id AND a.provider='resend'
    LEFT JOIN @SCHEMA@.email_messages e ON(e.tenant_id,e.job_id)=(c.tenant_id,j.id) AND e.direction='outbound'
    LEFT JOIN @SCHEMA@.outbox_jobs r ON(r.tenant_id,r.email_message_id)=(e.tenant_id,e.id) AND r.kind='email_reconcile'
    WHERE c.tenant_id=t AND c.id=j.case_id AND c.origin_draft_id=j.action_draft_id
      AND(j.kind='case_created' AND m.id=c.initial_message_id OR j.kind='email_send' AND m.author_type='staff' AND m.delivery_channel='email' AND mv.case_id=c.id);
  IF result IS NULL THEN RETURN NULL;END IF;
  required:=@SCHEMA@.source_labels_union(ARRAY[result->'case_labels',result->'conversation_labels',result->'draft_labels',result->'message_labels',result->'job_labels',result->'email_labels',result->'reconcile_labels']);
  -- A queued immutable case may not silently acquire new source facts.
  IF required IS DISTINCT FROM result->'case_labels' THEN RETURN NULL;END IF;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RETURN NULL;END IF;
  RETURN result||jsonb_build_object('labels',required,'server_now',clock_timestamp());END $$;

CREATE FUNCTION @SCHEMA@.mail_private_begin(t uuid,jid uuid,w uuid,token uuid,generation bigint,expected jsonb,envelope jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE snap jsonb;result jsonb;plain text;subject_line text;reply text;cid uuid;BEGIN
  SELECT case_id INTO cid FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid;
  PERFORM id FROM @SCHEMA@.cases WHERE tenant_id=t AND id=cid FOR UPDATE;
  PERFORM v.id FROM @SCHEMA@.conversations v WHERE v.tenant_id=t AND(v.id=(SELECT origin_conversation_id FROM @SCHEMA@.cases WHERE tenant_id=t AND id=cid)
    OR v.id=(SELECT m.conversation_id FROM @SCHEMA@.messages m JOIN @SCHEMA@.outbox_jobs j ON(j.tenant_id,j.source_message_id)=(m.tenant_id,m.id) WHERE j.tenant_id=t AND j.id=jid)) ORDER BY v.id FOR UPDATE;
  PERFORM id FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
  snap:=@SCHEMA@.mail_private_snapshot(t,jid,w,token,generation);
  IF snap IS NULL OR(snap-ARRAY['server_now','lease_until']) IS DISTINCT FROM(expected-ARRAY['server_now','lease_until'])
    OR snap->>'delivery_status' IS NOT NULL THEN RETURN 'false'::jsonb;END IF;
  IF snap->>'tenant_active'<>'true' OR snap->>'account_active'<>'true' OR snap->>'principal_active'<>'true' OR snap->>'verified'<>'true'
    OR snap->>'participant_active'<>'true' OR snap->>'principal_id' IS DISTINCT FROM snap->>'owner_principal_id' THEN RETURN 'false'::jsonb;END IF;
  IF jsonb_typeof(envelope)<>'object' OR NOT envelope ?& ARRAY['id','app_message_id','account_id','sender','recipient','reply_to','rfc_message_id','delivery_key','subject','text','provider_account_ref','reply_token_hash']
    OR envelope-ARRAY['id','app_message_id','account_id','sender','recipient','reply_to','rfc_message_id','delivery_key','subject','text','provider_account_ref','reply_token_hash']<>'{}'::jsonb THEN RETURN 'false'::jsonb;END IF;
  IF envelope->>'sender' IS DISTINCT FROM snap->>'sender' OR envelope->>'recipient' IS DISTINCT FROM snap->>'recipient'
    OR envelope->>'app_message_id' IS DISTINCT FROM snap->>'app_message_id' OR envelope->>'account_id' IS DISTINCT FROM snap->>'account_id'
    OR envelope->>'provider_account_ref' IS DISTINCT FROM snap->>'provider_account_ref' THEN RETURN 'false'::jsonb;END IF;
  reply:=snap->>'reply_to';
  IF envelope->>'reply_token_hash' IS NOT NULL THEN
    IF envelope->>'reply_token_hash' !~ '^[a-f0-9]{64}$' OR envelope->>'reply_to' !~ '^[^@]+\+r-[A-Za-z0-9_-]{32}@[^@]+$'
      OR split_part(envelope->>'reply_to','+r-',1)<>split_part(reply,'@',1) OR split_part(envelope->>'reply_to','@',2)<>split_part(reply,'@',2)
      OR encode(sha256(convert_to(split_part(split_part(envelope->>'reply_to','+r-',2),'@',1),'UTF8')),'hex')<>envelope->>'reply_token_hash' THEN RETURN 'false'::jsonb;END IF;
  ELSIF envelope->>'reply_to' IS DISTINCT FROM reply THEN RETURN 'false'::jsonb;END IF;
  IF snap->>'job_kind'='email_send' THEN
    plain:=snap->>'body';subject_line:=CASE WHEN snap->>'language'='en' THEN 'Reply to your support request' ELSE 'Balasan permintaan bantuan Anda' END;
  ELSIF snap->>'language'='en' THEN
    subject_line:='Your support request was received';plain:='We received your request. Your case reference is '||(snap->>'public_reference')||'. Our customer service team will follow up by email.';
  ELSE subject_line:='Permintaan bantuan Anda sudah diterima';plain:='Permintaan Anda sudah diterima. Nomor kasus Anda adalah '||(snap->>'public_reference')||'. Tim layanan pelanggan akan menindaklanjuti melalui email.';END IF;
  IF envelope->>'reply_token_hash' IS NOT NULL THEN plain:=plain||E'\n\nIf this case is closed and the same issue continues, start your reply with CONTINUE SAME ISSUE.';END IF;
  IF envelope->>'subject' IS DISTINCT FROM subject_line OR envelope->>'text' IS DISTINCT FROM plain
    OR envelope->>'delivery_key' IS DISTINCT FROM 'mail/'||replace(t::text,'-','')||'/'||replace(envelope->>'id','-','')
    OR envelope->>'rfc_message_id' IS DISTINCT FROM '<'||replace(envelope->>'id','-','')||'@'||split_part(snap->>'sender','@',2)||'>' THEN RETURN 'false'::jsonb;END IF;
  INSERT INTO @SCHEMA@.email_messages(tenant_id,id,case_id,app_message_id,email_account_id,job_id,recipient_contact_id,recipient_principal_id,
    sender_address,recipient_address,reply_address,requested_rfc_message_id,delivery_key,subject,body,reply_token_hash,resource_security_labels,approved_envelope,envelope_hash)
    VALUES(t,(envelope->>'id')::uuid,cid,(snap->>'app_message_id')::uuid,(snap->>'account_id')::uuid,jid,(snap->>'contact_id')::uuid,(snap->>'principal_id')::uuid,
      envelope->>'sender',envelope->>'recipient',envelope->>'reply_to',envelope->>'rfc_message_id',envelope->>'delivery_key',envelope->>'subject',envelope->>'text',
      envelope->>'reply_token_hash',snap->'labels',envelope,@SCHEMA@.mail_fingerprint(envelope));
  SET CONSTRAINTS ALL IMMEDIATE;
  result:=@SCHEMA@.mail_private_snapshot(t,jid,w,token,generation);
  IF result IS NULL OR result->>'email_id' IS DISTINCT FROM envelope->>'id' OR result->>'reconcile_email_id' IS DISTINCT FROM envelope->>'id'
    OR result->>'delivery_status'<>'sending' OR result->>'envelope_hash'<>@SCHEMA@.mail_fingerprint(envelope) THEN RAISE EXCEPTION 'Private reservation graph invalid or expired' USING ERRCODE='23514';END IF;
  SET CONSTRAINTS ALL DEFERRED;RETURN result;END $$;

CREATE FUNCTION @SCHEMA@.mail_private_send_snapshot(t uuid,jid uuid,w uuid,token uuid,generation bigint,eid uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE snap jsonb;BEGIN
  snap:=@SCHEMA@.mail_private_snapshot(t,jid,w,token,generation);
  IF snap->>'email_id' IS DISTINCT FROM eid::text OR snap->>'delivery_status'<>'sending' THEN RETURN NULL;END IF;
  RETURN snap;END $$;

CREATE FUNCTION @SCHEMA@.mail_private_abort_unsent(t uuid,jid uuid,w uuid,token uuid,generation bigint,eid uuid,attempt_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
  PERFORM id FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND kind IN('case_created','email_send') AND state='leased'
    AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RETURN false;END IF;
  PERFORM id FROM @SCHEMA@.email_messages WHERE tenant_id=t AND id=eid AND job_id=jid AND direction='outbound' AND envelope_hash=attempt_hash AND delivery_status='sending' AND provider_message_id IS NULL FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RETURN false;END IF;
  UPDATE @SCHEMA@.email_messages SET delivery_status='failed',completed_at=clock_timestamp(),delivery_version=delivery_version+1,needs_review=true,review_reason='DELIVERY_FAILED'
    WHERE tenant_id=t AND id=eid AND job_id=jid AND direction='outbound' AND envelope_hash=attempt_hash AND delivery_status='sending' AND provider_message_id IS NULL;
  IF NOT FOUND THEN RETURN false;END IF;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'Private abort fence expired' USING ERRCODE='23514';END IF;
  RETURN true;END $$;

CREATE FUNCTION @SCHEMA@.mail_private_complete(t uuid,jid uuid,w uuid,token uuid,generation bigint,eid uuid,attempt_hash text,outcome text,receipt text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ DECLARE e @SCHEMA@.email_messages%ROWTYPE;BEGIN
  IF outcome IS NULL OR outcome NOT IN('accepted','failed','delivery_unknown') OR(outcome='accepted') IS DISTINCT FROM(receipt IS NOT NULL)
    OR(receipt IS NOT NULL AND receipt !~ '^[A-Za-z0-9_-]{1,128}$') THEN RETURN false;END IF;
  PERFORM id FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND kind IN('case_created','email_send') AND state='leased'
    AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RETURN false;END IF;
  SELECT * INTO e FROM @SCHEMA@.email_messages WHERE tenant_id=t AND id=eid AND job_id=jid AND direction='outbound' AND envelope_hash=attempt_hash FOR UPDATE;
  IF NOT FOUND THEN RETURN false;END IF;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RETURN false;END IF;
  IF e.delivery_status<>'sending' THEN RETURN e.delivery_status=outcome AND e.provider_message_id IS NOT DISTINCT FROM receipt;END IF;
  UPDATE @SCHEMA@.email_messages SET delivery_status=outcome,provider_message_id=receipt,completed_at=clock_timestamp(),delivery_version=delivery_version+1,
    needs_review=outcome<>'accepted',review_reason=CASE outcome WHEN 'failed' THEN 'DELIVERY_FAILED' WHEN 'delivery_unknown' THEN 'NO_RECEIPT' ELSE NULL END
    WHERE tenant_id=t AND id=eid;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'Private completion fence expired' USING ERRCODE='23514';END IF;
  RETURN true;END $$;

REVOKE ALL ON FUNCTION @SCHEMA@.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint),@SCHEMA@.mail_private_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb),
  @SCHEMA@.mail_private_send_snapshot(uuid,uuid,uuid,uuid,bigint,uuid),@SCHEMA@.mail_private_abort_unsent(uuid,uuid,uuid,uuid,bigint,uuid,text),
  @SCHEMA@.mail_private_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text,text) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint) OWNER TO @MAIL@;
ALTER FUNCTION @SCHEMA@.mail_private_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb) OWNER TO @MAIL@;
ALTER FUNCTION @SCHEMA@.mail_private_send_snapshot(uuid,uuid,uuid,uuid,bigint,uuid) OWNER TO @MAIL@;
ALTER FUNCTION @SCHEMA@.mail_private_abort_unsent(uuid,uuid,uuid,uuid,bigint,uuid,text) OWNER TO @MAIL@;
ALTER FUNCTION @SCHEMA@.mail_private_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text,text) OWNER TO @MAIL@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint),@SCHEMA@.mail_private_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb),
  @SCHEMA@.mail_private_send_snapshot(uuid,uuid,uuid,uuid,bigint,uuid),@SCHEMA@.mail_private_abort_unsent(uuid,uuid,uuid,uuid,bigint,uuid,text),
  @SCHEMA@.mail_private_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text,text) TO @WORKER@;
