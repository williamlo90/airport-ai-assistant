"""Staff reply receipts and generation-bound AI publication; no model provider."""
from alembic import context,op

revision='0013_chat_publication'
down_revision='0012_chat_handoff'
branch_labels=None
depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime=(quote(context.config.attributes[k]) for k in ('schema','runtime_role'))
    checks=op.get_bind().exec_driver_sql("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",(context.config.attributes['schema']+'.messages',)).all()
    xor=[name for name,definition in checks if 'author_principal_id' in definition and 'author_session_id' in definition and '<>' in definition]
    if len(xor)!=1:raise RuntimeError('Unexpected message author constraint; upgrade aborted.')
    op.execute(f"ALTER TABLE {q}.messages DROP CONSTRAINT {quote(xor[0])},DROP CONSTRAINT messages_author_type_check,DROP CONSTRAINT messages_source_kind_check")
    op.execute(f"""CREATE TABLE {q}.ai_generations(
      tenant_id uuid NOT NULL,id uuid NOT NULL,conversation_id uuid NOT NULL,origin_session_id uuid NOT NULL,principal_id uuid,
      input_version bigint NOT NULL CHECK(input_version>0),generation_epoch bigint NOT NULL CHECK(generation_epoch>0),
      language text NOT NULL CHECK(language IN('en','id')),nonce_hash text NOT NULL CHECK(nonce_hash ~ '^[a-f0-9]{{64}}$'),
      created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,expires_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP+interval '5 minutes',
      state text NOT NULL DEFAULT 'pending' CHECK(state IN('pending','published','stale')),message_id uuid,
      CHECK(expires_at>created_at AND expires_at<=created_at+interval '5 minutes'),
      CHECK((state='published')=(message_id IS NOT NULL)),PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,id,conversation_id),
      FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
      FOREIGN KEY(tenant_id,origin_session_id) REFERENCES {q}.sessions(tenant_id,id),
      FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,message_id) REFERENCES {q}.messages(tenant_id,id) DEFERRABLE INITIALLY DEFERRED)""")
    op.execute(f"""ALTER TABLE {q}.messages ADD COLUMN created_session_id uuid,ADD COLUMN sender_membership_id uuid,
      ADD COLUMN staff_request_key text,ADD COLUMN request_hash text,ADD COLUMN generation_id uuid,
      ADD COLUMN basis_case_version bigint,ADD COLUMN basis_conversation_version bigint,ADD COLUMN basis_epoch bigint,
      ADD CONSTRAINT message_created_session_fk FOREIGN KEY(tenant_id,created_session_id) REFERENCES {q}.sessions(tenant_id,id),
      ADD CONSTRAINT message_sender_fk FOREIGN KEY(tenant_id,sender_membership_id) REFERENCES {q}.staff_memberships(tenant_id,id),
      ADD CONSTRAINT message_generation_fk FOREIGN KEY(tenant_id,generation_id,conversation_id) REFERENCES {q}.ai_generations(tenant_id,id,conversation_id),
      ADD CONSTRAINT messages_author_type_check CHECK(author_type IN('customer','staff','ai')),
      ADD CONSTRAINT messages_source_kind_check CHECK(source_kind IN('user_report','staff_reply','ai_response')),
      ADD CONSTRAINT message_author_shape CHECK(
        (author_type='customer' AND((author_principal_id IS NULL)<>(author_session_id IS NULL)) AND source_kind='user_report' AND sender_membership_id IS NULL AND staff_request_key IS NULL AND request_hash IS NULL AND generation_id IS NULL AND created_session_id IS NULL AND basis_case_version IS NULL AND basis_conversation_version IS NULL AND basis_epoch IS NULL)
        OR(author_type='staff' AND author_principal_id IS NOT NULL AND author_session_id IS NULL AND origin_session_id IS NULL AND source_kind='staff_reply' AND conversation_id IS NOT NULL AND case_id IS NULL AND created_session_id IS NOT NULL AND sender_membership_id IS NOT NULL AND staff_request_key IS NOT NULL AND length(staff_request_key) BETWEEN 16 AND 128 AND request_hash IS NOT NULL AND request_hash ~ '^[a-f0-9]{{64}}$' AND generation_id IS NULL AND basis_case_version>0 AND basis_conversation_version>0 AND basis_epoch>0 AND basis_case_version IS NOT NULL AND basis_conversation_version IS NOT NULL AND basis_epoch IS NOT NULL)
        OR(author_type='ai' AND author_principal_id IS NULL AND author_session_id IS NULL AND origin_session_id IS NULL AND source_kind='ai_response' AND conversation_id IS NOT NULL AND case_id IS NULL AND created_session_id IS NOT NULL AND sender_membership_id IS NULL AND staff_request_key IS NULL AND request_hash IS NULL AND generation_id IS NOT NULL AND basis_case_version IS NULL AND basis_conversation_version>0 AND basis_epoch>0 AND basis_conversation_version IS NOT NULL AND basis_epoch IS NOT NULL))""")
    op.execute(f"CREATE UNIQUE INDEX staff_message_request ON {q}.messages(tenant_id,author_principal_id,staff_request_key) WHERE author_type='staff'")
    op.execute(f"CREATE UNIQUE INDEX generation_message ON {q}.messages(tenant_id,generation_id) WHERE generation_id IS NOT NULL")
    op.execute(f"GRANT INSERT(source_kind,created_session_id,sender_membership_id,staff_request_key,request_hash,generation_id,basis_case_version,basis_conversation_version,basis_epoch) ON {q}.messages TO {runtime}")
    for name in ('runtime_insert','runtime_case_insert'):
        expr=op.get_bind().exec_driver_sql('SELECT pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid=%s::regclass AND polname=%s',(context.config.attributes['schema']+'.messages',name)).scalar_one()
        op.execute(f"ALTER POLICY {name} ON {q}.messages WITH CHECK(({expr}) AND author_type='customer')")
    op.execute(f"ALTER TABLE {q}.ai_generations ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {q}.ai_generations FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {q}.ai_generations FROM PUBLIC")
    op.execute(f"GRANT SELECT ON {q}.ai_generations TO {runtime}")
    op.execute(f"GRANT INSERT(tenant_id,id,conversation_id,origin_session_id,principal_id,input_version,generation_epoch,language,nonce_hash) ON {q}.ai_generations TO {runtime}")
    op.execute(f"GRANT UPDATE(state,message_id) ON {q}.ai_generations TO {runtime}")
    scope=f"tenant_id={q}.current_tenant_id() AND origin_session_id={q}.current_session_id() AND principal_id IS NOT DISTINCT FROM {q}.current_principal_id() AND {q}.conversation_session_valid() AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=ai_generations.tenant_id AND c.id=ai_generations.conversation_id)"
    op.execute(f"CREATE POLICY owner_read ON {q}.ai_generations FOR SELECT TO {runtime} USING({scope})")
    op.execute(f"CREATE POLICY owner_create ON {q}.ai_generations FOR INSERT TO {runtime} WITH CHECK({scope} AND state='pending')")
    op.execute(f"CREATE POLICY owner_finish ON {q}.ai_generations FOR UPDATE TO {runtime} USING({scope}) WITH CHECK({scope})")
    op.execute(f"CREATE POLICY runtime_staff_message ON {q}.messages FOR INSERT TO {runtime} WITH CHECK(tenant_id={q}.current_tenant_id() AND author_type='staff' AND author_principal_id={q}.current_principal_id() AND created_session_id={q}.current_session_id() AND {q}.case_staff_session_valid() AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.conversation_id AND c.mode='human' AND c.handler_membership_id=messages.sender_membership_id))")
    op.execute(f"CREATE POLICY runtime_ai_message ON {q}.messages FOR INSERT TO {runtime} WITH CHECK(tenant_id={q}.current_tenant_id() AND author_type='ai' AND created_session_id={q}.current_session_id() AND {q}.conversation_session_valid() AND EXISTS(SELECT 1 FROM {q}.ai_generations g WHERE g.tenant_id=messages.tenant_id AND g.id=messages.generation_id AND g.conversation_id=messages.conversation_id AND g.state='pending'))")
    _guards(q)
    _events(q)


