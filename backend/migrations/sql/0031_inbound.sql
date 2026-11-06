ALTER TABLE @SCHEMA@.email_reply_state ADD COLUMN private_command_hash text CHECK(private_command_hash ~ '^[a-f0-9]{64}$');
ALTER TABLE @SCHEMA@.email_reply_challenges ADD COLUMN source_content_digest text CHECK(source_content_digest ~ '^[a-f0-9]{64}$'),
  ADD COLUMN pending_text_hash text CHECK(pending_text_hash ~ '^[a-f0-9]{64}$'),
  ADD CONSTRAINT private_challenge_ttl CHECK(resource_security_labels='[]'::jsonb OR(expires_at>created_at AND expires_at<=created_at+interval '5 minutes'));
GRANT UPDATE(resource_security_labels) ON @SCHEMA@.email_messages TO @REPLY@;
CREATE FUNCTION @SCHEMA@.guard_private_challenge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source_digest text;body text;BEGIN
  IF TG_OP='UPDATE' AND OLD.resource_security_labels<>'[]'::jsonb AND
    (to_jsonb(NEW)-ARRAY['attempts','state','send_state','provider_message_id']) IS DISTINCT FROM(to_jsonb(OLD)-ARRAY['attempts','state','send_state','provider_message_id'])
  THEN RAISE EXCEPTION 'Private challenge binding is immutable' USING ERRCODE='23514';END IF;
  IF TG_OP='INSERT' AND NEW.resource_security_labels<>'[]'::jsonb THEN
    IF NEW.created_at>clock_timestamp() OR NEW.expires_at<=NEW.created_at THEN RAISE EXCEPTION 'Invalid private challenge clock' USING ERRCODE='23514';END IF;
    NEW.expires_at:=least(NEW.expires_at,NEW.created_at+interval '5 minutes');
    SELECT e.content_digest,r.pending_text INTO source_digest,body FROM @SCHEMA@.email_messages e
      JOIN @SCHEMA@.email_reply_state r ON(r.tenant_id,r.email_id)=(e.tenant_id,e.id)
      WHERE e.tenant_id=NEW.tenant_id AND e.id=NEW.email_id AND e.email_account_id=NEW.email_account_id
        AND r.case_id=NEW.case_id AND r.principal_id=NEW.principal_id AND e.sender_address=NEW.recipient;
    IF source_digest IS NULL OR body IS NULL THEN RAISE EXCEPTION 'Private challenge source required' USING ERRCODE='23514';END IF;
    NEW.source_content_digest:=source_digest;NEW.pending_text_hash:=encode(sha256(convert_to(body,'UTF8')),'hex');
  END IF;RETURN NEW;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_private_challenge() FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.guard_private_challenge() OWNER TO @REPLY@;
CREATE TRIGGER c_private_challenge BEFORE INSERT OR UPDATE ON @SCHEMA@.email_reply_challenges FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_private_challenge();

