GRANT EXECUTE ON FUNCTION @SCHEMA@.current_tenant_id(),@SCHEMA@.current_principal_id(),@SCHEMA@.case_staff_scope(uuid,uuid,boolean) TO @REPLY@;
GRANT SELECT(tenant_id,id,principal_id,organization_id,version,active) ON @SCHEMA@.staff_memberships TO @REPLY@;
CREATE POLICY private_history_membership_metadata ON @SCHEMA@.staff_memberships FOR SELECT TO @REPLY@ USING(true);
GRANT EXECUTE ON FUNCTION @SCHEMA@.case_target_membership(uuid,uuid,uuid) TO @REPLY@;
CREATE FUNCTION @SCHEMA@.source_email_recipient(case_uuid uuid,conversation_uuid uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT jsonb_build_object('tenant_id',c.tenant_id,'principal_id',ct.principal_id,'tenant_active',t.active,'principal_active',p.active,
    'participant_active',cp.principal_id IS NOT NULL AND cp.revoked_at IS NULL,'contact_id',ct.id,'verified',ct.verified_at IS NOT NULL,
    'owner_principal_id',c.owner_principal_id,'recipient',ct.email_address,'account_id',a.id,'account_active',a.active,'sender',a.sending_identity,'reply_to',a.reply_address)
  FROM @SCHEMA@.cases c JOIN @SCHEMA@.conversations v ON(v.tenant_id,v.case_id)=(c.tenant_id,c.id)
  JOIN @SCHEMA@.tenants t ON t.id=c.tenant_id JOIN @SCHEMA@.contacts ct ON(ct.tenant_id,ct.id)=(c.tenant_id,c.followup_contact_id)
  JOIN @SCHEMA@.principals p ON(p.tenant_id,p.id)=(ct.tenant_id,ct.principal_id)
  LEFT JOIN @SCHEMA@.case_participants cp ON(cp.tenant_id,cp.case_id,cp.principal_id)=(c.tenant_id,c.id,ct.principal_id)
  JOIN @SCHEMA@.email_accounts a ON a.tenant_id=c.tenant_id AND a.provider='resend'
  WHERE c.tenant_id=@SCHEMA@.current_tenant_id() AND c.id=case_uuid AND v.id=conversation_uuid
    AND @SCHEMA@.case_staff_scope(c.organization_id,c.queue_id,false) AND v.mode='human' AND c.assigned_membership_id=v.handler_membership_id
    AND(@SCHEMA@.case_target_membership(v.handler_membership_id,c.organization_id,c.queue_id)->>'principal_id')::uuid=@SCHEMA@.current_principal_id()
$$;
REVOKE ALL ON FUNCTION @SCHEMA@.source_email_recipient(uuid,uuid) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.source_email_recipient(uuid,uuid) OWNER TO @REPLY@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.source_email_recipient(uuid,uuid) TO @RUNTIME@;
CREATE FUNCTION @SCHEMA@.mail_private_history_context(case_uuid uuid,view_name text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t uuid:=@SCHEMA@.current_tenant_id();c @SCHEMA@.cases%ROWTYPE;scope_version bigint;groups jsonb;items jsonb;required jsonb;BEGIN
  IF view_name IS NULL OR view_name NOT IN('replies','deliveries') THEN RETURN NULL;END IF;
  SELECT * INTO c FROM @SCHEMA@.cases WHERE tenant_id=t AND id=case_uuid;
  IF NOT FOUND OR NOT @SCHEMA@.case_staff_scope(c.organization_id,c.queue_id,false) THEN RETURN NULL;END IF;
  SELECT m.version INTO scope_version FROM @SCHEMA@.staff_memberships m WHERE m.tenant_id=t AND m.principal_id=@SCHEMA@.current_principal_id()
    AND m.organization_id=c.organization_id AND m.active AND @SCHEMA@.case_member_metadata(m.id,c.organization_id,c.queue_id) IS NOT NULL;
  IF scope_version IS NULL THEN RETURN NULL;END IF;
  groups:=jsonb_build_object('case_labels',c.resource_security_labels,
    'conversation_labels',(SELECT @SCHEMA@.mail_labels_union(v.resource_security_labels) FROM @SCHEMA@.conversations v WHERE v.tenant_id=t AND(v.case_id=c.id OR v.id=c.origin_conversation_id)),
    'draft_labels',(SELECT @SCHEMA@.mail_labels_union(d.resource_security_labels) FROM @SCHEMA@.action_drafts d WHERE d.tenant_id=t AND(d.case_id=c.id OR d.result_case_id=c.id OR d.id=c.origin_draft_id)),
    'message_labels',(SELECT @SCHEMA@.mail_labels_union(m.resource_security_labels) FROM @SCHEMA@.messages m WHERE m.tenant_id=t AND(m.case_id=c.id OR EXISTS(SELECT 1 FROM @SCHEMA@.conversations v WHERE v.tenant_id=t AND v.id=m.conversation_id AND(v.case_id=c.id OR v.id=c.origin_conversation_id)))),
    'job_labels',(SELECT @SCHEMA@.mail_labels_union(j.resource_security_labels) FROM @SCHEMA@.outbox_jobs j WHERE j.tenant_id=t AND(j.case_id=c.id OR EXISTS(SELECT 1 FROM @SCHEMA@.email_reply_state r WHERE r.tenant_id=t AND r.email_id=j.reply_email_id AND r.case_id=c.id))),
    'email_labels',(SELECT @SCHEMA@.mail_labels_union(e.resource_security_labels) FROM @SCHEMA@.email_messages e WHERE e.tenant_id=t AND(e.case_id=c.id OR EXISTS(SELECT 1 FROM @SCHEMA@.email_reply_state r WHERE r.tenant_id=t AND r.email_id=e.id AND r.case_id=c.id))),
    'reply_labels',(SELECT @SCHEMA@.mail_labels_union(r.resource_security_labels) FROM @SCHEMA@.email_reply_state r WHERE r.tenant_id=t AND r.case_id=c.id),
    'challenge_labels',(SELECT @SCHEMA@.mail_labels_union(ch.resource_security_labels) FROM @SCHEMA@.email_reply_challenges ch WHERE ch.tenant_id=t AND ch.case_id=c.id),
    'reconcile_labels',(SELECT @SCHEMA@.mail_labels_union(j.resource_security_labels) FROM @SCHEMA@.outbox_jobs j WHERE j.tenant_id=t AND j.case_id=c.id AND j.kind='email_reconcile'),
    'history_labels',(SELECT @SCHEMA@.mail_labels_union(h.resource_security_labels) FROM @SCHEMA@.case_history h WHERE h.tenant_id=t AND h.case_id=c.id));
  SELECT @SCHEMA@.source_labels_union(array_agg(value)) INTO required FROM jsonb_each(groups);
  IF view_name='replies' THEN
    SELECT coalesce(jsonb_agg(to_jsonb(rowdata) ORDER BY rowdata.created_at,rowdata.email_id),'[]') INTO items FROM(
      SELECT r.email_id,r.state,r.reason,r.app_message_id,r.created_at,
        CASE WHEN r.state='published' AND e.verification_state='verified' AND m.email_source_id=e.id AND m.case_id=c.id AND m.author_principal_id=r.principal_id THEN m.body ELSE NULL END AS body,
        CASE WHEN ch.state='pending' AND ch.expires_at<=clock_timestamp() THEN 'expired' ELSE ch.state END AS verification_state,ch.send_state,ch.attempts
      FROM @SCHEMA@.email_reply_state r JOIN @SCHEMA@.email_messages e ON(e.tenant_id,e.id)=(r.tenant_id,r.email_id)
      LEFT JOIN @SCHEMA@.messages m ON(m.tenant_id,m.id)=(r.tenant_id,r.app_message_id)
      LEFT JOIN @SCHEMA@.email_reply_challenges ch ON(ch.tenant_id,ch.email_id)=(r.tenant_id,r.email_id)
      WHERE r.tenant_id=t AND r.case_id=c.id ORDER BY r.created_at,r.email_id LIMIT 100) rowdata;
  ELSE
    SELECT coalesce(jsonb_agg(to_jsonb(rowdata) ORDER BY rowdata.queued_at DESC,rowdata.job_id DESC),'[]') INTO items FROM(
      SELECT j.id AS job_id,coalesce(e.app_message_id,j.source_message_id) AS app_message_id,e.id AS email_message_id,
        coalesce(e.delivery_status,CASE WHEN j.state IN('blocked','dead') THEN 'failed' WHEN j.state='unknown' THEN 'delivery_unknown' ELSE 'queued' END) AS delivery_status,
        e.delivery_version,coalesce(e.needs_review,false) OR(e.id IS NULL AND j.state IN('blocked','dead','unknown')) OR
          (coalesce(e.delivery_status,'') NOT IN('delivered','bounced','failed') AND coalesce(reconcile.state IN('blocked','dead','unknown'),false)) AS needs_review,
        coalesce(e.review_reason,CASE WHEN coalesce(e.delivery_status,'') NOT IN('delivered','bounced','failed') AND reconcile.state IN('blocked','dead','unknown') THEN 'RECONCILE_STOPPED'
          WHEN e.id IS NULL AND j.state IN('blocked','dead','unknown') THEN 'SEND_NOT_CONFIRMED' END) AS review_reason,
        j.created_at AS queued_at,e.provider_status_at,e.last_checked_at
      FROM @SCHEMA@.outbox_jobs j LEFT JOIN @SCHEMA@.email_messages e ON(e.tenant_id,e.job_id)=(j.tenant_id,j.id) AND e.direction='outbound'
      LEFT JOIN @SCHEMA@.outbox_jobs reconcile ON(reconcile.tenant_id,reconcile.email_message_id)=(e.tenant_id,e.id) AND reconcile.kind='email_reconcile'
      WHERE j.tenant_id=t AND j.case_id=c.id AND j.kind IN('email_send','case_created') ORDER BY j.created_at DESC,j.id DESC LIMIT 101) rowdata;
  END IF;
  IF NOT @SCHEMA@.case_staff_scope(c.organization_id,c.queue_id,false) THEN RETURN NULL;END IF;
  RETURN groups||jsonb_build_object('classification_version',2,'tenant_id',t,'case_id',c.id,'view',view_name,'case_version',c.version,'scope_version',scope_version,'labels',required,'items',items);
END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.mail_private_history_context(uuid,text) FROM PUBLIC;
ALTER FUNCTION @SCHEMA@.mail_private_history_context(uuid,text) OWNER TO @REPLY@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.mail_private_history_context(uuid,text) TO @RUNTIME@;

-- Inventory remains distinct from executable readiness. This version only
-- reports an installed graph after its fixed role ACL/forced-RLS checks pass.
CREATE FUNCTION @SCHEMA@.private_mail_graph_ready() RETURNS boolean LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE signature text;guard record;attributes record;relation text;BEGIN
  IF EXISTS(SELECT 1 FROM pg_roles WHERE oid='@WORKER@'::regrole AND(rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolinherit)) THEN RETURN false;END IF;
  FOREACH signature IN ARRAY ARRAY[
    '@SCHEMA@.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)',
    '@SCHEMA@.mail_private_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',
    '@SCHEMA@.mail_private_send_snapshot(uuid,uuid,uuid,uuid,bigint,uuid)',
    '@SCHEMA@.mail_private_abort_unsent(uuid,uuid,uuid,uuid,bigint,uuid,text)',
    '@SCHEMA@.mail_private_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text,text)',
    '@SCHEMA@.email_private_reply_candidate(uuid,uuid,uuid,uuid,bigint)',
    '@SCHEMA@.email_private_reply_apply(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)',
    '@SCHEMA@.mail_private_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)',
    '@SCHEMA@.mail_private_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)'] LOOP
    IF to_regprocedure(signature) IS NULL THEN RETURN false;END IF;
    IF NOT has_function_privilege('@WORKER@'::regrole,signature,'EXECUTE') THEN RETURN false;END IF;
    IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_roles role ON role.oid=p.proowner WHERE p.oid=to_regprocedure(signature)
      AND(NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] OR role.rolcanlogin OR role.rolinherit OR role.rolsuper OR role.rolbypassrls
        OR p.proowner NOT IN('@MAIL@'::regrole,'@REPLY@'::regrole)
        OR has_schema_privilege(p.proowner,'@SCHEMA@'::regnamespace,'CREATE')
        OR pg_has_role('@RUNTIME@'::regrole,p.proowner,'MEMBER') OR pg_has_role('@WORKER@'::regrole,p.proowner,'MEMBER')
        OR EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE')))
      THEN RETURN false;END IF;
  END LOOP;
  signature:='@SCHEMA@.mail_private_history_context(uuid,text)';
  IF to_regprocedure(signature) IS NULL THEN RETURN false;END IF;
  IF NOT has_function_privilege('@RUNTIME@'::regrole,signature,'EXECUTE') OR has_function_privilege('@WORKER@'::regrole,signature,'EXECUTE') THEN RETURN false;END IF;
  IF EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure(signature) AND(p.proowner<>'@REPLY@'::regrole OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
    OR EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'))) THEN RETURN false;END IF;
  FOR guard IN SELECT * FROM(VALUES
    ('email_messages','b_private_mail_inherit','inherit_private_mail_graph()',false),
    ('email_messages','private_mail_graph','validate_private_mail_graph()',true),
    ('email_messages','z_approved_private_mail','guard_approved_private_mail()',false),
    ('email_reply_state','b_private_mail_inherit','inherit_private_mail_graph()',false),
    ('email_reply_state','private_mail_graph','validate_private_mail_graph()',true),
    ('email_reply_challenges','b_private_mail_inherit','inherit_private_mail_graph()',false),
    ('email_reply_challenges','c_private_challenge','guard_private_challenge()',false),
    ('outbox_jobs','b_private_mail_inherit','inherit_private_mail_graph()',false),
    ('outbox_jobs','private_mail_graph','validate_private_mail_graph()',true),
    ('email_delivery_events','b_private_mail_inherit','inherit_private_mail_graph()',false),
    ('email_delivery_events','private_mail_graph','validate_private_mail_graph()',true),
    ('cases','a_private_source_inherit','inherit_private_source_graph()',false),
    ('cases','private_source_graph','validate_private_source_graph()',true),
    ('messages','a_private_source_inherit','inherit_private_source_graph()',false),
    ('messages','private_source_graph','validate_private_source_graph()',true),
    ('conversations','a_private_source_inherit','inherit_private_source_graph()',false),
    ('conversations','private_source_graph','validate_private_source_graph()',true),
    ('action_drafts','a_private_source_inherit','inherit_private_source_graph()',false),
    ('action_drafts','private_source_graph','validate_private_source_graph()',true)
  ) expected(table_name,trigger_name,function_name,deferred) LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_trigger trigger JOIN pg_class c ON c.oid=trigger.tgrelid WHERE c.oid=to_regclass('@SCHEMA@.'||guard.table_name)
      AND trigger.tgname=guard.trigger_name AND trigger.tgfoid=to_regprocedure('@SCHEMA@.'||guard.function_name) AND NOT trigger.tgisinternal
      AND trigger.tgenabled IN('O','A') AND trigger.tgdeferrable=guard.deferred AND trigger.tginitdeferred=guard.deferred) THEN RETURN false;END IF;
    SELECT p.prosecdef,p.proowner,p.proconfig INTO attributes FROM pg_proc p WHERE p.oid=to_regprocedure('@SCHEMA@.'||guard.function_name);
    IF attributes.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] THEN RETURN false;END IF;
    IF guard.function_name IN('inherit_private_source_graph()','validate_private_source_graph()','inherit_private_mail_graph()','validate_private_mail_graph()') THEN
      IF NOT attributes.prosecdef OR attributes.proowner<>'@GRAPH@'::regrole THEN RETURN false;END IF;
    ELSIF guard.function_name='guard_private_challenge()' THEN
      IF NOT attributes.prosecdef OR attributes.proowner<>'@REPLY@'::regrole THEN RETURN false;END IF;
    ELSIF attributes.prosecdef THEN RETURN false;END IF;
  END LOOP;
  FOREACH relation IN ARRAY ARRAY['cases','conversations','messages','action_drafts','outbox_jobs','email_messages','email_reply_state','email_reply_challenges','email_delivery_events'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('@SCHEMA@.'||relation) AND conname='private_labels_canonical' AND convalidated) THEN RETURN false;END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('@SCHEMA@.'||relation) AND tgname='private_labels_monotone'
      AND tgenabled IN('O','A') AND tgfoid=to_regprocedure('@SCHEMA@.guard_source_labels_monotone()')) THEN RETURN false;END IF;
  END LOOP;
  IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.oid='@SCHEMA@'::regnamespace AND c.relkind='r' AND c.relname<>'schema_migrations' AND(NOT c.relrowsecurity OR NOT c.relforcerowsecurity)) THEN RETURN false;END IF;
  RETURN true;END $$;
REVOKE ALL ON FUNCTION @SCHEMA@.private_mail_graph_ready() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.private_mail_graph_ready() TO @WORKER@,@RUNTIME@;
CREATE FUNCTION @SCHEMA@.private_mail_ready() RETURNS boolean LANGUAGE sql SET search_path=pg_catalog AS $$
  SELECT current_user::regrole='@WORKER@'::regrole AND @SCHEMA@.private_mail_graph_ready() $$;
CREATE FUNCTION @SCHEMA@.private_mail_history_ready() RETURNS boolean LANGUAGE sql SET search_path=pg_catalog AS $$
  SELECT current_user::regrole='@RUNTIME@'::regrole AND @SCHEMA@.private_mail_graph_ready() $$;
REVOKE ALL ON FUNCTION @SCHEMA@.private_mail_ready(),@SCHEMA@.private_mail_history_ready() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION @SCHEMA@.private_mail_ready() TO @WORKER@;
GRANT EXECUTE ON FUNCTION @SCHEMA@.private_mail_history_ready() TO @RUNTIME@;
