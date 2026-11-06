CREATE FUNCTION @SCHEMA@.case_email_context(case_uuid uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object('tenant_id',c.tenant_id,'case_id',c.id,'case_version',c.version,
    'assigned_membership_id',c.assigned_membership_id,'status',c.status,
    'last_sequence',(SELECT coalesce(max(m.sequence),0) FROM @SCHEMA@.messages m WHERE m.tenant_id=c.tenant_id AND m.case_id=c.id),
    'case_labels',c.resource_security_labels,'draft_labels',d.resource_security_labels,'conversation_labels',v.resource_security_labels,
    'recipient',jsonb_build_object('tenant_id',c.tenant_id,'principal_id',ct.principal_id,'tenant_active',t.active,'principal_active',p.active,
      'source_subject_ref',p.source_subject_ref,'participant_active',cp.principal_id IS NOT NULL AND cp.revoked_at IS NULL,
      'contact_id',ct.id,'verified',ct.verified_at IS NOT NULL,'owner_principal_id',c.owner_principal_id,
      'recipient',ct.email_address,'account_id',a.id,'account_active',a.active,'sender',a.sending_identity,'reply_to',a.reply_address))
  FROM @SCHEMA@.cases c JOIN @SCHEMA@.action_drafts d ON(d.tenant_id,d.id)=(c.tenant_id,c.origin_draft_id)
  JOIN @SCHEMA@.conversations v ON(v.tenant_id,v.id)=(c.tenant_id,c.origin_conversation_id)
  JOIN @SCHEMA@.tenants t ON t.id=c.tenant_id
  JOIN @SCHEMA@.contacts ct ON(ct.tenant_id,ct.id)=(c.tenant_id,c.followup_contact_id) AND ct.id=c.owner_contact_id
  JOIN @SCHEMA@.principals p ON(p.tenant_id,p.id)=(ct.tenant_id,ct.principal_id)
  LEFT JOIN @SCHEMA@.case_participants cp ON(cp.tenant_id,cp.case_id,cp.principal_id)=(c.tenant_id,c.id,ct.principal_id)
  JOIN @SCHEMA@.email_accounts a ON a.tenant_id=c.tenant_id AND a.provider='resend'
  WHERE c.tenant_id=@SCHEMA@.current_tenant_id() AND c.id=case_uuid
    AND @SCHEMA@.case_staff_scope(c.organization_id,c.queue_id,false)
    AND(@SCHEMA@.case_target_membership(c.assigned_membership_id,c.organization_id,c.queue_id)->>'principal_id')::uuid=@SCHEMA@.current_principal_id()
$$;
REVOKE ALL ON FUNCTION @SCHEMA@.case_email_context(uuid) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.case_email_context(uuid) OWNER TO @REPLY@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.case_email_context(uuid) TO @RUNTIME@;

CREATE FUNCTION @SCHEMA@.case_correspondence_ancestry(case_uuid uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT @SCHEMA@.source_labels_union(ARRAY[c.resource_security_labels,d.resource_security_labels,v.resource_security_labels])
  FROM @SCHEMA@.cases c JOIN @SCHEMA@.action_drafts d ON(d.tenant_id,d.id)=(c.tenant_id,c.origin_draft_id)
  JOIN @SCHEMA@.conversations v ON(v.tenant_id,v.id)=(c.tenant_id,c.origin_conversation_id)
  WHERE c.tenant_id=@SCHEMA@.current_tenant_id() AND c.id=case_uuid
    AND @SCHEMA@.case_staff_scope(c.organization_id,c.queue_id,false)
$$;
REVOKE ALL ON FUNCTION @SCHEMA@.case_correspondence_ancestry(uuid) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.case_correspondence_ancestry(uuid) OWNER TO @REPLY@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.case_correspondence_ancestry(uuid) TO @RUNTIME@;

CREATE POLICY runtime_case_email ON @SCHEMA@.messages FOR INSERT TO @RUNTIME@ WITH CHECK(
  tenant_id=@SCHEMA@.current_tenant_id() AND author_type='staff' AND conversation_id IS NULL AND case_id IS NOT NULL
  AND delivery_channel='email' AND author_principal_id=@SCHEMA@.current_principal_id()
  AND created_session_id=@SCHEMA@.current_session_id() AND @SCHEMA@.case_staff_session_valid()
  AND EXISTS(SELECT 1 FROM @SCHEMA@.cases c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.case_id
    AND c.status<>'closed' AND c.assigned_membership_id=messages.sender_membership_id
    AND(@SCHEMA@.case_target_membership(c.assigned_membership_id,c.organization_id,c.queue_id)->>'principal_id')::uuid=@SCHEMA@.current_principal_id()));

CREATE FUNCTION @SCHEMA@.guard_case_email() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c @SCHEMA@.cases%ROWTYPE;ctx jsonb;seq bigint;BEGIN
  IF NEW.author_type<>'staff' OR NEW.conversation_id IS NOT NULL THEN RETURN NEW;END IF;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id FOR UPDATE;
  IF NOT FOUND OR NOT @SCHEMA@.case_staff_session_valid() OR c.status='closed'
    OR NEW.created_session_id IS DISTINCT FROM @SCHEMA@.current_session_id()
    OR NEW.author_principal_id IS DISTINCT FROM @SCHEMA@.current_principal_id()
    OR NEW.sender_membership_id IS DISTINCT FROM c.assigned_membership_id
    OR NEW.basis_case_version IS DISTINCT FROM c.version OR NEW.delivery_channel<>'email'
    OR NEW.basis_conversation_version IS NOT NULL OR NEW.basis_epoch IS NOT NULL THEN
    RAISE EXCEPTION 'Case email context changed' USING ERRCODE='23514';END IF;
  ctx:=@SCHEMA@.case_email_context(c.id);
  IF ctx IS NULL OR ctx->'recipient'->>'tenant_active' IS DISTINCT FROM 'true'
    OR ctx->'recipient'->>'principal_active' IS DISTINCT FROM 'true' OR ctx->'recipient'->>'participant_active' IS DISTINCT FROM 'true'
    OR ctx->'recipient'->>'verified' IS DISTINCT FROM 'true' OR ctx->'recipient'->>'account_active' IS DISTINCT FROM 'true'
    OR ctx->'recipient'->>'principal_id' IS DISTINCT FROM ctx->'recipient'->>'owner_principal_id'
    OR NEW.resource_security_labels IS DISTINCT FROM @SCHEMA@.source_labels_union(ARRAY[ctx->'case_labels',ctx->'draft_labels',ctx->'conversation_labels']) THEN
    RAISE EXCEPTION 'Case email recipient unavailable' USING ERRCODE='42501';END IF;
  SELECT coalesce(max(sequence),0)+1 INTO seq FROM @SCHEMA@.messages WHERE tenant_id=NEW.tenant_id AND case_id=NEW.case_id;
  IF NEW.sequence IS DISTINCT FROM seq OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.tenants t WHERE t.id=NEW.tenant_id AND NEW.language=ANY(t.supported_languages)) THEN
    RAISE EXCEPTION 'Case email sequence or language invalid' USING ERRCODE='23514';END IF;
  RETURN NEW;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.guard_case_email() FROM PUBLIC;
CREATE TRIGGER case_email_guard BEFORE INSERT ON @SCHEMA@.messages FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.guard_case_email();

CREATE FUNCTION @SCHEMA@.case_email_ready() RETURNS boolean LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF to_regprocedure('@SCHEMA@.case_email_context(uuid)') IS NULL
    OR to_regprocedure('@SCHEMA@.case_correspondence_ancestry(uuid)') IS NULL
    OR to_regprocedure('@SCHEMA@.guard_case_email()') IS NULL THEN RETURN false;END IF;
  RETURN(SELECT @SCHEMA@.private_mail_graph_ready()
    AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='@SCHEMA@.messages'::regclass AND tgname='case_email_guard'
      AND tgenabled='O' AND tgfoid='@SCHEMA@.guard_case_email()'::regprocedure AND tgtype=7 AND NOT tgisinternal)
    AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='@SCHEMA@.messages'::regclass AND conname='message_author_shape' AND convalidated)
    AND EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='@SCHEMA@.case_email_context(uuid)'::regprocedure
      AND p.prosecdef AND p.proowner='@REPLY@'::regrole AND p.proconfig=ARRAY['search_path=pg_catalog']
      AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
    AND has_function_privilege('@RUNTIME@'::regrole,'@SCHEMA@.case_email_context(uuid)','EXECUTE')
    AND NOT has_function_privilege('@WORKER@'::regrole,'@SCHEMA@.case_email_context(uuid)','EXECUTE'));
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.case_email_ready() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.case_email_ready() TO @RUNTIME@;
