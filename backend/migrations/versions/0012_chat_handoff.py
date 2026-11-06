"""Atomic staff lifecycle, immutable accept receipts and public chat events."""
from alembic import context,op

revision='0012_chat_handoff'
down_revision='0011_conversation_events'
branch_labels=None
depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,owner=(quote(context.config.attributes[k]) for k in ('schema','runtime_role','auth_role'))
    op.execute(f"""ALTER TABLE {q}.conversations DROP CONSTRAINT conversations_mode_check,DROP CONSTRAINT conversation_link_shape,
      ADD COLUMN handler_membership_id uuid,ADD COLUMN last_transition_id uuid,
      ADD CONSTRAINT conversations_mode_check CHECK(mode IN('ai','waiting_staff','human','ended')),
      ADD CONSTRAINT conversation_link_shape CHECK((mode='ai' AND case_id IS NULL) OR(mode<>'ai' AND case_id IS NOT NULL)),
      ADD CONSTRAINT conversation_handler_shape CHECK((mode='human')=(handler_membership_id IS NOT NULL)),
      ADD CONSTRAINT conversation_handler_fk FOREIGN KEY(tenant_id,handler_membership_id) REFERENCES {q}.staff_memberships(tenant_id,id)""")
    op.execute(f"DROP INDEX {q}.conversation_waiting_case")
    op.execute(f"CREATE UNIQUE INDEX conversation_active_case ON {q}.conversations(tenant_id,case_id) WHERE mode IN('waiting_staff','human')")
    op.execute(f"""CREATE TABLE {q}.chat_transitions(
      tenant_id uuid NOT NULL,id uuid NOT NULL,conversation_id uuid NOT NULL,case_id uuid NOT NULL,actor_principal_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),kind text NOT NULL CHECK(kind IN('accept','assignment','status')),
      request_key text,request_hash text,
      previous_case_version bigint NOT NULL CHECK(previous_case_version>0),result_case_version bigint NOT NULL CHECK(result_case_version=previous_case_version+1),
      previous_conversation_version bigint NOT NULL CHECK(previous_conversation_version>0),result_conversation_version bigint NOT NULL CHECK(result_conversation_version=previous_conversation_version+1),
      previous_epoch bigint NOT NULL CHECK(previous_epoch>0),result_epoch bigint NOT NULL CHECK(result_epoch=previous_epoch+1),
      from_mode text NOT NULL CHECK(from_mode IN('waiting_staff','human')),to_mode text NOT NULL CHECK(to_mode IN('waiting_staff','human','ended')),
      handler_membership_id uuid,previous_assignee_id uuid,
      CHECK((to_mode='human')=(handler_membership_id IS NOT NULL)),
      CHECK((from_mode='waiting_staff' AND to_mode='human')=(kind='accept')),
      CHECK(kind<>'accept' OR previous_assignee_id IS NULL OR previous_assignee_id=handler_membership_id),
      CHECK(kind='accept' OR(kind='assignment' AND to_mode=from_mode) OR(kind='status' AND(to_mode=from_mode OR to_mode='ended'))),
      CHECK((kind='accept' AND from_mode='waiting_staff' AND to_mode='human' AND request_key IS NOT NULL AND length(request_key) BETWEEN 16 AND 128 AND request_hash IS NOT NULL AND request_hash ~ '^[a-f0-9]{{64}}$') OR(kind<>'accept' AND request_key IS NULL AND request_hash IS NULL)),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,conversation_id,result_conversation_version),UNIQUE(tenant_id,id,conversation_id),UNIQUE(tenant_id,id,case_id),
      FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
      FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
      FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,handler_membership_id) REFERENCES {q}.staff_memberships(tenant_id,id),
      FOREIGN KEY(tenant_id,previous_assignee_id) REFERENCES {q}.staff_memberships(tenant_id,id))""")
    op.execute(f"CREATE UNIQUE INDEX accept_request_key ON {q}.chat_transitions(tenant_id,actor_principal_id,request_key) WHERE kind='accept'")
    op.execute(f"ALTER TABLE {q}.conversations ADD CONSTRAINT conversation_transition_fk FOREIGN KEY(tenant_id,last_transition_id,id) REFERENCES {q}.chat_transitions(tenant_id,id,conversation_id) DEFERRABLE INITIALLY DEFERRED")
    op.execute(f"ALTER TABLE {q}.case_history ADD COLUMN chat_transition_id uuid,ADD CONSTRAINT history_chat_transition_fk FOREIGN KEY(tenant_id,chat_transition_id,case_id) REFERENCES {q}.chat_transitions(tenant_id,id,case_id) DEFERRABLE INITIALLY DEFERRED")
    op.execute(f"GRANT UPDATE(handler_membership_id,last_transition_id) ON {q}.conversations TO {runtime}")
    staff=f"tenant_id={q}.current_tenant_id() AND case_id IS NOT NULL AND resource_security_labels='[]'::jsonb AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=conversations.tenant_id AND c.id=conversations.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false))"
    op.execute(f"CREATE POLICY chat_staff_update ON {q}.conversations FOR UPDATE TO {runtime} USING({staff}) WITH CHECK({staff})")
    op.execute(f"ALTER TABLE {q}.chat_transitions ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {q}.chat_transitions FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {q}.chat_transitions FROM PUBLIC")
    op.execute(f"GRANT SELECT,INSERT ON {q}.chat_transitions TO {runtime}")
    scope=f"tenant_id={q}.current_tenant_id() AND actor_principal_id={q}.current_principal_id() AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=chat_transitions.tenant_id AND c.id=chat_transitions.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false))"
    op.execute(f"CREATE POLICY actor_read ON {q}.chat_transitions FOR SELECT TO {runtime} USING({scope})")
    op.execute(f"CREATE POLICY actor_insert ON {q}.chat_transitions FOR INSERT TO {runtime} WITH CHECK({scope})")
    # Retire the temporary CHAT-01 lifecycle gate, preserving all CASE-03 checks.
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(f'{q}.guard_case_lifecycle()',)).scalar_one()
    begin=definition.index('IF (NEW.status IS DISTINCT FROM OLD.status OR NEW.assigned_membership_id IS DISTINCT FROM OLD.assigned_membership_id)')
    end=definition.index('IF NEW.version<>OLD.version+1 THEN',begin)
    op.execute(definition[:begin]+definition[end:])
    op.execute(f"""CREATE OR REPLACE FUNCTION {q}.guard_handoff_link() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE c {q}.cases%ROWTYPE;meta jsonb;BEGIN
        IF OLD.case_id IS DISTINCT FROM NEW.case_id THEN
          IF OLD.case_id IS NOT NULL OR OLD.mode<>'ai' OR NEW.mode<>'waiting_staff' OR NEW.case_id IS NULL OR NEW.handler_membership_id IS NOT NULL OR NEW.last_transition_id IS NOT NULL
            OR NEW.version<>OLD.version+1 OR NEW.generation_epoch<>OLD.generation_epoch+1
            OR NOT EXISTS(SELECT 1 FROM {q}.action_drafts d WHERE d.tenant_id=NEW.tenant_id AND d.conversation_id=NEW.id AND d.state='committed' AND d.action_type='request_handoff' AND d.result_case_id=NEW.case_id)
            OR NOT EXISTS(SELECT 1 FROM {q}.cases x WHERE x.tenant_id=NEW.tenant_id AND x.id=NEW.case_id AND x.status<>'closed') THEN
            RAISE EXCEPTION 'Invalid handoff link' USING ERRCODE='23514';END IF;
        ELSIF OLD.mode IS DISTINCT FROM NEW.mode OR OLD.handler_membership_id IS DISTINCT FROM NEW.handler_membership_id OR OLD.last_transition_id IS DISTINCT FROM NEW.last_transition_id THEN
          SELECT * INTO c FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id;
          IF NOT FOUND OR NOT {q}.case_staff_scope(c.organization_id,c.queue_id,false) THEN RAISE EXCEPTION 'Chat action forbidden' USING ERRCODE='42501';END IF;
          IF OLD.mode NOT IN('waiting_staff','human') OR NEW.mode NOT IN('waiting_staff','human','ended') OR(OLD.mode='human' AND NEW.mode='waiting_staff')
            OR NEW.version<>OLD.version+1 OR NEW.generation_epoch<>OLD.generation_epoch+1 OR NEW.last_transition_id IS NULL OR NEW.last_transition_id IS NOT DISTINCT FROM OLD.last_transition_id
            OR(NEW.mode='ended')<>(c.status='closed') THEN RAISE EXCEPTION 'Invalid chat transition' USING ERRCODE='23514';END IF;
          IF NEW.mode='human' THEN
            meta:={q}.case_target_membership(NEW.handler_membership_id,c.organization_id,c.queue_id);
            IF meta IS NULL OR NEW.handler_membership_id IS DISTINCT FROM c.assigned_membership_id THEN RAISE EXCEPTION 'Handler mismatch' USING ERRCODE='23514';END IF;
            IF OLD.mode='waiting_staff' AND(meta->>'principal_id')::uuid<>{q}.current_principal_id() THEN RAISE EXCEPTION 'Accept must be self' USING ERRCODE='42501';END IF;
          END IF;
        END IF;RETURN NEW;
      END $$""")
    op.execute(f"DROP TRIGGER handoff_link_guard ON {q}.conversations")
    op.execute(f"CREATE TRIGGER handoff_link_guard BEFORE UPDATE OF case_id,mode,handler_membership_id,last_transition_id ON {q}.conversations FOR EACH ROW EXECUTE FUNCTION {q}.guard_handoff_link()")
    _events(q,runtime,owner,staff)
    _validate(q)
    op.execute(f"""CREATE FUNCTION {q}.guard_ended_message() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE current_mode text;BEGIN
        IF NEW.conversation_id IS NULL THEN RETURN NEW;END IF;
        SELECT mode INTO current_mode FROM {q}.conversations WHERE tenant_id=NEW.tenant_id AND id=NEW.conversation_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Conversation unavailable' USING ERRCODE='42501';END IF;
        IF current_mode='ended' THEN RAISE EXCEPTION 'Conversation ended' USING ERRCODE='23514';END IF;
        RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_ended_message() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER ended_message_guard BEFORE INSERT ON {q}.messages FOR EACH ROW EXECUTE FUNCTION {q}.guard_ended_message()")
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(f'{q}.validate_case_commit()',)).scalar_one()
    op.execute(definition.replace("AND v.mode='waiting_staff'","AND v.mode IN('waiting_staff','human','ended')"))


def _events(q,runtime,owner,staff):
    op.execute(f"""ALTER TABLE {q}.conversation_events DROP CONSTRAINT conversation_events_event_type_check,DROP CONSTRAINT conversation_events_check,
      ADD COLUMN conversation_mode text CHECK(conversation_mode IN('waiting_staff','human','ended')),
      ADD COLUMN transition_id uuid,
      ADD CONSTRAINT event_transition_fk FOREIGN KEY(tenant_id,transition_id,conversation_id) REFERENCES {q}.chat_transitions(tenant_id,id,conversation_id) DEFERRABLE INITIALLY DEFERRED,
      ADD CONSTRAINT event_transition_unique UNIQUE(tenant_id,transition_id),
      ADD CONSTRAINT conversation_events_event_type_check CHECK(event_type IN('message_received','language_changed','handoff_requested','chat_accepted','chat_reassigned','chat_ended','chat_updated'))""")
    op.execute(f"UPDATE {q}.conversation_events SET conversation_mode='waiting_staff' WHERE event_type='handoff_requested'")
    op.execute(f"""ALTER TABLE {q}.conversation_events ADD CONSTRAINT conversation_events_check CHECK(
      (event_type='message_received' AND message_id IS NOT NULL AND language IS NOT NULL AND case_id IS NULL AND conversation_mode IS NULL AND transition_id IS NULL)
      OR(event_type='language_changed' AND message_id IS NULL AND language IS NOT NULL AND case_id IS NULL AND conversation_mode IS NULL AND transition_id IS NULL)
      OR(event_type='handoff_requested' AND message_id IS NULL AND language IS NULL AND case_id IS NOT NULL AND conversation_mode='waiting_staff' AND transition_id IS NULL)
      OR(event_type IN('chat_accepted','chat_reassigned','chat_ended','chat_updated') AND message_id IS NULL AND language IS NULL AND case_id IS NOT NULL AND conversation_mode IS NOT NULL AND transition_id IS NOT NULL))""")
    op.execute(f"GRANT SELECT(handler_membership_id,last_transition_id) ON {q}.conversations TO {owner}")
    op.execute(f"CREATE POLICY event_owner_staff_read ON {q}.conversations FOR SELECT TO {owner} USING({staff})")
    op.execute(f"CREATE POLICY event_owner_staff_write ON {q}.conversations FOR UPDATE TO {owner} USING({staff}) WITH CHECK({staff})")
    op.execute(f"""CREATE OR REPLACE FUNCTION {q}.emit_conversation_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE cid uuid;seq bigint;kind text;mid uuid;lang text;linked uuid;cm text;tid uuid;BEGIN
        IF TG_TABLE_NAME='messages' THEN
          IF NEW.conversation_id IS NULL THEN RETURN NEW;END IF;
          cid:=NEW.conversation_id;mid:=NEW.id;lang:=NEW.language;kind:='message_received';
        ELSE
          cid:=NEW.id;
          IF NEW.last_transition_id IS DISTINCT FROM OLD.last_transition_id THEN
            tid:=NEW.last_transition_id;linked:=NEW.case_id;cm:=NEW.mode;
            kind:=CASE WHEN NEW.mode='ended' THEN 'chat_ended' WHEN OLD.mode='waiting_staff' AND NEW.mode='human' THEN 'chat_accepted'
              WHEN OLD.handler_membership_id IS DISTINCT FROM NEW.handler_membership_id THEN 'chat_reassigned' ELSE 'chat_updated' END;
          ELSIF OLD.mode IS DISTINCT FROM NEW.mode THEN kind:='handoff_requested';linked:=NEW.case_id;cm:=NEW.mode;
          ELSIF OLD.language IS DISTINCT FROM NEW.language THEN kind:='language_changed';lang:=NEW.language;
          ELSE RETURN NEW;END IF;
        END IF;
        UPDATE {q}.conversations SET last_event_sequence=last_event_sequence+1 WHERE tenant_id=NEW.tenant_id AND id=cid RETURNING last_event_sequence INTO seq;
        IF seq IS NULL THEN RAISE EXCEPTION 'Event parent unavailable' USING ERRCODE='23514';END IF;
        INSERT INTO {q}.conversation_events(tenant_id,conversation_id,sequence,event_type,message_id,language,case_id,conversation_mode,transition_id) VALUES(NEW.tenant_id,cid,seq,kind,mid,lang,linked,cm,tid);
        RETURN NEW;END $$""")
    op.execute(f"DROP TRIGGER conversation_event ON {q}.conversations")
    op.execute(f"CREATE TRIGGER conversation_event AFTER UPDATE OF mode,language,last_transition_id ON {q}.conversations FOR EACH ROW EXECUTE FUNCTION {q}.emit_conversation_event()")


def _validate(q):
    op.execute(f"""CREATE FUNCTION {q}.validate_chat_transition() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE r {q}.chat_transitions%ROWTYPE;c {q}.cases%ROWTYPE;v {q}.conversations%ROWTYPE;rid uuid;BEGIN
        IF TG_TABLE_NAME='chat_transitions' THEN rid:=NEW.id;
        ELSIF TG_TABLE_NAME='conversations' THEN
          IF OLD.mode='ai' OR NEW.last_transition_id IS NOT DISTINCT FROM OLD.last_transition_id THEN RETURN NULL;END IF;
          rid:=NEW.last_transition_id;
        ELSE
          IF OLD.owner_anonymous_session_id IS NOT NULL AND NEW.owner_anonymous_session_id IS NULL AND OLD.owner_principal_id IS NULL AND NEW.owner_principal_id IS NOT NULL THEN RETURN NULL;END IF;
          SELECT chat_transition_id INTO rid FROM {q}.case_history WHERE tenant_id=NEW.tenant_id AND case_id=NEW.id AND resulting_version=NEW.version;
          IF rid IS NULL THEN
            IF EXISTS(SELECT 1 FROM {q}.conversations x WHERE x.tenant_id=NEW.tenant_id AND x.case_id=NEW.id AND x.mode IN('waiting_staff','human')) THEN RAISE EXCEPTION 'Chat transition required' USING ERRCODE='23514';END IF;
            RETURN NULL;
          END IF;
        END IF;
        SELECT * INTO r FROM {q}.chat_transitions WHERE tenant_id=NEW.tenant_id AND id=rid;
        IF NOT FOUND THEN RAISE EXCEPTION 'Missing transition' USING ERRCODE='23514';END IF;
        IF TG_TABLE_NAME='cases' THEN
          IF r.tenant_id<>NEW.tenant_id OR r.case_id<>NEW.id OR r.result_case_version<>NEW.version THEN RAISE EXCEPTION 'Case transition origin mismatch' USING ERRCODE='23514';END IF;
        ELSIF TG_TABLE_NAME='conversations' THEN
          IF r.tenant_id<>NEW.tenant_id OR r.conversation_id<>NEW.id OR r.case_id IS DISTINCT FROM NEW.case_id THEN RAISE EXCEPTION 'Conversation transition origin mismatch' USING ERRCODE='23514';END IF;
        END IF;
        SELECT * INTO c FROM {q}.cases WHERE tenant_id=r.tenant_id AND id=r.case_id;
        SELECT * INTO v FROM {q}.conversations WHERE tenant_id=r.tenant_id AND id=r.conversation_id;
        IF r.kind='assignment' THEN
          IF NOT {q}.case_staff_scope(c.organization_id,c.queue_id,true) THEN RAISE EXCEPTION 'Supervisor required' USING ERRCODE='42501';END IF;
        END IF;
        IF r.kind='accept' THEN
          IF c.status<>'in_progress' THEN RAISE EXCEPTION 'Accepted case must be in progress' USING ERRCODE='23514';END IF;
        END IF;
        IF c.id IS NULL OR v.id IS NULL OR v.case_id<>c.id OR c.version<>r.result_case_version OR v.version<>r.result_conversation_version OR v.generation_epoch<>r.result_epoch
          OR v.last_transition_id IS DISTINCT FROM r.id OR v.mode<>r.to_mode OR v.handler_membership_id IS DISTINCT FROM r.handler_membership_id
          OR(v.mode='human' AND v.handler_membership_id IS DISTINCT FROM c.assigned_membership_id) OR(v.mode='ended')<>(c.status='closed')
          OR NOT EXISTS(SELECT 1 FROM {q}.case_history h WHERE h.tenant_id=r.tenant_id AND h.case_id=c.id AND h.chat_transition_id=r.id AND h.previous_version=r.previous_case_version AND h.resulting_version=r.result_case_version AND h.actor_principal_id=r.actor_principal_id
            AND((r.kind='accept' AND h.event_type IN('assigned','status_changed')) OR(r.kind='assignment' AND h.event_type='assigned') OR(r.kind='status' AND h.event_type='status_changed')))
          OR NOT EXISTS(SELECT 1 FROM {q}.conversation_events e WHERE e.tenant_id=r.tenant_id AND e.conversation_id=v.id AND e.transition_id=r.id AND e.conversation_mode=r.to_mode)
          THEN RAISE EXCEPTION 'Inconsistent chat transition' USING ERRCODE='23514';END IF;
        IF TG_TABLE_NAME='conversations' THEN
          IF r.previous_conversation_version<>OLD.version OR r.previous_epoch<>OLD.generation_epoch OR r.from_mode<>OLD.mode THEN RAISE EXCEPTION 'Chat origin mismatch' USING ERRCODE='23514';END IF;
        ELSIF TG_TABLE_NAME='cases' THEN
          IF r.previous_case_version<>OLD.version OR r.previous_assignee_id IS DISTINCT FROM OLD.assigned_membership_id THEN RAISE EXCEPTION 'Case origin mismatch' USING ERRCODE='23514';END IF;
        END IF;
        RETURN NULL;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.validate_chat_transition() FROM PUBLIC")
    op.execute(f"CREATE CONSTRAINT TRIGGER chat_transition_check AFTER INSERT ON {q}.chat_transitions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_chat_transition()")
    op.execute(f"CREATE CONSTRAINT TRIGGER chat_pair_check AFTER UPDATE OF mode,handler_membership_id,last_transition_id ON {q}.conversations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_chat_transition()")
    op.execute(f"CREATE CONSTRAINT TRIGGER case_chat_pair_check AFTER UPDATE ON {q}.cases DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_chat_transition()")


def downgrade():raise RuntimeError('Chat lifecycle downgrade requires a reviewed recovery migration.')
