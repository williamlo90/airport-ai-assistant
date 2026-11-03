"""Scoped manual case operations and immutable public lifecycle history."""
from alembic import context,op

revision='0009_case_lifecycle'
down_revision='0008_case_commit'
branch_labels=None
depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,owner=(quote(context.config.attributes[k]) for k in ('schema','runtime_role','auth_role'))
    op.execute(f"""ALTER TABLE {q}.cases DROP CONSTRAINT cases_status_check,DROP CONSTRAINT cases_next_actor_check,
      ADD COLUMN assigned_membership_id uuid,ADD COLUMN next_action text NOT NULL DEFAULT 'Menunggu petugas menerima kasus.',
      ADD COLUMN close_reason text,ADD COLUMN resolution text,ADD COLUMN duplicate_of_case_id uuid,
      ADD COLUMN followup_evidence text,ADD COLUMN closed_at timestamptz,ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      ADD CONSTRAINT case_assignee_fk FOREIGN KEY(tenant_id,assigned_membership_id,organization_id) REFERENCES {q}.staff_memberships(tenant_id,id,organization_id),
      ADD CONSTRAINT case_duplicate_fk FOREIGN KEY(tenant_id,duplicate_of_case_id) REFERENCES {q}.cases(tenant_id,id),
      ADD CONSTRAINT cases_status_check CHECK(status IN('new','in_progress','awaiting_customer','awaiting_external','closed')),
      ADD CONSTRAINT cases_next_actor_check CHECK(next_actor IN('staff','customer','external','none')),
      ADD CONSTRAINT case_next_action CHECK(length(next_action) BETWEEN 1 AND 2000 AND next_action ~ '[^[:space:]]'),
      ADD CONSTRAINT case_state_shape CHECK(
        (status='new' AND next_actor='staff') OR(status='in_progress' AND assigned_membership_id IS NOT NULL AND next_actor='staff')
        OR(status='awaiting_customer' AND assigned_membership_id IS NOT NULL AND next_actor='customer')
        OR(status='awaiting_external' AND assigned_membership_id IS NOT NULL AND next_actor='external') OR(status='closed' AND next_actor='none')),
      ADD CONSTRAINT case_close_shape CHECK((status='closed' AND close_reason IS NOT NULL AND close_reason IN('resolved','customer_cancelled','duplicate','no_response')
          AND resolution IS NOT NULL AND length(resolution) BETWEEN 1 AND 4000 AND resolution ~ '[^[:space:]]' AND closed_at IS NOT NULL
          AND ((close_reason='duplicate' AND duplicate_of_case_id IS NOT NULL AND duplicate_of_case_id<>id) OR(close_reason<>'duplicate' AND duplicate_of_case_id IS NULL))
          AND ((close_reason='no_response' AND followup_evidence IS NOT NULL AND length(followup_evidence) BETWEEN 1 AND 2000 AND followup_evidence ~ '[^[:space:]]') OR(close_reason<>'no_response' AND followup_evidence IS NULL)))
        OR(status<>'closed' AND close_reason IS NULL AND resolution IS NULL AND closed_at IS NULL AND duplicate_of_case_id IS NULL AND followup_evidence IS NULL))""")
    op.execute(f"""ALTER TABLE {q}.case_history DROP CONSTRAINT case_history_event_type_check,
      ADD CONSTRAINT case_history_event_type_check CHECK(event_type IN('created','identity_verified','assigned','status_changed','reopened')),
      ADD COLUMN from_status text,ADD COLUMN to_status text,ADD COLUMN assigned_membership_id uuid,
      ADD COLUMN next_actor text,ADD COLUMN next_action text,ADD COLUMN close_reason text,ADD COLUMN resolution text,
      ADD COLUMN duplicate_of_case_id uuid,ADD COLUMN followup_evidence text,ADD COLUMN customer_message text,
      ADD COLUMN client_request_id uuid,ADD COLUMN request_hash text,
      ADD CONSTRAINT history_lifecycle_shape CHECK(event_type IN('created','identity_verified') OR(
        from_status IS NOT NULL AND to_status IS NOT NULL AND next_actor IS NOT NULL AND next_action IS NOT NULL)),
      ADD CONSTRAINT history_reopen_shape CHECK(event_type<>'reopened' OR(client_request_id IS NOT NULL AND request_hash IS NOT NULL AND request_hash ~ '^[a-f0-9]{{64}}$' AND customer_message IS NOT NULL AND from_status='closed'))""")
    op.execute(f"CREATE UNIQUE INDEX case_reopen_request ON {q}.case_history(tenant_id,case_id,actor_principal_id,client_request_id) WHERE event_type='reopened'")
    op.execute(f"CREATE INDEX case_assignment_status ON {q}.cases(tenant_id,assigned_membership_id,status,updated_at,id)")
    # A table-level INSERT grant would silently cover the new staff-owned
    # lifecycle columns too. Keep customer creation on the reviewed input only.
    op.execute(f"REVOKE INSERT ON {q}.cases FROM {runtime}")
    op.execute(f"GRANT INSERT(tenant_id,id,public_reference,origin_draft_id,origin_conversation_id,created_session_id,owner_principal_id,owner_anonymous_session_id,owner_contact_id,followup_contact_id,organization_id,queue_id,category,service_type,summary,customer_report,booking_reference,transaction_reference,language,initial_message_id) ON {q}.cases TO {runtime}")
    # The historical auth-owner policy is FOR ALL. Split it before adding a
    # row-lock UPDATE grant: permissive policies otherwise OR away CHECK(false).
    op.execute(f"DROP POLICY auth_owner_access ON {q}.principals")
    op.execute(f"CREATE POLICY auth_owner_read ON {q}.principals FOR SELECT TO {owner} USING(true)")
    op.execute(f"CREATE POLICY auth_owner_insert ON {q}.principals FOR INSERT TO {owner} WITH CHECK(true)")
    # Lock privileges do not grant metadata write authority to the function owner.
    for table,column in (('tenants','id'),('principals','id'),('organizations','id'),('service_queues','id'),('staff_memberships','id'),('membership_roles','id'),('membership_queues','id')):
        op.execute(f"GRANT UPDATE({column}) ON {q}.{table} TO {owner}")
        op.execute(f"CREATE POLICY lifecycle_owner_lock ON {q}.{table} FOR UPDATE TO {owner} USING(true) WITH CHECK(false)")
    op.execute(f"GRANT SELECT(assigned_membership_id,organization_id,queue_id) ON {q}.cases TO {owner}")
    op.execute(f"GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}")
    definitions=[('case_staff_session_valid()',True,f"""
      CREATE FUNCTION {q}.case_staff_session_valid() RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE s {q}.sessions%ROWTYPE;BEGIN
        IF {q}.current_actor_kind()<>'staff' THEN RETURN false;END IF;
        SELECT * INTO s FROM {q}.sessions WHERE tenant_id={q}.current_tenant_id() AND id={q}.current_session_id() FOR SHARE;
        IF NOT FOUND OR s.session_kind<>'staff' OR s.principal_id IS DISTINCT FROM {q}.current_principal_id() THEN RETURN false;END IF;
        PERFORM id FROM {q}.tenants WHERE id=s.tenant_id FOR SHARE;
        PERFORM id FROM {q}.principals WHERE tenant_id=s.tenant_id AND id=s.principal_id FOR SHARE;
        PERFORM principal_id FROM {q}.staff_credentials WHERE tenant_id=s.tenant_id AND principal_id=s.principal_id FOR SHARE;
        PERFORM ct.id FROM {q}.contacts ct JOIN {q}.staff_credentials sc ON(sc.tenant_id,sc.contact_id)=(ct.tenant_id,ct.id)
          WHERE sc.tenant_id=s.tenant_id AND sc.principal_id=s.principal_id FOR SHARE OF ct;
        RETURN {q}.auth_session(s.tenant_id,s.token_hash) IS NOT NULL;
      END $$"""),('case_member_metadata(uuid,uuid,uuid)',False,f"""
      CREATE FUNCTION {q}.case_member_metadata(mid uuid,org uuid,queue uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE m {q}.staff_memberships%ROWTYPE;roles text[];BEGIN
        IF mid IS NULL THEN RETURN NULL;END IF;
        PERFORM id FROM {q}.organizations WHERE tenant_id={q}.current_tenant_id() AND id=org AND active FOR SHARE;
        IF NOT FOUND THEN RETURN NULL;END IF;
        PERFORM id FROM {q}.service_queues WHERE tenant_id={q}.current_tenant_id() AND id=queue AND organization_id=org AND active FOR SHARE;
        IF NOT FOUND THEN RETURN NULL;END IF;
        SELECT * INTO m FROM {q}.staff_memberships WHERE tenant_id={q}.current_tenant_id() AND id=mid AND organization_id=org AND active FOR SHARE;
        IF NOT FOUND THEN RETURN NULL;END IF;
        PERFORM id FROM {q}.principals WHERE tenant_id=m.tenant_id AND id=m.principal_id AND active FOR SHARE;
        IF NOT FOUND THEN RETURN NULL;END IF;
        PERFORM sc.principal_id FROM {q}.staff_credentials sc JOIN {q}.contacts ct ON(ct.tenant_id,ct.id)=(sc.tenant_id,sc.contact_id)
          WHERE sc.tenant_id=m.tenant_id AND sc.principal_id=m.principal_id AND sc.active AND ct.principal_id=sc.principal_id AND ct.verified_at IS NOT NULL
          FOR SHARE OF sc,ct;
        IF NOT FOUND THEN RETURN NULL;END IF;
        PERFORM id FROM {q}.membership_roles WHERE tenant_id=m.tenant_id AND membership_id=m.id ORDER BY id FOR SHARE;
        SELECT array_agg(role) INTO roles FROM {q}.membership_roles WHERE tenant_id=m.tenant_id AND membership_id=m.id;
        IF NOT(roles && ARRAY['cs','supervisor']) OR roles IS NULL THEN RETURN NULL;END IF;
        PERFORM id FROM {q}.membership_queues WHERE tenant_id=m.tenant_id AND membership_id=m.id AND queue_id=queue FOR SHARE;
        IF NOT FOUND THEN RETURN NULL;END IF;
        RETURN jsonb_build_object('id',m.id,'principal_id',m.principal_id,'supervisor','supervisor'=ANY(roles));
      END $$"""),('case_staff_scope(uuid,uuid,boolean)',True,f"""
      CREATE FUNCTION {q}.case_staff_scope(org uuid,queue uuid,supervisor_only boolean DEFAULT false) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE mid uuid;meta jsonb;BEGIN
        IF NOT {q}.case_staff_session_valid() THEN RETURN false;END IF;
        FOR mid IN SELECT id FROM {q}.staff_memberships WHERE tenant_id={q}.current_tenant_id() AND principal_id={q}.current_principal_id() AND organization_id=org ORDER BY id LOOP
          meta:={q}.case_member_metadata(mid,org,queue);
          IF meta IS NOT NULL AND(NOT supervisor_only OR(meta->>'supervisor')::boolean) THEN RETURN true;END IF;
        END LOOP;RETURN false;
      END $$"""),('case_target_membership(uuid,uuid,uuid)',True,f"""
      CREATE FUNCTION {q}.case_target_membership(mid uuid,org uuid,queue uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
        IF NOT {q}.case_staff_scope(org,queue,false) THEN RETURN NULL;END IF;
        RETURN {q}.case_member_metadata(mid,org,queue);
      END $$""")]
    for signature,exposed,definition in definitions:
        op.execute(definition)
        op.execute(f"REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION {q}.{signature} OWNER TO {owner}")
        if exposed:op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}")
    op.execute(f"""CREATE POLICY lifecycle_owner_read ON {q}.cases FOR SELECT TO {owner} USING(tenant_id={q}.current_tenant_id() AND(
       ({q}.current_actor_kind()='customer' AND owner_principal_id={q}.current_principal_id() AND {q}.conversation_session_valid())
       OR {q}.case_staff_scope(organization_id,queue_id,false)))""")
    op.execute(f"GRANT EXECUTE ON FUNCTION {q}.conversation_session_valid() TO {owner}")
    op.execute(f"""CREATE FUNCTION {q}.case_current_assignee(cid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE c record;meta jsonb;BEGIN
        SELECT assigned_membership_id,organization_id,queue_id INTO c FROM {q}.cases WHERE tenant_id={q}.current_tenant_id() AND id=cid
          AND(({q}.current_actor_kind()='customer' AND owner_principal_id={q}.current_principal_id() AND {q}.conversation_session_valid())
            OR {q}.case_staff_scope(organization_id,queue_id,false));
        IF NOT FOUND THEN RETURN NULL;END IF;
        meta:={q}.case_member_metadata(c.assigned_membership_id,c.organization_id,c.queue_id);
        RETURN(meta->>'id')::uuid;
      END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.case_current_assignee(uuid) FROM PUBLIC")
    op.execute(f"ALTER FUNCTION {q}.case_current_assignee(uuid) OWNER TO {owner}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {q}.case_current_assignee(uuid) TO {runtime}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")
    staff=f"tenant_id={q}.current_tenant_id() AND source_observations='[]'::jsonb AND resource_security_labels='[]'::jsonb AND {q}.case_staff_scope(organization_id,queue_id,false)"
    op.execute(f"CREATE POLICY lifecycle_staff_read ON {q}.cases FOR SELECT TO {runtime} USING({staff})")
    for table,column in (('action_consents','case_id'),('outbox_jobs','case_id'),('idempotency_records','result_case_id')):
        op.execute(f"DROP POLICY runtime_read ON {q}.{table}")
        op.execute(f"CREATE POLICY runtime_read ON {q}.{table} FOR SELECT TO {runtime} USING(tenant_id={q}.current_tenant_id() AND {q}.current_actor_kind() IN('customer','anonymous') AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id={table}.tenant_id AND c.id={table}.{column}))")
    update=f"({staff}) OR(tenant_id={q}.current_tenant_id() AND {q}.current_actor_kind()='customer' AND {q}.conversation_session_valid() AND owner_principal_id={q}.current_principal_id())"
    op.execute(f"GRANT UPDATE(status,assigned_membership_id,next_actor,next_action,close_reason,resolution,duplicate_of_case_id,followup_evidence,closed_at,updated_at,version) ON {q}.cases TO {runtime}")
    op.execute(f"CREATE POLICY lifecycle_update ON {q}.cases FOR UPDATE TO {runtime} USING({update}) WITH CHECK({update})")
    # Only lifecycle history may be inserted by staff. Other creation children
    # retain their owner-only INSERT policies from CASE-02.
    op.execute(f"""CREATE POLICY lifecycle_staff_history ON {q}.case_history FOR INSERT TO {runtime} WITH CHECK(
      tenant_id={q}.current_tenant_id() AND actor_principal_id={q}.current_principal_id() AND actor_session_id IS NULL
      AND event_type IN('assigned','status_changed') AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=case_history.tenant_id AND c.id=case_history.case_id AND {q}.case_staff_scope(c.organization_id,c.queue_id,false)))""")
    op.execute(f"""CREATE FUNCTION {q}.guard_case_lifecycle() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE meta jsonb;supervisor boolean;BEGIN
        IF NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Case version must advance' USING ERRCODE='23514';END IF;
        NEW.updated_at:=clock_timestamp();
        IF current_user={_literal(context.config.attributes['auth_role'])} THEN
          IF OLD.owner_anonymous_session_id IS NULL OR OLD.owner_principal_id IS NOT NULL OR NEW.owner_anonymous_session_id IS NOT NULL
            OR NEW.owner_principal_id IS NULL OR NEW.status IS DISTINCT FROM OLD.status OR NEW.assigned_membership_id IS DISTINCT FROM OLD.assigned_membership_id THEN
            RAISE EXCEPTION 'Invalid case adoption' USING ERRCODE='23514';END IF;RETURN NEW;END IF;
        IF {q}.current_actor_kind()='customer' AND {q}.conversation_session_valid() AND OLD.owner_principal_id={q}.current_principal_id() THEN
          IF OLD.status<>'closed' OR NEW.status NOT IN('new','in_progress') OR NEW.assigned_membership_id IS DISTINCT FROM {q}.case_current_assignee(OLD.id)
            OR(NEW.status='in_progress')<>(NEW.assigned_membership_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Invalid customer continuation' USING ERRCODE='23514';END IF;
        ELSIF {q}.case_staff_scope(OLD.organization_id,OLD.queue_id,false) THEN
          supervisor:={q}.case_staff_scope(OLD.organization_id,OLD.queue_id,true);
          IF OLD.status='closed' THEN RAISE EXCEPTION 'Closed case requires customer continuation' USING ERRCODE='23514';END IF;
          IF NEW.assigned_membership_id IS DISTINCT FROM OLD.assigned_membership_id THEN
            meta:={q}.case_target_membership(NEW.assigned_membership_id,OLD.organization_id,OLD.queue_id);
            IF NEW.assigned_membership_id IS NOT NULL AND meta IS NULL THEN RAISE EXCEPTION 'Assignee unavailable' USING ERRCODE='23514';END IF;
            IF NOT supervisor AND(OLD.assigned_membership_id IS NOT NULL OR meta IS NULL OR(meta->>'principal_id')::uuid<>{q}.current_principal_id()) THEN
              RAISE EXCEPTION 'Assignment forbidden' USING ERRCODE='42501';END IF;
          END IF;
          IF NEW.status IS DISTINCT FROM OLD.status AND NOT(
             (OLD.status='new' AND NEW.status='in_progress') OR(OLD.status='in_progress' AND NEW.status IN('awaiting_customer','awaiting_external'))
             OR(OLD.status IN('awaiting_customer','awaiting_external') AND NEW.status='in_progress') OR NEW.status='closed') THEN
             RAISE EXCEPTION 'Invalid status transition' USING ERRCODE='23514';END IF;
          IF NEW.status IN('in_progress','awaiting_customer','awaiting_external') AND NEW.assigned_membership_id IS DISTINCT FROM {q}.case_current_assignee(NEW.id) THEN
            meta:={q}.case_target_membership(NEW.assigned_membership_id,OLD.organization_id,OLD.queue_id);
            IF meta IS NULL THEN RAISE EXCEPTION 'Assignee inactive' USING ERRCODE='23514';END IF;
          END IF;
          IF NEW.close_reason='duplicate' AND NOT EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=NEW.tenant_id AND c.id=NEW.duplicate_of_case_id
            AND {q}.case_staff_scope(c.organization_id,c.queue_id,false) AND c.owner_principal_id IS NOT DISTINCT FROM NEW.owner_principal_id
            AND c.owner_anonymous_session_id IS NOT DISTINCT FROM NEW.owner_anonymous_session_id) THEN
            RAISE EXCEPTION 'Duplicate reference unavailable' USING ERRCODE='23514';END IF;
        ELSE RAISE EXCEPTION 'Case mutation forbidden' USING ERRCODE='42501';END IF;
        RETURN NEW;
      END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_case_lifecycle() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER guard_case_lifecycle BEFORE UPDATE ON {q}.cases FOR EACH ROW EXECUTE FUNCTION {q}.guard_case_lifecycle()")
    op.execute(f"""CREATE FUNCTION {q}.validate_case_lifecycle() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      -- Deferred triggers run after the definer auth function returns. Adoption
      -- is authenticated by the immediate guard and private auth transaction;
      -- runtime has no UPDATE privilege on these ownership columns.
      IF OLD.owner_anonymous_session_id IS NOT NULL AND NEW.owner_anonymous_session_id IS NULL
        AND OLD.owner_principal_id IS NULL AND NEW.owner_principal_id IS NOT NULL THEN RETURN NULL;END IF;
      IF current_user<>{_literal(context.config.attributes['auth_role'])} THEN
        IF NOT EXISTS(SELECT 1 FROM {q}.case_history h WHERE h.tenant_id=NEW.tenant_id AND h.case_id=NEW.id
          AND h.previous_version=OLD.version AND h.resulting_version=NEW.version AND h.from_status=OLD.status AND h.to_status=NEW.status
          AND h.assigned_membership_id IS NOT DISTINCT FROM NEW.assigned_membership_id AND h.next_actor=NEW.next_actor AND h.next_action=NEW.next_action
          AND h.close_reason IS NOT DISTINCT FROM NEW.close_reason AND h.resolution IS NOT DISTINCT FROM NEW.resolution
          AND h.duplicate_of_case_id IS NOT DISTINCT FROM NEW.duplicate_of_case_id AND h.followup_evidence IS NOT DISTINCT FROM NEW.followup_evidence
          AND h.actor_principal_id={q}.current_principal_id() AND h.actor_session_id IS NULL
          AND(({q}.current_actor_kind()='staff' AND h.event_type=CASE WHEN NEW.assigned_membership_id IS DISTINCT FROM OLD.assigned_membership_id THEN 'assigned' ELSE 'status_changed' END)
            OR({q}.current_actor_kind()='customer' AND h.event_type='reopened')))
          THEN RAISE EXCEPTION 'Lifecycle history missing' USING ERRCODE='23514';END IF;
      END IF;RETURN NULL;
    END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.validate_case_lifecycle() FROM PUBLIC")
    op.execute(f"CREATE CONSTRAINT TRIGGER validate_case_lifecycle AFTER UPDATE ON {q}.cases DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_case_lifecycle()")
    op.execute(f"""CREATE FUNCTION {q}.guard_lifecycle_history() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.event_type='identity_verified' THEN
        IF current_user<>{_literal(context.config.attributes['auth_role'])} THEN
          RAISE EXCEPTION 'Identity history requires verified adoption' USING ERRCODE='42501';END IF;
        -- Do not plan the broader lifecycle SELECT below under auth-owner,
        -- which intentionally cannot read closure/report content.
        RETURN NEW;
      END IF;
      IF {q}.current_actor_kind()='staff' THEN
        IF NOT {q}.case_staff_session_valid() THEN RAISE EXCEPTION 'Session ended' USING ERRCODE='42501';END IF;
      ELSIF NOT {q}.conversation_session_valid() THEN RAISE EXCEPTION 'Session ended' USING ERRCODE='42501';END IF;
      IF NEW.event_type='created' AND(NEW.previous_version<>0 OR NEW.resulting_version<>1 OR NOT EXISTS(SELECT 1 FROM {q}.cases c
        WHERE c.tenant_id=NEW.tenant_id AND c.id=NEW.case_id AND c.version=1)) THEN
        RAISE EXCEPTION 'Invalid creation history' USING ERRCODE='23514';END IF;
      IF NEW.event_type='created' AND(NEW.from_status IS NOT NULL OR NEW.to_status IS NOT NULL OR NEW.assigned_membership_id IS NOT NULL
        OR NEW.next_actor IS NOT NULL OR NEW.next_action IS NOT NULL OR NEW.close_reason IS NOT NULL OR NEW.resolution IS NOT NULL
        OR NEW.duplicate_of_case_id IS NOT NULL OR NEW.followup_evidence IS NOT NULL OR NEW.customer_message IS NOT NULL
        OR NEW.client_request_id IS NOT NULL OR NEW.request_hash IS NOT NULL) THEN
        RAISE EXCEPTION 'Creation history cannot claim lifecycle changes' USING ERRCODE='23514';END IF;
      IF NEW.event_type IN('assigned','status_changed','reopened') AND NOT EXISTS(SELECT 1 FROM {q}.cases c
        WHERE c.tenant_id=NEW.tenant_id AND c.id=NEW.case_id AND c.version=NEW.resulting_version
        AND NEW.previous_version=c.version-1 AND NEW.to_status=c.status AND NEW.next_actor=c.next_actor
        AND NEW.next_action=c.next_action AND NEW.assigned_membership_id IS NOT DISTINCT FROM c.assigned_membership_id
        AND NEW.close_reason IS NOT DISTINCT FROM c.close_reason AND NEW.resolution IS NOT DISTINCT FROM c.resolution
        AND NEW.duplicate_of_case_id IS NOT DISTINCT FROM c.duplicate_of_case_id AND NEW.followup_evidence IS NOT DISTINCT FROM c.followup_evidence) THEN
          RAISE EXCEPTION 'History snapshot does not match case' USING ERRCODE='23514';END IF;
      RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_lifecycle_history() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER guard_lifecycle_history BEFORE INSERT ON {q}.case_history FOR EACH ROW EXECUTE FUNCTION {q}.guard_lifecycle_history()")
    op.execute(f"""CREATE FUNCTION {q}.guard_case_audit() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF NEW.event_type='identity_verified' AND current_user<>{_literal(context.config.attributes['auth_role'])} THEN
        RAISE EXCEPTION 'Identity audit requires verified adoption' USING ERRCODE='42501';END IF;RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_case_audit() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER guard_case_audit BEFORE INSERT ON {q}.audit_events FOR EACH ROW EXECUTE FUNCTION {q}.guard_case_audit()")


def _literal(value):return "'"+value.replace("'","''")+"'"


def downgrade():raise RuntimeError('Case lifecycle downgrade requires a reviewed recovery migration.')