def _guards(q):
    op.execute(f"DROP TRIGGER ended_message_guard ON {q}.messages")
    op.execute(f"""CREATE FUNCTION {q}.guard_message_publication() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE v {q}.conversations%ROWTYPE;c {q}.cases%ROWTYPE;g {q}.ai_generations%ROWTYPE;cid uuid;meta jsonb;BEGIN
        IF NEW.conversation_id IS NULL THEN RETURN NEW;END IF;
        SELECT case_id INTO cid FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id;
        IF cid IS NOT NULL THEN SELECT * INTO c FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=cid FOR UPDATE;END IF;
        SELECT * INTO v FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Conversation unavailable' USING ERRCODE='42501';END IF;
        IF v.case_id IS DISTINCT FROM cid THEN RAISE EXCEPTION 'Conversation context changed' USING ERRCODE='23514';END IF;
        IF v.mode='ended' THEN RAISE EXCEPTION 'Conversation ended' USING ERRCODE='23514';END IF;
        IF NEW.author_type='staff' THEN
          IF NOT {q}.case_staff_session_valid() OR c.id IS NULL OR c.status='closed' OR v.mode<>'human' OR NEW.created_session_id IS DISTINCT FROM {q}.current_session_id()
            OR NEW.author_principal_id IS DISTINCT FROM {q}.current_principal_id() OR NEW.sender_membership_id IS DISTINCT FROM v.handler_membership_id OR NEW.sender_membership_id IS DISTINCT FROM c.assigned_membership_id
            OR NEW.basis_case_version IS DISTINCT FROM c.version OR NEW.basis_conversation_version+1 IS DISTINCT FROM v.version OR NEW.basis_epoch IS DISTINCT FROM v.generation_epoch THEN
            RAISE EXCEPTION 'Staff send context changed' USING ERRCODE='23514';END IF;
          meta:={q}.case_target_membership(NEW.sender_membership_id,c.organization_id,c.queue_id);
          IF meta IS NULL OR(meta->>'principal_id')::uuid IS DISTINCT FROM {q}.current_principal_id() THEN RAISE EXCEPTION 'Sender unavailable' USING ERRCODE='42501';END IF;
        ELSIF NEW.author_type='ai' THEN
          SELECT * INTO g FROM {q}.ai_generations WHERE tenant_id=NEW.tenant_id AND id=NEW.generation_id FOR UPDATE;
          IF FOUND AND g.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Generation deadline crossed' USING ERRCODE='Z0013';END IF;
          IF NOT FOUND OR g.state<>'pending' OR g.conversation_id<>v.id OR g.origin_session_id IS DISTINCT FROM {q}.current_session_id()
            OR g.principal_id IS DISTINCT FROM {q}.current_principal_id() OR NEW.created_session_id IS DISTINCT FROM g.origin_session_id OR NOT {q}.conversation_session_valid()
            OR v.mode<>'ai' OR v.case_id IS NOT NULL OR g.input_version+1<>v.version OR g.generation_epoch<>v.generation_epoch OR NEW.language<>g.language OR g.language<>v.language
            OR NEW.basis_conversation_version IS DISTINCT FROM g.input_version OR NEW.basis_epoch IS DISTINCT FROM g.generation_epoch THEN RAISE EXCEPTION 'AI publication context changed' USING ERRCODE='23514';END IF;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM {q}.tenants t WHERE t.id=NEW.tenant_id AND NEW.language=ANY(t.supported_languages)) THEN RAISE EXCEPTION 'Unsupported message language' USING ERRCODE='23514';END IF;
        IF NEW.sequence<>v.last_sequence THEN RAISE EXCEPTION 'Message sequence mismatch' USING ERRCODE='23514';END IF;
        RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_message_publication() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER message_publication_guard BEFORE INSERT ON {q}.messages FOR EACH ROW EXECUTE FUNCTION {q}.guard_message_publication()")
    op.execute(f"""CREATE FUNCTION {q}.guard_generation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE v {q}.conversations%ROWTYPE;BEGIN
        IF TG_OP='INSERT' THEN
          SELECT * INTO v FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id FOR UPDATE;
          IF NOT FOUND OR v.mode<>'ai' OR v.case_id IS NOT NULL OR v.version<>NEW.input_version OR v.generation_epoch<>NEW.generation_epoch OR v.language<>NEW.language OR NOT EXISTS(SELECT 1 FROM {q}.tenants t WHERE t.id=NEW.tenant_id AND NEW.language=ANY(t.supported_languages)) THEN RAISE EXCEPTION 'Generation context unavailable' USING ERRCODE='23514';END IF;
        ELSE
          IF OLD.state<>'pending' OR NEW.state NOT IN('published','stale') THEN RAISE EXCEPTION 'Generation terminal' USING ERRCODE='23514';END IF;
        END IF;RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_generation() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER generation_guard BEFORE INSERT OR UPDATE ON {q}.ai_generations FOR EACH ROW EXECUTE FUNCTION {q}.guard_generation()")
    op.execute(f"""CREATE FUNCTION {q}.validate_ai_publication() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE gid uuid;g {q}.ai_generations%ROWTYPE;m {q}.messages%ROWTYPE;BEGIN
        IF TG_TABLE_NAME='messages' THEN IF NEW.author_type<>'ai' THEN RETURN NULL;END IF;gid:=NEW.generation_id;
        ELSE IF NEW.state<>'published' THEN RETURN NULL;END IF;gid:=NEW.id;END IF;
        SELECT * INTO g FROM {q}.ai_generations WHERE tenant_id=NEW.tenant_id AND id=gid;
        SELECT * INTO m FROM {q}.messages WHERE tenant_id=NEW.tenant_id AND generation_id=gid;
        IF g.id IS NULL OR m.id IS NULL OR g.state<>'published' OR g.message_id IS DISTINCT FROM m.id OR m.author_type<>'ai' OR m.conversation_id<>g.conversation_id
          OR m.created_session_id<>g.origin_session_id OR m.basis_conversation_version<>g.input_version OR m.basis_epoch<>g.generation_epoch THEN RAISE EXCEPTION 'Incomplete AI publication' USING ERRCODE='23514';END IF;
        RETURN NULL;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.validate_ai_publication() FROM PUBLIC")
    op.execute(f"CREATE CONSTRAINT TRIGGER ai_message_complete AFTER INSERT ON {q}.messages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_ai_publication()")
    op.execute(f"CREATE CONSTRAINT TRIGGER generation_complete AFTER UPDATE ON {q}.ai_generations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_ai_publication()")


def _events(q):
    op.execute(f"""ALTER TABLE {q}.conversation_events DROP CONSTRAINT conversation_events_event_type_check,DROP CONSTRAINT conversation_events_check,
      ADD CONSTRAINT conversation_events_event_type_check CHECK(event_type IN('message_received','language_changed','handoff_requested','chat_accepted','chat_reassigned','chat_ended','chat_updated','staff_message','ai_message')),
      ADD CONSTRAINT conversation_events_check CHECK(
        (event_type IN('message_received','staff_message','ai_message') AND message_id IS NOT NULL AND language IS NOT NULL AND case_id IS NULL AND conversation_mode IS NULL AND transition_id IS NULL)
        OR(event_type='language_changed' AND message_id IS NULL AND language IS NOT NULL AND case_id IS NULL AND conversation_mode IS NULL AND transition_id IS NULL)
        OR(event_type='handoff_requested' AND message_id IS NULL AND language IS NULL AND case_id IS NOT NULL AND conversation_mode='waiting_staff' AND transition_id IS NULL)
        OR(event_type IN('chat_accepted','chat_reassigned','chat_ended','chat_updated') AND message_id IS NULL AND language IS NULL AND case_id IS NOT NULL AND conversation_mode IS NOT NULL AND transition_id IS NOT NULL))""")
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(f'{q}.emit_conversation_event()',)).scalar_one()
    old="kind:='message_received';"
    if definition.count(old)!=1:raise RuntimeError('Unexpected event emitter; upgrade aborted.')
    op.execute(definition.replace(old,"kind:=CASE NEW.author_type WHEN 'staff' THEN 'staff_message' WHEN 'ai' THEN 'ai_message' ELSE 'message_received' END;"))


def downgrade():raise RuntimeError('Message publication downgrade requires a reviewed recovery migration.')
