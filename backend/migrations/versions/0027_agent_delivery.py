"""Human-approved staff translation drafts and per-input agent deduplication."""
from alembic import context,op
revision='0027_agent_delivery'
down_revision='0026_agent_runs'
branch_labels=depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,r=(quote(context.config.attributes[k]) for k in ('schema','runtime_role'))
    op.execute(f'CREATE UNIQUE INDEX agent_message_mode ON {q}.agent_runs(tenant_id,session_id,conversation_id,message_id,mode)')
    op.execute(f'''CREATE TABLE {q}.staff_translation_drafts(
      tenant_id uuid NOT NULL,id uuid NOT NULL,case_id uuid NOT NULL,conversation_id uuid NOT NULL,
      principal_id uuid NOT NULL,session_id uuid NOT NULL,request_key uuid NOT NULL,request_hash text NOT NULL CHECK(request_hash~'^[a-f0-9]{{64}}$'),
      original_body text NOT NULL CHECK(length(btrim(original_body))>0 AND length(original_body)<=4000),
      source_language text NOT NULL CHECK(source_language IN('en','id')),target_language text NOT NULL CHECK(target_language IN('en','id') AND target_language<>source_language),
      channel text NOT NULL CHECK(channel IN('chat','email')),case_version bigint NOT NULL CHECK(case_version>0),
      conversation_version bigint NOT NULL CHECK(conversation_version>0),generation_epoch bigint NOT NULL CHECK(generation_epoch>0),
      state text NOT NULL DEFAULT 'generating' CHECK(state IN('generating','ready','failed','sent')),
      translated_body text CHECK(length(btrim(translated_body))>0 AND length(translated_body)<=4000),
      reviewed_body text CHECK(length(btrim(reviewed_body))>0 AND length(reviewed_body)<=4000),message_id uuid,send_key text,
      revision integer NOT NULL DEFAULT 1 CHECK(revision>0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '15 minutes',
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,principal_id,request_key),UNIQUE(tenant_id,message_id),
      FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
      FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
      FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,session_id) REFERENCES {q}.sessions(tenant_id,id),
      FOREIGN KEY(tenant_id,message_id) REFERENCES {q}.messages(tenant_id,id),
      CHECK((state IN('ready','sent'))=(translated_body IS NOT NULL)),
      CHECK((state='sent')=(reviewed_body IS NOT NULL AND message_id IS NOT NULL AND send_key IS NOT NULL)),
      CHECK(state='sent' OR(reviewed_body IS NULL AND message_id IS NULL AND send_key IS NULL)),
      CHECK(expires_at>created_at AND expires_at<=created_at+interval '16 minutes'))''')
    op.execute(f'ALTER TABLE {q}.staff_translation_drafts ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {q}.staff_translation_drafts FORCE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON {q}.staff_translation_drafts FROM PUBLIC')
    op.execute(f'GRANT SELECT,INSERT ON {q}.staff_translation_drafts TO {r}')
    op.execute(f'GRANT UPDATE(state,translated_body,reviewed_body,message_id,send_key,revision) ON {q}.staff_translation_drafts TO {r}')
    gate=f'''tenant_id={q}.current_tenant_id() AND principal_id={q}.current_principal_id() AND session_id={q}.current_session_id()
      AND {q}.case_staff_session_valid() AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=staff_translation_drafts.tenant_id AND c.id=staff_translation_drafts.case_id)
      AND EXISTS(SELECT 1 FROM {q}.conversations v WHERE v.tenant_id=staff_translation_drafts.tenant_id AND v.id=staff_translation_drafts.conversation_id AND v.case_id=staff_translation_drafts.case_id)'''
    for operation in ('SELECT','INSERT','UPDATE'):
        clause=f'WITH CHECK({gate})' if operation=='INSERT' else f'USING({gate})'+(f' WITH CHECK({gate})' if operation=='UPDATE' else '')
        op.execute(f'CREATE POLICY translation_{operation.lower()} ON {q}.staff_translation_drafts FOR {operation} TO {r} {clause}')
    op.execute(f'''CREATE FUNCTION {q}.translation_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE c record;v record;m record;member jsonb;BEGIN
        SELECT * INTO c FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Translation access denied';END IF;
        SELECT * INTO v FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id FOR UPDATE;
        IF NOT FOUND OR v.case_id IS DISTINCT FROM c.id OR NEW.principal_id IS DISTINCT FROM {q}.current_principal_id()
          OR NEW.session_id IS DISTINCT FROM {q}.current_session_id() OR NOT {q}.case_staff_session_valid()
        THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Translation access denied';END IF;
        IF TG_OP='INSERT' THEN
          IF NEW.state<>'generating' OR NEW.revision<>1 OR NEW.created_at<clock_timestamp()-interval '10 seconds'
            OR NEW.created_at>clock_timestamp()+interval '1 second' THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid translation draft';END IF;
        ELSE
          IF(to_jsonb(NEW)-ARRAY['state','translated_body','reviewed_body','message_id','send_key','revision'])
             IS DISTINCT FROM(to_jsonb(OLD)-ARRAY['state','translated_body','reviewed_body','message_id','send_key','revision'])
            OR NEW.revision<>OLD.revision+1 OR NOT((OLD.state='generating' AND NEW.state IN('ready','failed')) OR(OLD.state='ready' AND NEW.state='sent'))
            OR(OLD.state='ready' AND NEW.translated_body IS DISTINCT FROM OLD.translated_body)
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid translation transition';END IF;
        END IF;
        IF NEW.state<>'failed' THEN
          member:={q}.case_target_membership(v.handler_membership_id,c.organization_id,c.queue_id);
          IF c.status='closed' OR v.mode<>'human' OR c.version<>NEW.case_version OR v.generation_epoch<>NEW.generation_epoch
            OR c.assigned_membership_id IS DISTINCT FROM v.handler_membership_id OR member IS NULL
            OR(member->>'principal_id')::uuid IS DISTINCT FROM NEW.principal_id
            OR c.resource_security_labels<>'[]'::jsonb OR v.resource_security_labels<>'[]'::jsonb
            OR NOT EXISTS(SELECT 1 FROM {q}.tenants t WHERE t.id=NEW.tenant_id AND NEW.source_language=ANY(t.supported_languages) AND NEW.target_language=ANY(t.supported_languages))
            OR (NEW.state<>'sent' AND v.version<>NEW.conversation_version)
            OR (NEW.state='ready' AND NEW.created_at+interval '120 seconds'<=clock_timestamp())
            OR NEW.expires_at<=clock_timestamp()
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Translation context changed';END IF;
        END IF;
        IF NEW.state='sent' THEN
          SELECT * INTO m FROM {q}.messages WHERE tenant_id=NEW.tenant_id AND id=NEW.message_id;
          IF NOT FOUND OR m.author_type<>'staff' OR m.conversation_id<>NEW.conversation_id OR m.created_session_id<>NEW.session_id
            OR m.author_principal_id<>NEW.principal_id OR m.body<>NEW.reviewed_body OR m.language<>NEW.target_language
            OR m.staff_request_key<>NEW.send_key OR m.basis_case_version<>NEW.case_version
            OR m.basis_conversation_version<>NEW.conversation_version OR m.basis_epoch<>NEW.generation_epoch
            OR v.version<>NEW.conversation_version+1
          THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid approved message';END IF;
        END IF;
        RETURN NEW;END $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.translation_guard() FROM PUBLIC')
    op.execute(f'CREATE TRIGGER translation_guard BEFORE INSERT OR UPDATE ON {q}.staff_translation_drafts FOR EACH ROW EXECUTE FUNCTION {q}.translation_guard()')


def downgrade():raise RuntimeError('Translation history requires a reviewed forward migration.')