CREATE FUNCTION @SCHEMA@.email_private_reply_candidate(t uuid,jid uuid,w uuid,token uuid,generation bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j @SCHEMA@.outbox_jobs%ROWTYPE;e @SCHEMA@.email_messages%ROWTYPE;source @SCHEMA@.email_messages%ROWTYPE;
  referenced @SCHEMA@.email_messages%ROWTYPE;r @SCHEMA@.email_reply_state%ROWTYPE;sr @SCHEMA@.email_reply_state%ROWTYPE;
  c @SCHEMA@.cases%ROWTYPE;ch @SCHEMA@.email_reply_challenges%ROWTYPE;contact record;active_account boolean;
  refs text[];token_cases uuid[];candidates uuid[];token_value text;cid uuid;required jsonb;raw jsonb;body text;
  conv_labels jsonb;draft_labels jsonb;message_labels jsonb;history_labels jsonb;mode text;challenge jsonb;BEGIN
  SELECT * INTO j FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid;
  IF NOT FOUND OR j.kind<>'email_reply' OR j.state<>'leased' OR j.lease_worker_id IS DISTINCT FROM w OR j.lease_token IS DISTINCT FROM token
    OR j.lease_generation IS DISTINCT FROM generation OR j.lease_until<=clock_timestamp() THEN RETURN NULL;END IF;
  SELECT x.* INTO e FROM @SCHEMA@.email_messages x JOIN @SCHEMA@.email_accounts a ON(a.tenant_id,a.id)=(x.tenant_id,x.email_account_id) AND a.active
    JOIN @SCHEMA@.tenants tn ON tn.id=x.tenant_id AND tn.active WHERE x.tenant_id=t AND x.id=j.reply_email_id AND x.direction='inbound' AND a.id=j.reply_account_id;
  IF NOT FOUND THEN RETURN NULL;END IF;
  SELECT * INTO r FROM @SCHEMA@.email_reply_state WHERE tenant_id=t AND email_id=e.id;
  IF NOT FOUND THEN RETURN NULL;END IF;
  source:=e;
  IF e.verification_challenge_id IS NOT NULL THEN
    SELECT * INTO ch FROM @SCHEMA@.email_reply_challenges WHERE tenant_id=t AND id=e.verification_challenge_id AND email_account_id=e.email_account_id;
    IF NOT FOUND OR(position('+v-' in coalesce(e.inbound_to,''))>0 AND substring(e.inbound_to from '\+v-([a-f0-9]{32})@') IS DISTINCT FROM replace(ch.id::text,'-','')) THEN RETURN NULL;END IF;
    SELECT * INTO source FROM @SCHEMA@.email_messages WHERE tenant_id=t AND id=ch.email_id AND direction='inbound' AND email_account_id=e.email_account_id;
    IF NOT FOUND OR source.id=e.id THEN RETURN NULL;END IF;
  END IF;
  token_value:=substring(source.inbound_to from '\+r-([A-Za-z0-9_-]{32})@');
  IF position('+r-' in coalesce(source.inbound_to,''))>0 THEN
    SELECT array_agg(DISTINCT case_id) INTO token_cases FROM @SCHEMA@.email_messages WHERE tenant_id=t AND email_account_id=e.email_account_id
      AND direction='outbound' AND reply_token_hash=encode(sha256(convert_to(coalesce(token_value,''),'UTF8')),'hex');
    IF cardinality(token_cases) IS DISTINCT FROM 1 THEN RETURN NULL;END IF;
  END IF;
  SELECT array_agg(m[1]) INTO refs FROM regexp_matches(coalesce(source.in_reply_to,'')||' '||coalesce(source.references_header,''),'<[^<>[:space:]]+>','g') m;
  IF coalesce(cardinality(refs),0)>20 THEN RETURN NULL;END IF;
  SELECT array_agg(DISTINCT case_id) INTO candidates FROM @SCHEMA@.email_messages WHERE tenant_id=t AND email_account_id=e.email_account_id AND direction='outbound'
    AND(coalesce(rfc_message_id,requested_rfc_message_id)=ANY(coalesce(refs,ARRAY[]::text[])) OR case_id=ANY(coalesce(token_cases,ARRAY[]::uuid[])));
  IF cardinality(candidates) IS DISTINCT FROM 1 THEN RETURN NULL;END IF;cid:=candidates[1];
  IF e.verification_challenge_id IS NOT NULL AND ch.case_id<>cid THEN RETURN NULL;END IF;
  SELECT * INTO referenced FROM @SCHEMA@.email_messages WHERE tenant_id=t AND email_account_id=e.email_account_id AND direction='outbound' AND case_id=cid
    AND(coalesce(rfc_message_id,requested_rfc_message_id)=ANY(coalesce(refs,ARRAY[]::text[])) OR reply_token_hash=encode(sha256(convert_to(coalesce(token_value,''),'UTF8')),'hex')) ORDER BY id LIMIT 1;
  IF NOT FOUND THEN RETURN NULL;END IF;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=t AND id=cid;
  IF NOT FOUND OR(j.case_id IS NOT NULL AND j.case_id<>cid) THEN RETURN NULL;END IF;
  SELECT ct.id,ct.principal_id,ct.email_address,p.active,ct.verified_at IS NOT NULL AS verified,cp.revoked_at IS NULL AND cp.principal_id IS NOT NULL AS participant
    INTO contact FROM @SCHEMA@.contacts ct JOIN @SCHEMA@.principals p ON(p.tenant_id,p.id)=(ct.tenant_id,ct.principal_id)
    LEFT JOIN @SCHEMA@.case_participants cp ON(cp.tenant_id,cp.case_id,cp.principal_id)=(ct.tenant_id,cid,ct.principal_id)
    WHERE ct.tenant_id=t AND ct.id=c.followup_contact_id AND ct.id=c.owner_contact_id AND ct.principal_id=c.owner_principal_id;
  IF NOT FOUND OR NOT contact.active OR NOT contact.verified OR NOT contact.participant OR contact.email_address<>e.sender_address OR contact.email_address<>source.sender_address THEN RETURN NULL;END IF;
  SELECT * INTO sr FROM @SCHEMA@.email_reply_state WHERE tenant_id=t AND email_id=source.id;
  IF NOT FOUND THEN RETURN NULL;END IF;
  body:=CASE WHEN e.id=source.id THEN source.body ELSE sr.pending_text END;
  IF body IS NULL THEN RETURN NULL;END IF;
  SELECT resource_security_labels INTO conv_labels FROM @SCHEMA@.conversations WHERE tenant_id=t AND id=c.origin_conversation_id;
  SELECT resource_security_labels INTO draft_labels FROM @SCHEMA@.action_drafts WHERE tenant_id=t AND id=c.origin_draft_id;
  IF conv_labels IS NULL OR draft_labels IS NULL THEN RETURN NULL;END IF;
  SELECT resource_security_labels INTO message_labels FROM @SCHEMA@.messages WHERE tenant_id=t AND id=source.app_message_id;
  SELECT resource_security_labels INTO history_labels FROM @SCHEMA@.case_history WHERE tenant_id=t AND email_source_id=source.id;
  SELECT v.mode INTO mode FROM @SCHEMA@.conversations v WHERE v.tenant_id=t AND v.case_id=cid
    ORDER BY(v.mode IN('human','waiting_staff')) DESC,v.created_at DESC,v.id DESC LIMIT 1;
  PERFORM set_config('app.tenant_id',t::text,true);
  IF e.verification_challenge_id IS NOT NULL THEN
    challenge:=jsonb_build_object('id',ch.id,'control_email_id',e.id,'email_id',source.id,'case_id',ch.case_id,'principal_id',ch.principal_id,'contact_id',ch.contact_id,
      'account_id',ch.email_account_id,'recipient',ch.recipient,'source_content_digest',ch.source_content_digest,'pending_text_hash',ch.pending_text_hash,
      'response_hash',e.verification_response_hash,'code_hash',ch.code_hash,'expires_at',ch.expires_at,'attempts',ch.attempts,'state',ch.state);
  END IF;
  raw:=jsonb_build_object('classification_version',2,'tenant_id',t,'job_id',jid,'worker_id',w,'lease_token',token,'lease_generation',generation,'lease_valid',true,
    'job_kind',j.kind,'job_case_id',j.case_id,'job_draft_id',j.action_draft_id,'server_now',clock_timestamp(),'lease_until',j.lease_until,
    'case_id',cid,'email_id',e.id,'source_email_id',source.id,'referenced_email_id',referenced.id,'account_id',e.email_account_id,'principal_id',contact.principal_id,'contact_id',contact.id,
    'thread_case_id',cid,'thread_account_id',e.email_account_id,'source_case_id',cid,'referenced_case_id',referenced.case_id,'referenced_account_id',referenced.email_account_id,
    'source_account_id',source.email_account_id,'followup_contact_id',c.followup_contact_id,'owner_principal_id',c.owner_principal_id,
    'tenant_active',true,'account_active',true,'principal_active',contact.active,'verified',contact.verified,'participant_active',contact.participant,'eligible',true,
    'sender',e.sender_address,'contact_address',contact.email_address,'source_sender',source.sender_address,'case_version',c.version,'case_status',c.status,'case_language',c.language);
  raw:=raw||jsonb_build_object(
    'assigned_membership_id',c.assigned_membership_id,'assignee_eligible',c.assigned_membership_id IS NOT NULL AND @SCHEMA@.case_member_metadata(c.assigned_membership_id,c.organization_id,c.queue_id) IS NOT NULL,
    'conversation_mode',mode,'state',r.state,'source_reply_state',sr.state,'case_close_reason',c.close_reason,'case_resolution',c.resolution,'case_closed_at',c.closed_at,
    'content_digest',e.content_digest,'source_content_digest',source.content_digest,'source_body',body,'source_body_hash',encode(sha256(convert_to(body,'UTF8')),'hex'),
    'app_message_id',source.app_message_id,'authentication',e.inbound_authentication,'verification_challenge_id',e.verification_challenge_id,'challenge',challenge);
  raw:=raw||jsonb_build_object('case_labels',c.resource_security_labels,'conversation_labels',conv_labels,'draft_labels',draft_labels,
    'message_labels',coalesce(message_labels,'[]'),'job_labels',j.resource_security_labels,'email_labels',@SCHEMA@.source_labels_union(ARRAY[e.resource_security_labels,source.resource_security_labels]),
    'referenced_email_labels',referenced.resource_security_labels,'challenge_labels',coalesce(ch.resource_security_labels,'[]'),
    'reply_labels',@SCHEMA@.source_labels_union(ARRAY[r.resource_security_labels,sr.resource_security_labels]),'history_labels',coalesce(history_labels,'[]'));
  required:=@SCHEMA@.source_labels_union(ARRAY[raw->'case_labels',raw->'conversation_labels',raw->'draft_labels',raw->'message_labels',raw->'job_labels',raw->'email_labels',raw->'referenced_email_labels',raw->'challenge_labels',raw->'reply_labels',raw->'history_labels']);
  IF required IS DISTINCT FROM c.resource_security_labels THEN RETURN NULL;END IF;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid AND state='leased' AND lease_worker_id=w AND lease_token=token AND lease_generation=generation AND lease_until>clock_timestamp()) THEN RETURN NULL;END IF;
  RETURN raw||jsonb_build_object('labels',required,'server_now',clock_timestamp());END $$;

