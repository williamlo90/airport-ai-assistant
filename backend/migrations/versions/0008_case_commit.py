"""Confirmed report cases and their atomic consent/history/outbox graph."""

from alembic import context,op

revision='0008_case_commit'
down_revision='0007_anonymous_adoption'
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,owner=(quote(attrs[key]) for key in ('schema','runtime_role','auth_role'))
    common=f"""tenant_id uuid NOT NULL REFERENCES {q}.tenants(id),id uuid NOT NULL DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,id)"""
    op.execute(f"""CREATE TABLE {q}.cases({common},public_reference text NOT NULL CHECK(public_reference ~ '^CASE-[a-f0-9]{{32}}$'),
        origin_draft_id uuid NOT NULL,origin_conversation_id uuid NOT NULL,created_session_id uuid NOT NULL,
        owner_principal_id uuid,owner_anonymous_session_id uuid,owner_contact_id uuid,followup_contact_id uuid,
        organization_id uuid NOT NULL,queue_id uuid NOT NULL,
        category text NOT NULL CHECK(category IN('information','booking_issue','transaction_issue')),
        service_type text NOT NULL CHECK(service_type IN('general','parking','lounge')),
        summary text NOT NULL CHECK(length(summary) BETWEEN 1 AND 200),customer_report text NOT NULL CHECK(length(customer_report) BETWEEN 1 AND 4000),
        booking_reference text,transaction_reference text,language text NOT NULL CHECK(language IN('id','en')),
        source_observations jsonb NOT NULL DEFAULT '[]' CHECK(source_observations='[]'::jsonb),
        resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
        status text NOT NULL DEFAULT 'new' CHECK(status='new'),next_actor text NOT NULL DEFAULT 'staff' CHECK(next_actor='staff'),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),initial_message_id uuid NOT NULL,
        CHECK((owner_principal_id IS NULL)<>(owner_anonymous_session_id IS NULL)),
        CHECK((owner_principal_id IS NULL)=(owner_contact_id IS NULL)),
        CHECK(followup_contact_id IS NULL OR followup_contact_id=owner_contact_id),
        CHECK(owner_anonymous_session_id IS NULL OR(category='information' AND service_type='general' AND booking_reference IS NULL AND transaction_reference IS NULL AND followup_contact_id IS NULL)),
        UNIQUE(tenant_id,public_reference),UNIQUE(tenant_id,origin_draft_id),
        FOREIGN KEY(tenant_id,origin_draft_id) REFERENCES {q}.action_drafts(tenant_id,id),
        FOREIGN KEY(tenant_id,origin_conversation_id) REFERENCES {q}.conversations(tenant_id,id),
        FOREIGN KEY(tenant_id,created_session_id) REFERENCES {q}.sessions(tenant_id,id),
        FOREIGN KEY(tenant_id,owner_principal_id) REFERENCES {q}.principals(tenant_id,id),
        FOREIGN KEY(tenant_id,owner_anonymous_session_id) REFERENCES {q}.sessions(tenant_id,id),
        FOREIGN KEY(tenant_id,owner_contact_id) REFERENCES {q}.contacts(tenant_id,id),
        FOREIGN KEY(tenant_id,followup_contact_id) REFERENCES {q}.contacts(tenant_id,id),
        FOREIGN KEY(tenant_id,queue_id,organization_id) REFERENCES {q}.service_queues(tenant_id,id,organization_id))""")
    op.execute(f"ALTER TABLE {q}.messages ALTER COLUMN conversation_id DROP NOT NULL")
    op.execute(f"ALTER TABLE {q}.messages ADD COLUMN case_id uuid,ADD CONSTRAINT message_parent_xor CHECK((conversation_id IS NULL)<>(case_id IS NULL)),ADD CONSTRAINT message_case_fk FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),ADD CONSTRAINT message_case_identity UNIQUE(tenant_id,id,case_id)")
    op.execute(f"CREATE UNIQUE INDEX case_message_sequence ON {q}.messages(tenant_id,case_id,sequence) WHERE case_id IS NOT NULL")
    op.execute(f"CREATE UNIQUE INDEX case_message_principal_dedupe ON {q}.messages(tenant_id,author_principal_id,case_id,client_message_id) WHERE case_id IS NOT NULL AND author_principal_id IS NOT NULL")
    op.execute(f"CREATE UNIQUE INDEX case_message_session_dedupe ON {q}.messages(tenant_id,author_session_id,case_id,client_message_id) WHERE case_id IS NOT NULL AND author_session_id IS NOT NULL")
    op.execute(f"ALTER TABLE {q}.cases ADD CONSTRAINT case_initial_message FOREIGN KEY(tenant_id,initial_message_id,id) REFERENCES {q}.messages(tenant_id,id,case_id) DEFERRABLE INITIALLY DEFERRED")
    op.execute(f"ALTER TABLE {q}.action_drafts DROP CONSTRAINT action_drafts_state_check")
    op.execute(f"""ALTER TABLE {q}.action_drafts ADD CONSTRAINT action_drafts_state_check CHECK(state IN('pending','cancelled','expired','committed')),ADD COLUMN result_case_id uuid,ADD COLUMN result_message_id uuid,ADD COLUMN result_ref text,ADD COLUMN committed_at timestamptz,
        ADD CONSTRAINT draft_result_case FOREIGN KEY(tenant_id,result_case_id) REFERENCES {q}.cases(tenant_id,id),
        ADD CONSTRAINT draft_result_message FOREIGN KEY(tenant_id,result_message_id,result_case_id) REFERENCES {q}.messages(tenant_id,id,case_id),
        ADD CONSTRAINT draft_result_shape CHECK((state='committed' AND result_case_id IS NOT NULL AND result_message_id IS NOT NULL AND result_ref IS NOT NULL AND committed_at IS NOT NULL) OR(state<>'committed' AND result_case_id IS NULL AND result_message_id IS NULL AND result_ref IS NULL AND committed_at IS NULL))""")
    op.execute(f"""CREATE OR REPLACE FUNCTION {q}.guard_draft_transition() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF OLD.state<>'pending' THEN RAISE EXCEPTION 'Terminal draft is immutable' USING ERRCODE='23514';END IF;
        IF NEW.state='expired' AND clock_timestamp()<OLD.expires_at THEN RAISE EXCEPTION 'Draft not expired' USING ERRCODE='23514';END IF;
        IF OLD.expires_at<=clock_timestamp() THEN
            IF NEW.state IN('pending','committed') THEN RETURN NULL;END IF;
            IF NEW.state='cancelled' THEN NEW.state:='expired';END IF;
        END IF;RETURN NEW;END $$""")
    op.execute(f"""CREATE TABLE {q}.action_consents({common},draft_id uuid NOT NULL,case_id uuid NOT NULL,
        confirmed_version bigint NOT NULL CHECK(confirmed_version>0),payload_hash text NOT NULL,
        actor_principal_id uuid,actor_session_id uuid,confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),request_id uuid NOT NULL,
        CHECK((actor_principal_id IS NULL)<>(actor_session_id IS NULL)),UNIQUE(tenant_id,draft_id),
        FOREIGN KEY(tenant_id,draft_id) REFERENCES {q}.action_drafts(tenant_id,id),FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,actor_session_id) REFERENCES {q}.sessions(tenant_id,id))""")
    op.execute(f"""CREATE TABLE {q}.case_participants({common},case_id uuid NOT NULL,principal_id uuid NOT NULL,
        scopes text[] NOT NULL DEFAULT ARRAY['case:read'] CHECK(scopes=ARRAY['case:read']),
        relation_origin text NOT NULL DEFAULT 'verified_case_contact' CHECK(relation_origin='verified_case_contact'),
        verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),revoked_at timestamptz,
        UNIQUE(tenant_id,case_id,principal_id),FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id))""")
    op.execute(f"""CREATE TABLE {q}.case_history({common},case_id uuid NOT NULL,actor_principal_id uuid,actor_session_id uuid,
        event_type text NOT NULL CHECK(event_type IN('created','identity_verified')),previous_version bigint NOT NULL,resulting_version bigint NOT NULL,
        CHECK(resulting_version=previous_version+1),CHECK((actor_principal_id IS NULL)<>(actor_session_id IS NULL)),
        UNIQUE(tenant_id,case_id,resulting_version),FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,actor_session_id) REFERENCES {q}.sessions(tenant_id,id))""")
    op.execute(f"""CREATE TABLE {q}.audit_events({common},case_id uuid NOT NULL,action_draft_id uuid NOT NULL,
        event_type text NOT NULL CHECK(event_type IN('case_confirmed','identity_verified')),actor_principal_id uuid,actor_session_id uuid,request_id uuid,
        metadata jsonb NOT NULL DEFAULT '{{}}' CHECK(metadata='{{}}'::jsonb),CHECK((actor_principal_id IS NULL)<>(actor_session_id IS NULL)),
        UNIQUE(tenant_id,action_draft_id,event_type),FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),FOREIGN KEY(tenant_id,action_draft_id) REFERENCES {q}.action_drafts(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,actor_session_id) REFERENCES {q}.sessions(tenant_id,id))""")
    op.execute(f"""CREATE TABLE {q}.outbox_jobs({common},case_id uuid NOT NULL,action_draft_id uuid NOT NULL,
        kind text NOT NULL DEFAULT 'case_created' CHECK(kind='case_created'),state text NOT NULL DEFAULT 'queued' CHECK(state='queued'),
        dedupe_key text NOT NULL,payload jsonb NOT NULL,attempts integer NOT NULL DEFAULT 0 CHECK(attempts=0),
        max_attempts integer NOT NULL DEFAULT 5 CHECK(max_attempts>0),next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
        CHECK(payload=jsonb_build_object('case_id',case_id::text,'draft_id',action_draft_id::text)),
        UNIQUE(tenant_id,dedupe_key),UNIQUE(tenant_id,case_id,kind),
        FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),FOREIGN KEY(tenant_id,action_draft_id) REFERENCES {q}.action_drafts(tenant_id,id))""")
    op.execute(f"""CREATE TABLE {q}.idempotency_records({common},actor_principal_id uuid,actor_session_id uuid,created_session_id uuid NOT NULL,
        operation text NOT NULL CHECK(operation='confirm_draft'),key text NOT NULL CHECK(length(key) BETWEEN 16 AND 128),request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{{64}}$'),
        draft_id uuid NOT NULL,result_case_id uuid NOT NULL,result_message_id uuid NOT NULL,result_ref text NOT NULL,
        state text NOT NULL DEFAULT 'committed' CHECK(state='committed'),response_code integer NOT NULL DEFAULT 201 CHECK(response_code=201),
        CHECK((actor_principal_id IS NULL)<>(actor_session_id IS NULL)),
        FOREIGN KEY(tenant_id,draft_id) REFERENCES {q}.action_drafts(tenant_id,id),FOREIGN KEY(tenant_id,result_case_id) REFERENCES {q}.cases(tenant_id,id),
        FOREIGN KEY(tenant_id,result_message_id,result_case_id) REFERENCES {q}.messages(tenant_id,id,case_id),FOREIGN KEY(tenant_id,created_session_id) REFERENCES {q}.sessions(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,actor_session_id) REFERENCES {q}.sessions(tenant_id,id))""")
    for actor in ('principal','session'):
        op.execute(f"CREATE UNIQUE INDEX idempotency_{actor}_key ON {q}.idempotency_records(tenant_id,actor_{actor}_id,operation,key) WHERE actor_{actor}_id IS NOT NULL")
    op.execute(f"CREATE INDEX cases_queue_idx ON {q}.cases(tenant_id,queue_id,status,created_at,id)")
    op.execute(f"CREATE INDEX cases_anonymous_idx ON {q}.cases(tenant_id,owner_anonymous_session_id,id) WHERE owner_anonymous_session_id IS NOT NULL")
    own=f"""tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid()
        AND resource_security_labels='[]'::jsonb AND source_observations='[]'::jsonb
        AND(({q}.current_actor_kind()='customer' AND owner_principal_id={q}.current_principal_id())
          OR({q}.current_actor_kind()='anonymous' AND owner_anonymous_session_id={q}.current_session_id()))"""
    tables=('cases','action_consents','case_participants','case_history','audit_events','outbox_jobs','idempotency_records')
    for table in tables:
        op.execute(f"ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {q}.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT ON {q}.{table} TO {runtime}")
        casecol='result_case_id' if table=='idempotency_records' else 'case_id'
        visible=own if table=='cases' else f"tenant_id={q}.current_tenant_id() AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id={table}.tenant_id AND c.id={table}.{casecol})"
        op.execute(f"CREATE POLICY runtime_read ON {q}.{table} FOR SELECT TO {runtime} USING({visible})")
        insert=visible
        if table=='cases':
            insert+=f" AND created_session_id={q}.current_session_id() AND EXISTS(SELECT 1 FROM {q}.action_drafts d WHERE d.tenant_id=cases.tenant_id AND d.id=cases.origin_draft_id AND d.created_session_id={q}.current_session_id() AND d.state='pending')"
        if table in ('action_consents','case_history','audit_events','idempotency_records'):
            insert+=f" AND (({q}.current_actor_kind()='customer' AND actor_principal_id={q}.current_principal_id() AND actor_session_id IS NULL) OR({q}.current_actor_kind()='anonymous' AND actor_session_id={q}.current_session_id() AND actor_principal_id IS NULL))"
        if table=='case_participants':insert+=f" AND principal_id={q}.current_principal_id() AND {q}.current_actor_kind()='customer'"
        op.execute(f"CREATE POLICY runtime_insert ON {q}.{table} FOR INSERT TO {runtime} WITH CHECK({insert})")
    op.execute(f"GRANT INSERT(case_id) ON {q}.messages TO {runtime}")
    message_case=f"tenant_id={q}.current_tenant_id() AND conversation_id IS NULL AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.case_id)"
    op.execute(f"CREATE POLICY runtime_case_read ON {q}.messages FOR SELECT TO {runtime} USING({message_case})")
    author=f"(({q}.current_actor_kind()='customer' AND author_principal_id={q}.current_principal_id() AND author_session_id IS NULL) OR({q}.current_actor_kind()='anonymous' AND author_session_id={q}.current_session_id() AND author_principal_id IS NULL))"
    op.execute(f"CREATE POLICY runtime_case_insert ON {q}.messages FOR INSERT TO {runtime} WITH CHECK({message_case} AND {author})")
    op.execute(f"GRANT UPDATE(result_case_id,result_message_id,result_ref,committed_at) ON {q}.action_drafts TO {runtime}")
    # PostgreSQL row locks require a column UPDATE grant. WITH CHECK(false)
    # permits SELECT FOR SHARE but still forbids actual runtime modification.
    for table,column,extra in (('organizations','name',''),('service_queues','name',''),
                               ('contacts','email_address',f' AND principal_id={q}.current_principal_id()')):
        op.execute(f"GRANT UPDATE({column}) ON {q}.{table} TO {runtime}")
        op.execute(f"CREATE POLICY case_confirmation_lock ON {q}.{table} FOR UPDATE TO {runtime} USING(tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid(){extra}) WITH CHECK(false)")
    # Validate the complete immutable creation graph at the outer commit, not
    # between statements while FK cycles and consent are intentionally pending.
    op.execute(f"""CREATE FUNCTION {q}.validate_case_commit() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    DECLARE did uuid;d {q}.action_drafts%ROWTYPE;c {q}.cases%ROWTYPE;m {q}.messages%ROWTYPE;
    BEGIN
      IF TG_TABLE_NAME='action_drafts' THEN
        IF NEW.state<>'committed' THEN RETURN NULL;END IF;did:=NEW.id;
      ELSIF TG_TABLE_NAME='cases' THEN did:=NEW.origin_draft_id;
      ELSIF TG_TABLE_NAME='messages' THEN
        IF NEW.case_id IS NULL THEN RETURN NULL;END IF;
        SELECT origin_draft_id INTO did FROM {q}.cases WHERE tenant_id=NEW.tenant_id AND id=NEW.case_id;
      ELSE did:=NEW.draft_id;END IF;
      SELECT * INTO d FROM {q}.action_drafts WHERE tenant_id=NEW.tenant_id AND id=did;
      IF NOT FOUND THEN RAISE EXCEPTION 'Commit draft missing' USING ERRCODE='23514';END IF;
      IF TG_TABLE_NAME='idempotency_records' THEN
        IF d.state<>'committed' OR NEW.result_case_id<>d.result_case_id OR NEW.result_message_id<>d.result_message_id
          OR NEW.result_ref<>d.result_ref OR NEW.created_session_id<>d.created_session_id
          OR NEW.actor_principal_id IS DISTINCT FROM d.actor_principal_id OR NEW.actor_session_id IS DISTINCT FROM d.actor_session_id THEN
          RAISE EXCEPTION 'Idempotency result mismatch' USING ERRCODE='23514';END IF;RETURN NULL;
      END IF;
      IF d.state<>'committed' THEN
        IF EXISTS(SELECT 1 FROM {q}.cases WHERE tenant_id=d.tenant_id AND origin_draft_id=d.id) THEN
          RAISE EXCEPTION 'Uncommitted case graph' USING ERRCODE='23514';END IF;RETURN NULL;
      END IF;
      SELECT * INTO c FROM {q}.cases WHERE tenant_id=d.tenant_id AND id=d.result_case_id;
      IF NOT FOUND OR c.origin_draft_id IS DISTINCT FROM d.id OR c.origin_conversation_id IS DISTINCT FROM d.conversation_id OR c.public_reference IS DISTINCT FROM d.result_ref
        OR c.queue_id IS DISTINCT FROM d.destination_queue_id OR c.organization_id::text IS DISTINCT FROM d.destination->>'organization_id'
        OR c.summary IS DISTINCT FROM d.payload->>'summary' OR c.customer_report IS DISTINCT FROM d.payload->>'customer_report'
        OR c.category IS DISTINCT FROM d.payload->>'category' OR c.service_type IS DISTINCT FROM d.payload->>'service_type' OR c.language IS DISTINCT FROM d.language
        OR c.booking_reference IS DISTINCT FROM d.payload->>'booking_reference' OR c.transaction_reference IS DISTINCT FROM d.payload->>'transaction_reference'
        OR c.created_session_id<>d.created_session_id OR c.owner_principal_id IS DISTINCT FROM d.actor_principal_id
        OR c.owner_anonymous_session_id IS DISTINCT FROM d.actor_session_id THEN
          RAISE EXCEPTION 'Case does not match consented draft' USING ERRCODE='23514';END IF;
      SELECT * INTO m FROM {q}.messages WHERE tenant_id=d.tenant_id AND id=d.result_message_id;
      IF NOT FOUND OR m.case_id IS DISTINCT FROM c.id OR m.conversation_id IS NOT NULL OR m.body IS DISTINCT FROM c.customer_report
        OR m.author_principal_id IS DISTINCT FROM d.actor_principal_id OR m.author_session_id IS DISTINCT FROM d.actor_session_id
        OR m.language<>d.language OR m.sequence<>1 OR c.initial_message_id<>m.id THEN
          RAISE EXCEPTION 'Case message mismatch' USING ERRCODE='23514';END IF;
      IF NOT EXISTS(SELECT 1 FROM {q}.action_consents a WHERE a.tenant_id=d.tenant_id AND a.draft_id=d.id AND a.case_id=c.id
        AND a.confirmed_version=d.version AND a.payload_hash=d.payload_hash
        AND a.actor_principal_id IS NOT DISTINCT FROM d.actor_principal_id AND a.actor_session_id IS NOT DISTINCT FROM d.actor_session_id)
        OR NOT EXISTS(SELECT 1 FROM {q}.case_history WHERE tenant_id=c.tenant_id AND case_id=c.id AND event_type='created' AND resulting_version=1)
        OR NOT EXISTS(SELECT 1 FROM {q}.audit_events WHERE tenant_id=c.tenant_id AND case_id=c.id AND action_draft_id=d.id AND event_type='case_confirmed')
        OR NOT EXISTS(SELECT 1 FROM {q}.outbox_jobs WHERE tenant_id=c.tenant_id AND case_id=c.id AND action_draft_id=d.id AND kind='case_created')
        OR NOT EXISTS(SELECT 1 FROM {q}.idempotency_records WHERE tenant_id=c.tenant_id AND draft_id=d.id AND result_case_id=c.id
            AND result_message_id=m.id AND result_ref=c.public_reference)
        THEN RAISE EXCEPTION 'Incomplete case commit graph' USING ERRCODE='23514';END IF;
      IF c.owner_principal_id IS NOT NULL AND(NOT EXISTS(SELECT 1 FROM {q}.contacts WHERE tenant_id=c.tenant_id AND id=c.owner_contact_id
          AND principal_id=c.owner_principal_id AND verified_at IS NOT NULL)
        OR NOT EXISTS(SELECT 1 FROM {q}.case_participants WHERE tenant_id=c.tenant_id AND case_id=c.id AND principal_id=c.owner_principal_id AND revoked_at IS NULL))
        THEN RAISE EXCEPTION 'Verified case owner missing' USING ERRCODE='23514';END IF;
      IF (d.payload->>'contact_email') IS NULL THEN
        IF c.followup_contact_id IS NOT NULL THEN RAISE EXCEPTION 'Unrequested followup' USING ERRCODE='23514';END IF;
      ELSIF NOT EXISTS(SELECT 1 FROM {q}.contacts WHERE tenant_id=c.tenant_id AND id=c.followup_contact_id
          AND principal_id=c.owner_principal_id AND email_lookup_key=d.payload->>'contact_email' AND verified_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Followup contact mismatch' USING ERRCODE='23514';END IF;
      RETURN NULL;
    END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.validate_case_commit() FROM PUBLIC")
    for table in ('cases','action_drafts','action_consents','messages','idempotency_records'):
        events='INSERT OR UPDATE OF state,result_case_id,result_message_id,result_ref' if table=='action_drafts' else 'INSERT'
        op.execute(f"CREATE CONSTRAINT TRIGGER validate_case_commit AFTER {events} ON {q}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.validate_case_commit()")
    _adoption(q,runtime,owner)


def _adoption(q,runtime,owner):
    # Extend AUTH-02 only for anonymous general report cases; there is still no
    # source-data grant inferred from login, and no email queued on adoption.
    t="nullif(current_setting('app.adoption_tenant',true),'')::uuid"
    s="nullif(current_setting('app.adoption_session',true),'')::uuid"
    p="nullif(current_setting('app.adoption_principal',true),'')::uuid"
    scope=f"tenant_id={t} AND created_session_id={s}"
    op.execute(f"GRANT SELECT(tenant_id,id,origin_draft_id,origin_conversation_id,created_session_id,owner_principal_id,owner_anonymous_session_id,owner_contact_id,version,category,service_type,source_observations,resource_security_labels) ON {q}.cases TO {owner}")
    op.execute(f"GRANT UPDATE(owner_principal_id,owner_anonymous_session_id,owner_contact_id,version) ON {q}.cases TO {owner}")
    op.execute(f"CREATE POLICY auth_adoption_read ON {q}.cases FOR SELECT TO {owner} USING({scope} AND(owner_anonymous_session_id={s} OR owner_principal_id={p}))")
    op.execute(f"CREATE POLICY auth_adoption_write ON {q}.cases FOR UPDATE TO {owner} USING({scope} AND owner_anonymous_session_id={s}) WITH CHECK({scope} AND owner_principal_id={p} AND owner_anonymous_session_id IS NULL AND source_observations='[]'::jsonb AND resource_security_labels='[]'::jsonb)")
    for table in ('case_participants','case_history','audit_events'):
        op.execute(f"GRANT INSERT ON {q}.{table} TO {owner}")
        op.execute(f"CREATE POLICY auth_adoption_insert ON {q}.{table} FOR INSERT TO {owner} WITH CHECK(tenant_id={t} AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id={table}.tenant_id AND c.id={table}.case_id AND c.created_session_id={s} AND c.owner_principal_id={p}))")
    message_scope=f"tenant_id={t} AND conversation_id IS NULL AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.case_id AND c.created_session_id={s} AND c.owner_principal_id={p})"
    op.execute(f"GRANT SELECT(case_id) ON {q}.messages TO {owner}")
    op.execute(f"CREATE POLICY auth_case_read ON {q}.messages FOR SELECT TO {owner} USING({message_scope} AND(author_session_id={s} OR origin_session_id={s}))")
    op.execute(f"CREATE POLICY auth_case_write ON {q}.messages FOR UPDATE TO {owner} USING({message_scope} AND author_session_id={s}) WITH CHECK({message_scope} AND author_principal_id={p} AND author_session_id IS NULL AND origin_session_id={s})")
    op.execute(f"GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}")
    op.execute(f"""CREATE FUNCTION {q}.auth_adopt_report_cases(contact uuid) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM {q}.contacts WHERE tenant_id={t} AND id=contact AND principal_id={p} AND verified_at IS NOT NULL) THEN
          RAISE EXCEPTION 'Verified contact missing' USING ERRCODE='Z0001';END IF;
        UPDATE {q}.cases SET owner_principal_id={p},owner_anonymous_session_id=NULL,owner_contact_id=contact,version=version+1
          WHERE tenant_id={t} AND owner_anonymous_session_id={s} AND created_session_id={s};
        UPDATE {q}.messages SET author_principal_id={p},author_session_id=NULL,origin_session_id={s}
          WHERE tenant_id={t} AND author_session_id={s} AND case_id IN(SELECT id FROM {q}.cases WHERE tenant_id={t} AND created_session_id={s} AND owner_principal_id={p});
        INSERT INTO {q}.case_participants(tenant_id,case_id,principal_id)
          SELECT tenant_id,id,{p} FROM {q}.cases WHERE tenant_id={t} AND created_session_id={s} AND owner_principal_id={p};
        INSERT INTO {q}.case_history(tenant_id,case_id,actor_principal_id,event_type,previous_version,resulting_version)
          SELECT tenant_id,id,{p},'identity_verified',version-1,version FROM {q}.cases WHERE tenant_id={t} AND created_session_id={s} AND owner_principal_id={p};
        INSERT INTO {q}.audit_events(tenant_id,case_id,action_draft_id,event_type,actor_principal_id)
          SELECT tenant_id,id,origin_draft_id,'identity_verified',{p} FROM {q}.cases WHERE tenant_id={t} AND created_session_id={s} AND owner_principal_id={p};
      END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.auth_adopt_report_cases(uuid) FROM PUBLIC")
    op.execute(f"ALTER FUNCTION {q}.auth_adopt_report_cases(uuid) OWNER TO {owner}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")
    signature=f'{q}.auth_verify(uuid,uuid,text,text,text,uuid,text,text,integer,text,integer,integer,integer)'
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(signature,)).scalar_one()
    lock=f'PERFORM id FROM {q}.conversations WHERE tenant_id=p_tenant AND anonymous_session_id=v_session.id ORDER BY id FOR UPDATE;'
    cancel=f'UPDATE {q}.action_drafts SET state=\'cancelled\' WHERE tenant_id=p_tenant AND created_session_id=v_session.id'
    if definition.count(lock)!=1 or definition.count(cancel)!=1:
        raise RuntimeError('Unexpected AUTH-02 function definition; case adoption upgrade aborted.')
    definition=definition.replace(lock,f'PERFORM id FROM {q}.cases WHERE tenant_id=p_tenant AND owner_anonymous_session_id=v_session.id ORDER BY id FOR UPDATE;\n      '+lock)
    definition=definition.replace(cancel,f'PERFORM {q}.auth_adopt_report_cases(v_contact.id);\n        '+cancel)
    op.execute(definition)


def downgrade():
    raise RuntimeError('Case commit downgrade requires a reviewed recovery migration.')
