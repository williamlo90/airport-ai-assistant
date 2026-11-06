"""Durable customer-visible events and consent-bound handoff links."""
from alembic import context,op

revision='0011_conversation_events'
down_revision='0010_attachments'
branch_labels=None
depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,owner=(quote(context.config.attributes[k]) for k in ('schema','runtime_role','auth_role'))
    op.execute(f"""ALTER TABLE {q}.conversations DROP CONSTRAINT conversations_mode_check,DROP CONSTRAINT conversations_case_id_check,
        ADD CONSTRAINT conversations_mode_check CHECK(mode IN('ai','waiting_staff')),
        ADD CONSTRAINT conversation_link_shape CHECK((mode='ai' AND case_id IS NULL) OR(mode='waiting_staff' AND case_id IS NOT NULL)),
        ADD CONSTRAINT conversation_case_fk FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
        ADD COLUMN last_event_sequence bigint NOT NULL DEFAULT 0 CHECK(last_event_sequence>=0)""")
    op.execute(f"CREATE UNIQUE INDEX conversation_waiting_case ON {q}.conversations(tenant_id,case_id) WHERE case_id IS NOT NULL")
    op.execute(f"""ALTER TABLE {q}.action_drafts DROP CONSTRAINT action_drafts_case_id_check,
        ADD CONSTRAINT draft_existing_handoff CHECK(case_id IS NULL OR action_type='request_handoff'),
        ADD CONSTRAINT draft_existing_case_fk FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id)""")
    # Keep customer ownership explicit; linked case access must also remain valid.
    own=f"""tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid() AND resource_security_labels='[]'::jsonb
      AND(({q}.current_actor_kind()='anonymous' AND anonymous_session_id={q}.current_session_id()) OR({q}.current_actor_kind()='customer' AND customer_principal_id={q}.current_principal_id()))"""
    linked=f"(case_id IS NULL OR EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=conversations.tenant_id AND c.id=conversations.case_id))"
    for action in ('select','insert','update'):
        op.execute(f"DROP POLICY runtime_{action} ON {q}.conversations")
        cond=own+' AND '+linked
        if action=='insert':cond+=' AND case_id IS NULL AND mode=\'ai\''
        op.execute(f"CREATE POLICY runtime_{action} ON {q}.conversations FOR {action.upper()} TO {runtime} "+(f'USING({cond}) ' if action!='insert' else '')+(f'WITH CHECK({cond})' if action!='select' else ''))
    op.execute(f"CREATE POLICY chat_staff_read ON {q}.conversations FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND resource_security_labels='[]'::jsonb AND case_id IS NOT NULL AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=conversations.tenant_id AND c.id=conversations.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false)))")
    op.execute(f"GRANT UPDATE(case_id,mode) ON {q}.conversations TO {runtime}")
    op.execute(f"CREATE POLICY chat_anon_lock ON {q}.cases FOR UPDATE TO {runtime} USING(tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid() AND {q}.current_actor_kind()='anonymous' AND owner_anonymous_session_id={q}.current_session_id() AND resource_security_labels='[]'::jsonb AND source_observations='[]'::jsonb) WITH CHECK(false)")
    for action in ('select','insert','update'):
        # Preserve the original draft/session ownership expression exactly,
        # expanding only the previously unsupported existing-case predicate.
        using,check=op.get_bind().exec_driver_sql("SELECT pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid=%s::regclass AND polname=%s",(context.config.attributes['schema']+'.action_drafts','runtime_'+action)).one()
        replacement=f"(case_id IS NULL OR EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=action_drafts.tenant_id AND c.id=action_drafts.case_id))"
        using=using.replace('(case_id IS NULL)',replacement) if using else None
        check=check.replace('(case_id IS NULL)',replacement) if check else None
        op.execute(f"ALTER POLICY runtime_{action} ON {q}.action_drafts "+(f'USING({using}) ' if using else '')+(f'WITH CHECK({check})' if check else ''))
    # Auth adoption retains the same proven origin and source-data exclusion.
    s="nullif(current_setting('app.adoption_session',true),'')::uuid"
    t="nullif(current_setting('app.adoption_tenant',true),'')::uuid"
    p="nullif(current_setting('app.adoption_principal',true),'')::uuid"
    op.execute(f"ALTER POLICY auth_adoption_write ON {q}.conversations WITH CHECK(tenant_id={t} AND adopted_from_session_id={s} AND customer_principal_id={p} AND anonymous_session_id IS NULL AND adopted_at IS NOT NULL AND resource_security_labels='[]'::jsonb)")
    signature=f'{q}.auth_verify(uuid,uuid,text,text,text,uuid,text,text,integer,text,integer,integer,integer)'
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(signature,)).scalar_one()
    old="(case_id IS NOT NULL OR resource_security_labels<>'[]'::jsonb)"
    if definition.count(old)!=1:raise RuntimeError('Unexpected adoption function; upgrade aborted.')
    op.execute(definition.replace(old,"(resource_security_labels<>'[]'::jsonb)"))
    op.execute(f"""CREATE TABLE {q}.conversation_events(
      tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),conversation_id uuid NOT NULL,
      sequence bigint NOT NULL CHECK(sequence>0),event_type text NOT NULL CHECK(event_type IN('message_received','language_changed','handoff_requested')),
      visibility text NOT NULL DEFAULT 'customer' CHECK(visibility='customer'),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      message_id uuid,language text CHECK(language IN('id','en')),case_id uuid,
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,conversation_id,sequence),UNIQUE(tenant_id,message_id),
      FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
      FOREIGN KEY(tenant_id,message_id,conversation_id) REFERENCES {q}.messages(tenant_id,id,conversation_id),
      FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
      CHECK((event_type='message_received' AND message_id IS NOT NULL AND language IS NOT NULL AND case_id IS NULL)
        OR(event_type='language_changed' AND message_id IS NULL AND language IS NOT NULL AND case_id IS NULL)
        OR(event_type='handoff_requested' AND message_id IS NULL AND language IS NULL AND case_id IS NOT NULL)))""")
    op.execute(f"ALTER TABLE {q}.conversation_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {q}.conversation_events FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {q}.conversation_events FROM PUBLIC")
    op.execute(f"GRANT SELECT ON {q}.conversation_events TO {runtime}")
    op.execute(f"CREATE POLICY runtime_read ON {q}.conversation_events FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=conversation_events.tenant_id AND c.id=conversation_events.conversation_id))")
    op.execute(f"""INSERT INTO {q}.conversation_events(tenant_id,conversation_id,sequence,event_type,created_at,message_id,language)
      SELECT tenant_id,conversation_id,sequence,'message_received',created_at,id,language FROM {q}.messages WHERE conversation_id IS NOT NULL""")
    op.execute(f"UPDATE {q}.conversations c SET last_event_sequence=(SELECT coalesce(max(sequence),0) FROM {q}.conversation_events e WHERE e.tenant_id=c.tenant_id AND e.conversation_id=c.id)")
    # Only trigger functions can emit rows. Their NOLOGIN owner has metadata,
    # never message bodies or a callable event-emission entry point.
    op.execute(f"GRANT SELECT(last_event_sequence,mode,language),UPDATE(last_event_sequence) ON {q}.conversations TO {owner}")
    op.execute(f"CREATE POLICY event_owner_read ON {q}.conversations FOR SELECT TO {owner} USING({own})")
    op.execute(f"CREATE POLICY event_owner_write ON {q}.conversations FOR UPDATE TO {owner} USING({own}) WITH CHECK({own})")
    op.execute(f"GRANT INSERT ON {q}.conversation_events TO {owner}")
    op.execute(f"CREATE POLICY event_owner_insert ON {q}.conversation_events FOR INSERT TO {owner} WITH CHECK(tenant_id={q}.current_tenant_id() AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=conversation_events.tenant_id AND c.id=conversation_events.conversation_id))")
    op.execute(f"GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}")
    op.execute(f"""CREATE FUNCTION {q}.emit_conversation_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE cid uuid;seq bigint;kind text;mid uuid;lang text;linked uuid;BEGIN
        IF TG_TABLE_NAME='messages' THEN
          IF NEW.conversation_id IS NULL THEN RETURN NEW;END IF;
          cid:=NEW.conversation_id;mid:=NEW.id;lang:=NEW.language;kind:='message_received';
        ELSE
          cid:=NEW.id;
          IF OLD.mode IS DISTINCT FROM NEW.mode THEN kind:='handoff_requested';linked:=NEW.case_id;
          ELSIF OLD.language IS DISTINCT FROM NEW.language THEN kind:='language_changed';lang:=NEW.language;
          ELSE RETURN NEW;END IF;
        END IF;
        UPDATE {q}.conversations SET last_event_sequence=last_event_sequence+1 WHERE tenant_id=NEW.tenant_id AND id=cid RETURNING last_event_sequence INTO seq;
        IF seq IS NULL THEN RAISE EXCEPTION 'Event parent unavailable' USING ERRCODE='23514';END IF;
        INSERT INTO {q}.conversation_events(tenant_id,conversation_id,sequence,event_type,message_id,language,case_id) VALUES(NEW.tenant_id,cid,seq,kind,mid,lang,linked);
        RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.emit_conversation_event() FROM PUBLIC")
    op.execute(f"ALTER FUNCTION {q}.emit_conversation_event() OWNER TO {owner}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")
    op.execute(f"CREATE TRIGGER message_event AFTER INSERT ON {q}.messages FOR EACH ROW EXECUTE FUNCTION {q}.emit_conversation_event()")
    op.execute(f"CREATE TRIGGER conversation_event AFTER UPDATE OF mode,language ON {q}.conversations FOR EACH ROW EXECUTE FUNCTION {q}.emit_conversation_event()")
    # Bind handoff only after the same confirmation commits the draft. Existing
    # cases reuse their original initial message as receipt, not a second case.
    op.execute(f"""CREATE FUNCTION {q}.bind_handoff() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF OLD.state='pending' AND NEW.state='committed' AND NEW.action_type='request_handoff' THEN
        UPDATE {q}.conversations SET case_id=NEW.result_case_id,mode='waiting_staff',version=version+1,generation_epoch=generation_epoch+1
          WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id AND case_id IS NULL AND mode='ai';
        IF NOT FOUND THEN RAISE EXCEPTION 'Handoff already linked' USING ERRCODE='23514';END IF;
      END IF;RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.bind_handoff() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER handoff_binding AFTER UPDATE OF state ON {q}.action_drafts FOR EACH ROW EXECUTE FUNCTION {q}.bind_handoff()")
    op.execute(f"""CREATE FUNCTION {q}.guard_handoff_link() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF OLD.case_id IS DISTINCT FROM NEW.case_id OR OLD.mode IS DISTINCT FROM NEW.mode THEN
        IF OLD.case_id IS NOT NULL OR OLD.mode<>'ai' OR NEW.mode<>'waiting_staff' OR NEW.case_id IS NULL
          OR NEW.version<>OLD.version+1 OR NEW.generation_epoch<>OLD.generation_epoch+1
          OR NOT EXISTS(SELECT 1 FROM {q}.action_drafts d WHERE d.tenant_id=NEW.tenant_id AND d.conversation_id=NEW.id AND d.state='committed' AND d.action_type='request_handoff' AND d.result_case_id=NEW.case_id)
          OR NOT EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=NEW.tenant_id AND c.id=NEW.case_id AND c.status<>'closed') THEN
          RAISE EXCEPTION 'Invalid handoff link' USING ERRCODE='23514';END IF;
      END IF;RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_handoff_link() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER handoff_link_guard BEFORE UPDATE OF case_id,mode ON {q}.conversations FOR EACH ROW EXECUTE FUNCTION {q}.guard_handoff_link()")
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(f'{q}.guard_case_lifecycle()',)).scalar_one()
    marker='IF NEW.version<>OLD.version+1 THEN'
    if definition.count(marker)!=1:raise RuntimeError('Unexpected lifecycle guard; upgrade aborted.')
    guard=f"""IF (NEW.status IS DISTINCT FROM OLD.status OR NEW.assigned_membership_id IS DISTINCT FROM OLD.assigned_membership_id)
      AND EXISTS(SELECT 1 FROM {q}.conversations v WHERE v.tenant_id=NEW.tenant_id AND v.case_id=NEW.id) THEN
      RAISE EXCEPTION 'Active chat transition awaits CHAT-02' USING ERRCODE='23514';END IF;
      """
    op.execute(definition.replace(marker,guard+marker))
    _existing_validation(q)


def _existing_validation(q):
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(f'{q}.validate_case_commit()',)).scalar_one()
    marker=f'SELECT * INTO c FROM {q}.cases WHERE tenant_id=d.tenant_id AND id=d.result_case_id;'
    if definition.count(marker)!=1:raise RuntimeError('Unexpected commit validator; upgrade aborted.')
    branch=f"""IF d.case_id IS NOT NULL THEN
      SELECT * INTO c FROM {q}.cases WHERE tenant_id=d.tenant_id AND id=d.case_id;
      IF NOT FOUND OR d.action_type<>'request_handoff' OR d.result_case_id<>c.id OR d.result_message_id<>c.initial_message_id OR d.result_ref<>c.public_reference
        OR NOT EXISTS(SELECT 1 FROM {q}.action_consents a WHERE a.tenant_id=d.tenant_id AND a.draft_id=d.id AND a.case_id=c.id AND a.confirmed_version=d.version AND a.payload_hash=d.payload_hash AND a.actor_principal_id IS NOT DISTINCT FROM d.actor_principal_id AND a.actor_session_id IS NOT DISTINCT FROM d.actor_session_id)
        OR NOT EXISTS(SELECT 1 FROM {q}.idempotency_records r WHERE r.tenant_id=d.tenant_id AND r.draft_id=d.id AND r.result_case_id=c.id)
        OR NOT EXISTS(SELECT 1 FROM {q}.conversations v WHERE v.tenant_id=d.tenant_id AND v.id=d.conversation_id AND v.case_id=c.id AND v.mode='waiting_staff') THEN
        RAISE EXCEPTION 'Incomplete existing handoff' USING ERRCODE='23514';END IF;
      RETURN NULL;END IF;
      """
    op.execute(definition.replace(marker,branch+marker))


def downgrade():
    raise RuntimeError('Conversation event downgrade requires a reviewed recovery migration.')
