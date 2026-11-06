-- Canonical hashing matches Python fingerprint (UTF8, sorted object keys,
-- compact separators), never the whitespace/order of jsonb::text.
CREATE FUNCTION @SCHEMA@.mail_canonical_json(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text;BEGIN
  CASE jsonb_typeof(value)
    WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||@SCHEMA@.mail_canonical_json(item),',' ORDER BY key COLLATE "C"),'')||'}'
      INTO result FROM jsonb_each(value) entry(key,item);
    WHEN 'array' THEN SELECT '['||coalesce(string_agg(@SCHEMA@.mail_canonical_json(item),',' ORDER BY ordinal),'')||']'
      INTO result FROM jsonb_array_elements(value) WITH ORDINALITY entry(item,ordinal);
    ELSE result:=value::text;
  END CASE;RETURN result;END $$;
CREATE FUNCTION @SCHEMA@.mail_fingerprint(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT encode(sha256(convert_to(@SCHEMA@.mail_canonical_json(value),'UTF8')),'hex') $$;
REVOKE ALL ON FUNCTION @SCHEMA@.mail_canonical_json(jsonb),@SCHEMA@.mail_fingerprint(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.mail_canonical_json(jsonb),@SCHEMA@.mail_fingerprint(jsonb) TO @MAIL@,@REPLY@;
CREATE FUNCTION @SCHEMA@.mail_labels_step(state jsonb,item jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT @SCHEMA@.source_labels_union(ARRAY[state,item]) $$;
CREATE AGGREGATE @SCHEMA@.mail_labels_union(jsonb)(SFUNC=@SCHEMA@.mail_labels_step,STYPE=jsonb,INITCOND='[]');
REVOKE ALL ON FUNCTION @SCHEMA@.mail_labels_step(jsonb,jsonb),@SCHEMA@.mail_labels_union(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.mail_labels_step(jsonb,jsonb),@SCHEMA@.mail_labels_union(jsonb) TO @MAIL@,@REPLY@;

ALTER TABLE @SCHEMA@.email_messages DROP CONSTRAINT email_messages_resource_security_labels_check;
ALTER TABLE @SCHEMA@.email_reply_state DROP CONSTRAINT private_labels_hold;
ALTER TABLE @SCHEMA@.email_reply_challenges DROP CONSTRAINT private_labels_hold;
ALTER TABLE @SCHEMA@.email_messages ADD COLUMN approved_envelope jsonb,
  ADD COLUMN envelope_hash text CHECK(envelope_hash ~ '^[a-f0-9]{64}$'),
  ADD CONSTRAINT approved_private_envelope CHECK(
    (approved_envelope IS NULL)=(envelope_hash IS NULL) AND
    (approved_envelope IS NULL OR(direction='outbound' AND jsonb_typeof(approved_envelope)='object'
      AND octet_length(approved_envelope::text)<=24000)));
GRANT INSERT(approved_envelope,envelope_hash) ON @SCHEMA@.email_messages TO @MAIL@;
GRANT SELECT(resource_security_labels) ON @SCHEMA@.action_drafts,@SCHEMA@.conversations TO @MAIL@;
GRANT SELECT(tenant_id,id,case_id,resource_security_labels) ON @SCHEMA@.conversations TO @MAIL@;
GRANT SELECT(tenant_id,id,resource_security_labels) ON @SCHEMA@.action_drafts TO @MAIL@;
GRANT SELECT(origin_conversation_id,origin_draft_id) ON @SCHEMA@.cases TO @MAIL@;
GRANT SELECT(owner_contact_id) ON @SCHEMA@.cases TO @MAIL@;
GRANT SELECT(author_type,conversation_id,case_id,source_kind) ON @SCHEMA@.messages TO @MAIL@;
GRANT UPDATE(id) ON @SCHEMA@.cases,@SCHEMA@.conversations TO @MAIL@;
CREATE POLICY mail_private_case_lock ON @SCHEMA@.cases FOR UPDATE TO @MAIL@ USING(true) WITH CHECK(false);
CREATE POLICY mail_private_conversation_lock ON @SCHEMA@.conversations FOR UPDATE TO @MAIL@ USING(true) WITH CHECK(false);
GRANT UPDATE(id) ON @SCHEMA@.conversations TO @REPLY@;
CREATE POLICY private_reply_conversation_lock ON @SCHEMA@.conversations FOR UPDATE TO @REPLY@ USING(true) WITH CHECK(false);
GRANT SELECT(tenant_id,id,sequence,resource_security_labels) ON @SCHEMA@.messages TO @MAIL@;
GRANT SELECT(resource_security_labels) ON @SCHEMA@.outbox_jobs TO @MAIL@;
CREATE POLICY mail_private_graph_read ON @SCHEMA@.conversations FOR SELECT TO @MAIL@ USING(true);
CREATE POLICY mail_private_graph_read ON @SCHEMA@.action_drafts FOR SELECT TO @MAIL@ USING(true);
GRANT SELECT,UPDATE(resource_security_labels) ON @SCHEMA@.outbox_jobs TO @REPLY@;
CREATE POLICY private_reply_job_classification ON @SCHEMA@.outbox_jobs FOR UPDATE TO @REPLY@
  USING(kind='email_reply') WITH CHECK(kind='email_reply');

ALTER TABLE @SCHEMA@.email_delivery_events ADD COLUMN resource_security_labels jsonb NOT NULL DEFAULT '[]',
  ADD CONSTRAINT private_labels_canonical CHECK(@SCHEMA@.source_labels_valid(resource_security_labels));
CREATE TRIGGER private_labels_monotone BEFORE UPDATE OF resource_security_labels ON @SCHEMA@.email_delivery_events
  FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_source_labels_monotone();
GRANT SELECT(tenant_id,id,case_id,email_message_id,resource_security_labels) ON @SCHEMA@.email_delivery_events TO @GRAPH@;
CREATE POLICY source_graph_metadata ON @SCHEMA@.email_delivery_events FOR SELECT TO @GRAPH@ USING(true);
GRANT SELECT(verification_challenge_id,email_account_id) ON @SCHEMA@.email_messages TO @GRAPH@;
GRANT SELECT(reply_account_id) ON @SCHEMA@.outbox_jobs TO @GRAPH@;
-- This trigger reads classification/link metadata only. It never reads bodies.
CREATE FUNCTION @SCHEMA@.inherit_private_mail_graph() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rowdata jsonb:=to_jsonb(NEW);required jsonb:=NEW.resource_security_labels;parent jsonb;cid uuid;
  eid uuid;linked uuid;BEGIN
  cid:=(rowdata->>'case_id')::uuid;
  eid:=coalesce((rowdata->>'email_message_id')::uuid,(rowdata->>'reply_email_id')::uuid,(rowdata->>'email_id')::uuid);
  IF eid IS NOT NULL THEN
    SELECT resource_security_labels,case_id INTO parent,linked FROM @SCHEMA@.email_messages WHERE tenant_id=NEW.tenant_id AND id=eid;
    IF NOT FOUND THEN RAISE EXCEPTION 'Mail classification parent unavailable' USING ERRCODE='23514';END IF;
    required:=@SCHEMA@.source_labels_union(ARRAY[required,parent]);cid:=coalesce(cid,linked);
  END IF;
  IF cid IS NOT NULL THEN
    SELECT resource_security_labels INTO parent FROM @SCHEMA@.cases WHERE tenant_id=NEW.tenant_id AND id=cid;
    IF NOT FOUND THEN RAISE EXCEPTION 'Mail case unavailable' USING ERRCODE='23514';END IF;
    required:=@SCHEMA@.source_labels_union(ARRAY[required,parent]);
  END IF;
  NEW.resource_security_labels:=required;RETURN NEW;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.inherit_private_mail_graph() FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.inherit_private_mail_graph() OWNER TO @GRAPH@;
CREATE TRIGGER b_private_mail_inherit BEFORE INSERT OR UPDATE ON @SCHEMA@.email_messages FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.inherit_private_mail_graph();
CREATE TRIGGER b_private_mail_inherit BEFORE INSERT OR UPDATE ON @SCHEMA@.email_reply_state FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.inherit_private_mail_graph();
CREATE TRIGGER b_private_mail_inherit BEFORE INSERT OR UPDATE ON @SCHEMA@.email_reply_challenges FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.inherit_private_mail_graph();
CREATE TRIGGER b_private_mail_inherit BEFORE INSERT OR UPDATE ON @SCHEMA@.outbox_jobs FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.inherit_private_mail_graph();
CREATE TRIGGER b_private_mail_inherit BEFORE INSERT OR UPDATE ON @SCHEMA@.email_delivery_events FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.inherit_private_mail_graph();
-- Existing0030 validator covers reply/job rows. Email/event rows need a final
-- current-row check against their persisted case; no queued relationship image.
CREATE FUNCTION @SCHEMA@.validate_private_mail_graph() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE labels jsonb;cid uuid;parent jsonb;invalid_link boolean:=false;BEGIN
  IF TG_TABLE_NAME='email_messages' THEN
    SELECT e.resource_security_labels,coalesce(e.case_id,r.case_id,ch.case_id),
      (e.case_id IS NOT NULL AND r.case_id IS NOT NULL AND e.case_id<>r.case_id) OR(ch.case_id IS NOT NULL AND coalesce(e.case_id,r.case_id,ch.case_id)<>ch.case_id)
      INTO labels,cid,invalid_link FROM @SCHEMA@.email_messages e
      LEFT JOIN @SCHEMA@.email_reply_state r ON(r.tenant_id,r.email_id)=(e.tenant_id,e.id)
      LEFT JOIN @SCHEMA@.email_reply_challenges ch ON(ch.tenant_id,ch.id)=(e.tenant_id,e.verification_challenge_id)
      WHERE e.tenant_id=NEW.tenant_id AND e.id=NEW.id;
  ELSIF TG_TABLE_NAME='outbox_jobs' THEN
    SELECT j.resource_security_labels,coalesce(j.case_id,e.case_id,r.case_id,ch.case_id),
      (j.case_id IS NOT NULL AND coalesce(e.case_id,r.case_id,ch.case_id,j.case_id)<>j.case_id)
      OR(j.reply_account_id IS NOT NULL AND j.reply_account_id IS DISTINCT FROM e.email_account_id)
      INTO labels,cid,invalid_link FROM @SCHEMA@.outbox_jobs j
      LEFT JOIN @SCHEMA@.email_messages e ON e.tenant_id=j.tenant_id AND e.id=coalesce(j.reply_email_id,j.email_message_id)
      LEFT JOIN @SCHEMA@.email_reply_state r ON(r.tenant_id,r.email_id)=(e.tenant_id,e.id)
      LEFT JOIN @SCHEMA@.email_reply_challenges ch ON(ch.tenant_id,ch.id)=(e.tenant_id,e.verification_challenge_id)
      WHERE j.tenant_id=NEW.tenant_id AND j.id=NEW.id;
  ELSIF TG_TABLE_NAME='email_reply_state' THEN
    SELECT r.resource_security_labels,coalesce(r.case_id,e.case_id),r.case_id IS NOT NULL AND e.case_id IS NOT NULL AND r.case_id<>e.case_id
      INTO labels,cid,invalid_link FROM @SCHEMA@.email_reply_state r JOIN @SCHEMA@.email_messages e ON(e.tenant_id,e.id)=(r.tenant_id,r.email_id)
      WHERE r.tenant_id=NEW.tenant_id AND r.email_id=NEW.email_id;
  ELSE
    SELECT resource_security_labels,case_id INTO labels,cid FROM @SCHEMA@.email_delivery_events WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
  END IF;
  IF labels IS NULL OR invalid_link OR(cid IS NULL AND labels<>'[]'::jsonb) THEN RAISE EXCEPTION 'Mail graph unavailable or mislinked' USING ERRCODE='23514';END IF;
  IF cid IS NOT NULL THEN
    SELECT resource_security_labels INTO parent FROM @SCHEMA@.cases WHERE tenant_id=NEW.tenant_id AND id=cid;
    IF parent IS NULL OR NOT @SCHEMA@.source_labels_contains(parent,labels) THEN RAISE EXCEPTION 'Mail exceeds case aggregate' USING ERRCODE='23514';END IF;
  END IF;RETURN NULL;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.validate_private_mail_graph() FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.validate_private_mail_graph() OWNER TO @GRAPH@;
CREATE CONSTRAINT TRIGGER private_mail_graph AFTER INSERT OR UPDATE ON @SCHEMA@.email_messages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_private_mail_graph();
CREATE CONSTRAINT TRIGGER private_mail_graph AFTER INSERT OR UPDATE ON @SCHEMA@.email_delivery_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_private_mail_graph();
CREATE CONSTRAINT TRIGGER private_mail_graph AFTER INSERT OR UPDATE ON @SCHEMA@.outbox_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_private_mail_graph();
CREATE CONSTRAINT TRIGGER private_mail_graph AFTER INSERT OR UPDATE ON @SCHEMA@.email_reply_state DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.validate_private_mail_graph();
CREATE FUNCTION @SCHEMA@.guard_approved_private_mail() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
  IF OLD.approved_envelope IS NOT NULL AND
    (to_jsonb(NEW)-ARRAY['delivery_status','provider_message_id','completed_at','delivery_version','rfc_message_id','provider_status_at','last_checked_at','needs_review','review_reason'])
    IS DISTINCT FROM(to_jsonb(OLD)-ARRAY['delivery_status','provider_message_id','completed_at','delivery_version','rfc_message_id','provider_status_at','last_checked_at','needs_review','review_reason'])
  THEN RAISE EXCEPTION 'Approved mail provenance is immutable' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_approved_private_mail() FROM PUBLIC;
CREATE TRIGGER z_approved_private_mail BEFORE UPDATE ON @SCHEMA@.email_messages FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_approved_private_mail();
