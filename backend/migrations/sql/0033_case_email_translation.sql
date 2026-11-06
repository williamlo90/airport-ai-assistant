GRANT EXECUTE ON FUNCTION @SCHEMA@.mail_canonical_json(jsonb),@SCHEMA@.mail_fingerprint(jsonb) TO @RUNTIME@;
CREATE INDEX case_correspondence_position ON @SCHEMA@.messages(tenant_id,case_id,created_at DESC,id DESC) WHERE case_id IS NOT NULL;
CREATE INDEX conversation_correspondence_position ON @SCHEMA@.messages(tenant_id,conversation_id,created_at DESC,id DESC) WHERE conversation_id IS NOT NULL;
CREATE INDEX case_correspondence_conversations ON @SCHEMA@.conversations(tenant_id,case_id,id) WHERE case_id IS NOT NULL;
CREATE TABLE @SCHEMA@.case_email_translation_drafts(
  tenant_id uuid NOT NULL,id uuid NOT NULL,case_id uuid NOT NULL,principal_id uuid NOT NULL,session_id uuid NOT NULL,membership_id uuid NOT NULL,
  request_key uuid NOT NULL,request_hash text NOT NULL CHECK(request_hash~'^[a-f0-9]{64}$'),
  original_body text NOT NULL CHECK(length(btrim(original_body))>0 AND length(original_body)<=4000),
  source_language text NOT NULL CHECK(source_language IN('en','id')),target_language text NOT NULL CHECK(target_language IN('en','id') AND target_language<>source_language),
  channel text NOT NULL DEFAULT 'email' CHECK(channel='email'),case_version bigint NOT NULL CHECK(case_version>0),
  source_context_hash text NOT NULL CHECK(source_context_hash~'^[a-f0-9]{64}$'),
  resource_security_labels jsonb NOT NULL CHECK(@SCHEMA@.source_labels_valid(resource_security_labels)),
  state text NOT NULL DEFAULT 'generating' CHECK(state IN('generating','ready','failed','sent')),
  translated_body text CHECK(length(btrim(translated_body))>0 AND length(translated_body)<=4000),
  reviewed_body text CHECK(length(btrim(reviewed_body))>0 AND length(reviewed_body)<=4000),message_id uuid,send_key text,
  revision integer NOT NULL DEFAULT 1 CHECK(revision>0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '15 minutes',
  PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,principal_id,request_key),
  CHECK(expires_at>created_at AND expires_at<=created_at+interval '16 minutes'),
  CHECK((state IN('ready','sent'))=(translated_body IS NOT NULL)),
  CHECK((state='sent')=(reviewed_body IS NOT NULL AND message_id IS NOT NULL AND send_key IS NOT NULL)),
  CHECK(state='sent' OR(reviewed_body IS NULL AND message_id IS NULL AND send_key IS NULL)),
  FOREIGN KEY(tenant_id,case_id) REFERENCES @SCHEMA@.cases(tenant_id,id),
  FOREIGN KEY(tenant_id,principal_id) REFERENCES @SCHEMA@.principals(tenant_id,id),
  FOREIGN KEY(tenant_id,session_id) REFERENCES @SCHEMA@.sessions(tenant_id,id),
  FOREIGN KEY(tenant_id,membership_id) REFERENCES @SCHEMA@.staff_memberships(tenant_id,id),
  FOREIGN KEY(tenant_id,message_id,case_id) REFERENCES @SCHEMA@.messages(tenant_id,id,case_id)
);
ALTER TABLE @SCHEMA@.case_email_translation_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE @SCHEMA@.case_email_translation_drafts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON @SCHEMA@.case_email_translation_drafts FROM PUBLIC;
GRANT SELECT ON @SCHEMA@.case_email_translation_drafts TO @RUNTIME@;
GRANT INSERT(tenant_id,id,case_id,principal_id,session_id,membership_id,request_key,request_hash,original_body,source_language,target_language,case_version,source_context_hash,resource_security_labels)
  ON @SCHEMA@.case_email_translation_drafts TO @RUNTIME@;
GRANT UPDATE(state,translated_body,reviewed_body,message_id,send_key,revision) ON @SCHEMA@.case_email_translation_drafts TO @RUNTIME@;
CREATE POLICY case_translation_read ON @SCHEMA@.case_email_translation_drafts FOR SELECT TO @RUNTIME@ USING(
  tenant_id=@SCHEMA@.current_tenant_id() AND principal_id=@SCHEMA@.current_principal_id() AND session_id=@SCHEMA@.current_session_id()
  AND @SCHEMA@.case_staff_session_valid() AND EXISTS(SELECT 1 FROM @SCHEMA@.cases c WHERE c.tenant_id=case_email_translation_drafts.tenant_id AND c.id=case_email_translation_drafts.case_id));
CREATE POLICY case_translation_create ON @SCHEMA@.case_email_translation_drafts FOR INSERT TO @RUNTIME@ WITH CHECK(
  tenant_id=@SCHEMA@.current_tenant_id() AND principal_id=@SCHEMA@.current_principal_id() AND session_id=@SCHEMA@.current_session_id()
  AND @SCHEMA@.case_staff_session_valid() AND EXISTS(SELECT 1 FROM @SCHEMA@.cases c WHERE c.tenant_id=case_email_translation_drafts.tenant_id AND c.id=case_email_translation_drafts.case_id));