-- Match Python str.strip for command sentinels, including Unicode whitespace.
CREATE FUNCTION @SCHEMA@.mail_strip(value text) RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT btrim(value,chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||
    chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)) $$;
CREATE FUNCTION @SCHEMA@.email_private_command(raw jsonb) RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE body text:=raw->>'source_body';first_line text;proof text;attempt boolean:=false;review boolean;reopening boolean;ambiguous boolean;event text;
  action text;assignee jsonb;status text;source_state text;ch jsonb:=raw->'challenge';binding jsonb;BEGIN
  first_line:=upper(@SCHEMA@.mail_strip(split_part(@SCHEMA@.mail_strip(body),E'\n',1)));
  IF raw->>'verification_challenge_id' IS NULL THEN
    IF raw->>'source_email_id'=raw->>'email_id' AND raw->'authentication'->>'dkim'='pass' AND raw->'authentication'->>'dmarc'='pass' THEN proof:='authenticated_email';END IF;
  ELSE
    binding:=jsonb_build_object('id',raw->'verification_challenge_id','control_email_id',raw->'email_id','email_id',raw->'source_email_id','case_id',raw->'case_id',
      'principal_id',raw->'principal_id','contact_id',raw->'contact_id','account_id',raw->'account_id','recipient',raw->'sender',
      'source_content_digest',raw->'source_content_digest','pending_text_hash',raw->'source_body_hash');
    IF raw->>'source_email_id'<>raw->>'email_id' AND raw->>'source_reply_state'='challenged' AND raw->>'state'='pending'
      AND ch @> binding AND ch->>'state'='pending' AND(ch->>'attempts')::int BETWEEN 0 AND 4
      AND(ch->>'expires_at')::timestamptz>(raw->>'server_now')::timestamptz
      AND(ch->>'expires_at')::timestamptz<=(raw->>'server_now')::timestamptz+interval '300 seconds'
      AND ch->>'response_hash' ~ '^[a-f0-9]{64}$' AND ch->>'code_hash' ~ '^[a-f0-9]{64}$' THEN
      attempt:=true;IF ch->>'response_hash'=ch->>'code_hash' THEN proof:='challenge';END IF;
    END IF;
  END IF;
  review:=proof IS NULL OR length(body) NOT BETWEEN 1 AND 4000 OR @SCHEMA@.mail_strip(body)='' OR left(first_line,7)='VERIFY ' OR first_line IN('NEW ISSUE','MASALAH BARU');
  reopening:=raw->>'case_status'='closed' AND first_line IN('CONTINUE SAME ISSUE','LANJUTKAN MASALAH YANG SAMA');
  ambiguous:=raw->>'case_status'='closed' AND NOT reopening AND lower(@SCHEMA@.mail_strip(body)) NOT IN('thanks','thank you','terima kasih');
  IF reopening AND raw->>'conversation_mode' IN('human','waiting_staff') THEN review:=true;END IF;
  action:=CASE WHEN review THEN 'review' WHEN proof='challenge' THEN 'verify' ELSE 'publish' END;
  event:=CASE WHEN review OR(raw->>'case_status'='closed' AND NOT reopening) THEN NULL WHEN reopening THEN 'reopened' ELSE 'email_received' END;
  assignee:=raw->'assigned_membership_id';IF reopening AND NOT review AND raw->>'assignee_eligible'='false' THEN assignee:='null';END IF;
  status:=CASE WHEN reopening AND NOT review THEN CASE WHEN assignee='null'::jsonb THEN 'new' ELSE 'in_progress' END ELSE raw->>'case_status' END;
  source_state:=CASE WHEN NOT review THEN 'published' WHEN raw->>'source_email_id'=raw->>'email_id' THEN 'review' ELSE raw->>'source_reply_state' END;
  IF attempt AND review AND(ch->>'attempts')::int=4 THEN source_state:='review';END IF;
  RETURN jsonb_build_object('action',action,'body',CASE WHEN review THEN NULL ELSE body END,'body_hash',CASE WHEN review THEN NULL ELSE raw->>'source_body_hash' END,
    'proof_kind',CASE WHEN review THEN NULL ELSE proof END,'history_event',event,'case_status',status,'case_version',(raw->>'case_version')::bigint+CASE WHEN event IS NULL THEN 0 ELSE 1 END,
    'assigned_membership_id',assignee,'source_reply_state',source_state,'conversation_mode',raw->'conversation_mode',
    'case_close_reason',CASE WHEN reopening AND NOT review THEN 'null'::jsonb ELSE raw->'case_close_reason' END,
    'case_resolution',CASE WHEN reopening AND NOT review THEN 'null'::jsonb ELSE raw->'case_resolution' END,
    'case_closed_at',CASE WHEN reopening AND NOT review THEN 'null'::jsonb ELSE raw->'case_closed_at' END,
    'review_reason',CASE WHEN ambiguous AND NOT review THEN 'CLOSED_REPLY_AMBIGUOUS' ELSE NULL END,'labels',raw->'labels',
    'input_basis_hash',@SCHEMA@.mail_fingerprint(raw-ARRAY['server_now','lease_until']),'challenge_attempt',attempt,
    'challenge_attempts',CASE WHEN attempt THEN(ch->>'attempts')::int+1 ELSE NULL END,
    'challenge_state',CASE WHEN attempt THEN CASE WHEN action='verify' THEN 'consumed' WHEN(ch->>'attempts')::int=4 THEN 'failed' ELSE 'pending' END ELSE NULL END);
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.mail_strip(text),@SCHEMA@.email_private_command(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.mail_strip(text),@SCHEMA@.email_private_command(jsonb) TO @REPLY@;

CREATE FUNCTION @SCHEMA@.email_private_reply_apply(t uuid,jid uuid,w uuid,token uuid,generation bigint,expected jsonb,command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE raw jsonb;calc jsonb;receipt jsonb;final_context jsonb;cid uuid;eid uuid;sid uuid;mid uuid;seq bigint;before @SCHEMA@.cases%ROWTYPE;
  c @SCHEMA@.cases%ROWTYPE;event text;BEGIN
  raw:=@SCHEMA@.email_private_reply_candidate(t,jid,w,token,generation);
  IF raw IS NULL OR(raw-ARRAY['server_now','lease_until']) IS DISTINCT FROM(expected-ARRAY['server_now','lease_until']) OR raw->>'state'<>'pending' THEN RETURN 'false'::jsonb;END IF;
  cid:=(raw->>'case_id')::uuid;eid:=(raw->>'email_id')::uuid;sid:=(raw->>'source_email_id')::uuid;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=t AND id=cid FOR UPDATE;before:=c;
  PERFORM id FROM @SCHEMA@.conversations WHERE tenant_id=t AND(case_id=cid OR id=c.origin_conversation_id) ORDER BY id FOR UPDATE;
  PERFORM id FROM @SCHEMA@.outbox_jobs WHERE tenant_id=t AND id=jid FOR UPDATE;
  PERFORM id FROM @SCHEMA@.email_messages WHERE tenant_id=t AND id=ANY(ARRAY[eid,sid]) ORDER BY id FOR UPDATE;
  PERFORM email_id FROM @SCHEMA@.email_reply_state WHERE tenant_id=t AND email_id=ANY(ARRAY[eid,sid]) ORDER BY email_id FOR UPDATE;
  PERFORM id FROM @SCHEMA@.email_reply_challenges WHERE tenant_id=t AND id=(raw->>'verification_challenge_id')::uuid FOR UPDATE;
  PERFORM id FROM @SCHEMA@.tenants WHERE id=t FOR SHARE;
  PERFORM id FROM @SCHEMA@.email_accounts WHERE tenant_id=t AND id=(raw->>'account_id')::uuid FOR SHARE;
  PERFORM id FROM @SCHEMA@.principals WHERE tenant_id=t AND id=(raw->>'principal_id')::uuid FOR SHARE;
  PERFORM id FROM @SCHEMA@.contacts WHERE tenant_id=t AND id=(raw->>'contact_id')::uuid FOR SHARE;
  PERFORM id FROM @SCHEMA@.case_participants WHERE tenant_id=t AND case_id=cid AND principal_id=(raw->>'principal_id')::uuid FOR SHARE;
  raw:=@SCHEMA@.email_private_reply_candidate(t,jid,w,token,generation);
  IF raw IS NULL OR(raw-ARRAY['server_now','lease_until']) IS DISTINCT FROM(expected-ARRAY['server_now','lease_until']) THEN RETURN 'false'::jsonb;END IF;
  calc:=@SCHEMA@.email_private_command(raw);
  IF calc IS DISTINCT FROM command THEN RETURN 'false'::jsonb;END IF;
  event:=calc->>'history_event';
  UPDATE @SCHEMA@.email_reply_state SET case_id=cid,principal_id=(raw->>'principal_id')::uuid,resource_security_labels=raw->'labels',private_command_hash=@SCHEMA@.mail_fingerprint(calc)
    WHERE tenant_id=t AND email_id=eid;
  UPDATE @SCHEMA@.outbox_jobs SET resource_security_labels=raw->'labels' WHERE tenant_id=t AND id=jid;
  UPDATE @SCHEMA@.email_messages SET resource_security_labels=raw->'labels' WHERE tenant_id=t AND id=eid;
  IF calc->>'action'='review' THEN
    UPDATE @SCHEMA@.email_reply_state SET state='review',reason='REPLY_REVIEW' WHERE tenant_id=t AND email_id=eid;
    IF sid<>eid AND calc->>'source_reply_state'='review' THEN UPDATE @SCHEMA@.email_reply_state SET state='review',reason='CHALLENGE_ATTEMPTS_EXHAUSTED' WHERE tenant_id=t AND email_id=sid;END IF;
  ELSE
    UPDATE @SCHEMA@.email_reply_state SET state='publishing',proof=calc->>'proof_kind',case_id=cid,principal_id=(raw->>'principal_id')::uuid,resource_security_labels=raw->'labels'
      WHERE tenant_id=t AND email_id=sid;
    SELECT coalesce(max(sequence),0)+1 INTO seq FROM @SCHEMA@.messages WHERE tenant_id=t AND case_id=cid;mid:=gen_random_uuid();
    INSERT INTO @SCHEMA@.messages(tenant_id,id,case_id,author_principal_id,author_type,channel,source_kind,body,language,client_message_id,sequence,email_source_id,resource_security_labels)
      VALUES(t,mid,cid,(raw->>'principal_id')::uuid,'customer','email','email_reply',calc->>'body',c.language,sid,seq,sid,raw->'labels');
    IF event IS NOT NULL THEN
      IF event='reopened' THEN
        UPDATE @SCHEMA@.cases SET status=calc->>'case_status',assigned_membership_id=(calc->>'assigned_membership_id')::uuid,next_actor='staff',next_action='Customer continued by email.',
          close_reason=NULL,resolution=NULL,duplicate_of_case_id=NULL,followup_evidence=NULL,closed_at=NULL,version=version+1 WHERE tenant_id=t AND id=cid RETURNING * INTO c;
      ELSE UPDATE @SCHEMA@.cases SET version=version+1 WHERE tenant_id=t AND id=cid RETURNING * INTO c;END IF;
      INSERT INTO @SCHEMA@.case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version,from_status,to_status,assigned_membership_id,next_actor,next_action,
        close_reason,resolution,duplicate_of_case_id,followup_evidence,customer_message,client_request_id,request_hash,email_source_id,resource_security_labels)
        VALUES(t,cid,(raw->>'principal_id')::uuid,event,before.version,c.version,before.status,c.status,c.assigned_membership_id,c.next_actor,c.next_action,
          c.close_reason,c.resolution,c.duplicate_of_case_id,c.followup_evidence,calc->>'body',sid,raw->>'source_content_digest',sid,raw->'labels');
    END IF;
    UPDATE @SCHEMA@.email_messages SET case_id=cid,app_message_id=mid,recipient_contact_id=(raw->>'contact_id')::uuid,recipient_principal_id=(raw->>'principal_id')::uuid,
      verification_state='verified',resource_security_labels=raw->'labels' WHERE tenant_id=t AND id=sid;
    UPDATE @SCHEMA@.email_reply_state SET state='published',app_message_id=mid,reason=calc->>'review_reason' WHERE tenant_id=t AND email_id=sid;
    IF calc->>'action'='verify' THEN UPDATE @SCHEMA@.email_reply_state SET state='verification_reply',reason=NULL WHERE tenant_id=t AND email_id=eid;END IF;
  END IF;
  IF calc->>'challenge_attempt'='true' THEN
    UPDATE @SCHEMA@.email_reply_challenges SET attempts=attempts+1,state=calc->>'challenge_state'
      WHERE tenant_id=t AND id=(raw->>'verification_challenge_id')::uuid AND state='pending' AND attempts<5 AND expires_at>clock_timestamp();
    IF NOT FOUND THEN RAISE EXCEPTION 'Private challenge expired' USING ERRCODE='23514';END IF;
  END IF;
  SET CONSTRAINTS ALL IMMEDIATE;
  -- Receipt is built from actual persisted rows, not the proposed command.
  SELECT jsonb_build_object('classification_version',2,'tenant_id',t,'job_id',j.id,'email_id',e.id,'source_email_id',source.id,'case_id',stored_case.id,
    'account_id',e.email_account_id,'principal_id',r.principal_id,'contact_id',ct.id,'worker_id',j.lease_worker_id,'lease_token',j.lease_token,'lease_generation',j.lease_generation,
    'lease_valid',j.state='leased' AND j.lease_until>clock_timestamp(),'server_now',clock_timestamp(),'lease_until',j.lease_until,
    'command_hash',r.private_command_hash,'state',r.state,'case_status',stored_case.status,'case_version',stored_case.version,'assigned_membership_id',stored_case.assigned_membership_id,
    'history_event',h.event_type,'message_body_hash',CASE WHEN m.id IS NOT NULL THEN encode(sha256(convert_to(m.body,'UTF8')),'hex') ELSE NULL END,
    'proof_kind',CASE WHEN m.id IS NOT NULL THEN sr.proof ELSE NULL END,'challenge_attempts',CASE WHEN calc->>'challenge_attempt'='true' THEN ch.attempts ELSE NULL END,
    'challenge_state',CASE WHEN calc->>'challenge_attempt'='true' THEN ch.state ELSE NULL END,'source_reply_state',sr.state,
    'conversation_mode',raw->'conversation_mode','case_close_reason',stored_case.close_reason,'case_resolution',stored_case.resolution,'case_closed_at',stored_case.closed_at,
    'message_author_principal_id',m.author_principal_id,'message_source_email_id',m.email_source_id,'message_source_kind',m.source_kind,'message_channel',m.channel,
    'message_language',m.language,'message_case_id',m.case_id,'message_content_digest',CASE WHEN m.id IS NOT NULL THEN source.content_digest ELSE NULL END,
    'history_source_email_id',h.email_source_id,'history_case_id',h.case_id,'history_actor_principal_id',h.actor_principal_id,'history_previous_version',h.previous_version,'history_resulting_version',h.resulting_version)
    ||jsonb_build_object(
    'challenge_id',CASE WHEN calc->>'challenge_attempt'='true' THEN ch.id ELSE NULL END,'challenge_control_email_id',CASE WHEN calc->>'challenge_attempt'='true' THEN e.id ELSE NULL END,
    'challenge_source_email_id',CASE WHEN calc->>'challenge_attempt'='true' THEN ch.email_id ELSE NULL END,
    'review_reason',CASE WHEN m.id IS NOT NULL THEN sr.reason ELSE NULL END,'app_message_id',m.id,'email_app_message_id',source.app_message_id,'reply_app_message_id',sr.app_message_id,'history_id',h.id,
    'case_labels',stored_case.resource_security_labels,'email_labels',@SCHEMA@.source_labels_union(ARRAY[e.resource_security_labels,source.resource_security_labels]),
    'reply_labels',@SCHEMA@.source_labels_union(ARRAY[r.resource_security_labels,sr.resource_security_labels]),'message_labels',coalesce(m.resource_security_labels,'[]'),'history_labels',coalesce(h.resource_security_labels,'[]'))
    INTO receipt FROM @SCHEMA@.outbox_jobs j JOIN @SCHEMA@.email_messages e ON(e.tenant_id,e.id)=(j.tenant_id,j.reply_email_id)
    JOIN @SCHEMA@.email_messages source ON source.tenant_id=t AND source.id=sid
    JOIN @SCHEMA@.email_reply_state r ON(r.tenant_id,r.email_id)=(e.tenant_id,e.id)
    JOIN @SCHEMA@.email_reply_state sr ON(sr.tenant_id,sr.email_id)=(source.tenant_id,source.id)
    JOIN @SCHEMA@.cases stored_case ON stored_case.tenant_id=t AND stored_case.id=cid JOIN @SCHEMA@.contacts ct ON(ct.tenant_id,ct.id)=(stored_case.tenant_id,stored_case.followup_contact_id)
    LEFT JOIN @SCHEMA@.messages m ON(m.tenant_id,m.id)=(source.tenant_id,source.app_message_id) AND calc->>'action'<>'review'
    LEFT JOIN @SCHEMA@.case_history h ON h.tenant_id=t AND h.email_source_id=sid AND event IS NOT NULL
    LEFT JOIN @SCHEMA@.email_reply_challenges ch ON ch.tenant_id=t AND ch.id=e.verification_challenge_id
    WHERE j.tenant_id=t AND j.id=jid;
  final_context:=@SCHEMA@.email_private_reply_candidate(t,jid,w,token,generation);
  IF final_context IS NULL OR(calc->>'challenge_attempt'='true' AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.email_reply_challenges WHERE tenant_id=t AND id=(raw->>'verification_challenge_id')::uuid AND expires_at>clock_timestamp()))
    THEN RAISE EXCEPTION 'Private reply final fence expired' USING ERRCODE='23514';END IF;
  SET CONSTRAINTS ALL DEFERRED;RETURN receipt||jsonb_build_object('final_context',final_context);END $$;

REVOKE ALL ON FUNCTION @SCHEMA@.email_private_reply_candidate(uuid,uuid,uuid,uuid,bigint),@SCHEMA@.email_private_reply_apply(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.email_private_reply_candidate(uuid,uuid,uuid,uuid,bigint) OWNER TO @REPLY@;
ALTER FUNCTION @SCHEMA@.email_private_reply_apply(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb) OWNER TO @REPLY@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.email_private_reply_candidate(uuid,uuid,uuid,uuid,bigint),@SCHEMA@.email_private_reply_apply(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb) TO @WORKER@;