CREATE POLICY case_translation_update ON @SCHEMA@.case_email_translation_drafts FOR UPDATE TO @RUNTIME@ USING(
  tenant_id=@SCHEMA@.current_tenant_id() AND principal_id=@SCHEMA@.current_principal_id() AND session_id=@SCHEMA@.current_session_id()
  AND @SCHEMA@.case_staff_session_valid() AND EXISTS(SELECT 1 FROM @SCHEMA@.cases c WHERE c.tenant_id=case_email_translation_drafts.tenant_id AND c.id=case_email_translation_drafts.case_id))
  WITH CHECK(tenant_id=@SCHEMA@.current_tenant_id() AND principal_id=@SCHEMA@.current_principal_id() AND session_id=@SCHEMA@.current_session_id());

CREATE FUNCTION @SCHEMA@.guard_case_email_translation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c @SCHEMA@.cases%ROWTYPE;ctx jsonb;labels jsonb;m @SCHEMA@.messages%ROWTYPE;BEGIN
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id FOR UPDATE;
  IF NOT FOUND OR NOT @SCHEMA@.case_staff_session_valid() OR NEW.principal_id IS DISTINCT FROM @SCHEMA@.current_principal_id()
    OR NEW.session_id IS DISTINCT FROM @SCHEMA@.current_session_id() OR NEW.membership_id IS DISTINCT FROM c.assigned_membership_id
    OR NEW.case_version IS DISTINCT FROM c.version OR c.status='closed' THEN RAISE EXCEPTION 'Translation context changed' USING ERRCODE='23514';END IF;
  ctx:=@SCHEMA@.case_email_context(c.id);
  IF ctx IS NULL OR ctx->'recipient'->>'tenant_active' IS DISTINCT FROM 'true' OR ctx->'recipient'->>'principal_active' IS DISTINCT FROM 'true'
    OR ctx->'recipient'->>'participant_active' IS DISTINCT FROM 'true' OR ctx->'recipient'->>'verified' IS DISTINCT FROM 'true'
    OR ctx->'recipient'->>'account_active' IS DISTINCT FROM 'true' OR ctx->'recipient'->>'principal_id' IS DISTINCT FROM ctx->'recipient'->>'owner_principal_id'
    THEN RAISE EXCEPTION 'Translation recipient unavailable' USING ERRCODE='42501';END IF;
  labels:=@SCHEMA@.source_labels_union(ARRAY[ctx->'case_labels',ctx->'draft_labels',ctx->'conversation_labels']);
  IF NEW.resource_security_labels IS DISTINCT FROM labels OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.tenants t WHERE t.id=NEW.tenant_id AND NEW.source_language=ANY(t.supported_languages) AND NEW.target_language=ANY(t.supported_languages))
    THEN RAISE EXCEPTION 'Translation classification changed' USING ERRCODE='23514';END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'generating' OR NEW.revision<>1 OR NEW.created_at<clock_timestamp()-interval '10 seconds'
      OR NEW.created_at>clock_timestamp()+interval '1 second' THEN RAISE EXCEPTION 'Invalid translation reservation' USING ERRCODE='23514';END IF;
  ELSE
    IF(to_jsonb(NEW)-ARRAY['state','translated_body','reviewed_body','message_id','send_key','revision']) IS DISTINCT FROM
      (to_jsonb(OLD)-ARRAY['state','translated_body','reviewed_body','message_id','send_key','revision'])
      OR NEW.revision<>OLD.revision+1 OR NOT(OLD.state='generating' AND NEW.state IN('ready','failed') OR OLD.state='ready' AND NEW.state='sent')
      OR(OLD.state='ready' AND NEW.translated_body IS DISTINCT FROM OLD.translated_body)
      THEN RAISE EXCEPTION 'Invalid translation transition' USING ERRCODE='23514';END IF;
    IF NEW.state='ready' AND NEW.created_at+interval '120 seconds'<=clock_timestamp()
      OR NEW.state='sent' AND NEW.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Translation expired' USING ERRCODE='23514';END IF;
    IF NEW.state='sent' THEN
      SELECT * INTO m FROM @SCHEMA@.messages WHERE tenant_id=NEW.tenant_id AND id=NEW.message_id;
      IF NOT FOUND OR m.case_id IS DISTINCT FROM NEW.case_id OR m.conversation_id IS NOT NULL OR m.author_type<>'staff'
        OR m.author_principal_id IS DISTINCT FROM NEW.principal_id OR m.created_session_id IS DISTINCT FROM NEW.session_id
        OR m.sender_membership_id IS DISTINCT FROM NEW.membership_id OR m.body IS DISTINCT FROM NEW.reviewed_body
        OR m.language IS DISTINCT FROM NEW.target_language OR m.delivery_channel<>'email' OR m.staff_request_key IS DISTINCT FROM NEW.send_key
        OR m.basis_case_version IS DISTINCT FROM NEW.case_version OR m.resource_security_labels IS DISTINCT FROM labels THEN
        RAISE EXCEPTION 'Translation send mismatch' USING ERRCODE='23514';END IF;
      ctx:=jsonb_set(ctx,'{last_sequence}',to_jsonb((ctx->>'last_sequence')::bigint-1));
    END IF;
  END IF;
  IF NEW.source_context_hash IS DISTINCT FROM @SCHEMA@.mail_fingerprint(ctx) THEN RAISE EXCEPTION 'Translation basis changed' USING ERRCODE='23514';END IF;
  RETURN NEW;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_case_email_translation() FROM PUBLIC;
CREATE TRIGGER case_email_translation_guard BEFORE INSERT OR UPDATE ON @SCHEMA@.case_email_translation_drafts FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_case_email_translation();
