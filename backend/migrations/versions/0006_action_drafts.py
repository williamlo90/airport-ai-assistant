"""Session-bound standalone previews; no consent or case commit yet."""

from alembic import context,op

revision='0006_action_drafts'
down_revision='0005_conversations'
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime=(quote(attrs[key]) for key in ('schema','runtime_role'))
    op.execute(f"""CREATE TABLE {q}.action_drafts(
        tenant_id uuid NOT NULL REFERENCES {q}.tenants(id),id uuid NOT NULL,
        created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        conversation_id uuid NOT NULL,actor_principal_id uuid,actor_session_id uuid,
        created_session_id uuid NOT NULL,case_id uuid CHECK(case_id IS NULL),
        action_type text NOT NULL CHECK(action_type IN('create_case','request_handoff')),
        destination_queue_id uuid NOT NULL,destination jsonb NOT NULL CHECK(jsonb_typeof(destination)='object'
            AND destination ? 'queue_id' AND destination->>'queue_id'=destination_queue_id::text),
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'
            AND payload ?& ARRAY['category','service_type','summary','customer_report','attachment_ids']
            AND payload-ARRAY['category','service_type','summary','customer_report','booking_reference','transaction_reference','contact_email','attachment_ids']='{{}}'::jsonb
            AND payload->'attachment_ids'='[]'::jsonb),
        displayed_summary text NOT NULL CHECK(length(displayed_summary) BETWEEN 1 AND 7000),
        language text NOT NULL CHECK(language IN('id','en')),
        payload_hash text NOT NULL CHECK(payload_hash ~ '^[a-f0-9]{{64}}$'),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        state text NOT NULL DEFAULT 'pending' CHECK(state IN('pending','cancelled','expired')),
        expires_at timestamptz NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+interval '15 minutes'),
        resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
        CHECK((actor_principal_id IS NULL)<>(actor_session_id IS NULL)),
        CHECK(actor_session_id IS NULL OR actor_session_id=created_session_id),
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),
        FOREIGN KEY(tenant_id,actor_session_id) REFERENCES {q}.sessions(tenant_id,id),
        FOREIGN KEY(tenant_id,created_session_id) REFERENCES {q}.sessions(tenant_id,id),
        FOREIGN KEY(tenant_id,destination_queue_id) REFERENCES {q}.service_queues(tenant_id,id))""")
    op.execute(f"CREATE INDEX drafts_conversation_idx ON {q}.action_drafts(tenant_id,conversation_id)")
    op.execute(f"CREATE INDEX drafts_session_pending_idx ON {q}.action_drafts(tenant_id,created_session_id,expires_at) WHERE state='pending'")
    op.execute(f"CREATE INDEX drafts_principal_idx ON {q}.action_drafts(tenant_id,actor_principal_id) WHERE actor_principal_id IS NOT NULL")
    op.execute(f"ALTER TABLE {q}.action_drafts ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {q}.action_drafts FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {q}.action_drafts FROM PUBLIC")
    op.execute(f"GRANT SELECT,INSERT ON {q}.action_drafts TO {runtime}")
    op.execute(f"GRANT UPDATE(destination_queue_id,destination,payload,displayed_summary,language,payload_hash,version,state) ON {q}.action_drafts TO {runtime}")
    condition=f"""tenant_id={q}.current_tenant_id() AND {q}.conversation_session_valid()
        AND created_session_id={q}.current_session_id() AND case_id IS NULL AND resource_security_labels='[]'::jsonb
        AND (({q}.current_actor_kind()='anonymous' AND actor_session_id={q}.current_session_id() AND actor_principal_id IS NULL)
             OR ({q}.current_actor_kind()='customer' AND actor_principal_id={q}.current_principal_id() AND actor_session_id IS NULL))
        AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=action_drafts.tenant_id AND c.id=action_drafts.conversation_id)"""
    for action in ('SELECT','INSERT','UPDATE'):
        using=f'USING ({condition})' if action!='INSERT' else ''
        check=f'WITH CHECK ({condition})' if action!='SELECT' else ''
        op.execute(f"CREATE POLICY runtime_{action.lower()} ON {q}.action_drafts FOR {action} TO {runtime} {using} {check}")
    op.execute(f"""CREATE FUNCTION {q}.guard_draft_transition() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$ BEGIN
        IF OLD.state<>'pending' THEN RAISE EXCEPTION 'Terminal draft is immutable' USING ERRCODE='23514'; END IF;
        IF NEW.state='expired' AND clock_timestamp()<OLD.expires_at THEN
            RAISE EXCEPTION 'Draft not expired' USING ERRCODE='23514'; END IF;
        IF OLD.expires_at<=clock_timestamp() THEN
            IF NEW.state='pending' THEN RETURN NULL; END IF;
            IF NEW.state='cancelled' THEN NEW.state:='expired'; END IF;
        END IF;
        RETURN NEW; END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_draft_transition() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER guard_transition BEFORE UPDATE ON {q}.action_drafts FOR EACH ROW EXECUTE FUNCTION {q}.guard_draft_transition()")
    op.execute(f"CREATE TRIGGER touch_updated_at BEFORE UPDATE ON {q}.action_drafts FOR EACH ROW EXECUTE FUNCTION {q}.touch_updated_at()")


def downgrade():
    raise RuntimeError('Draft downgrade requires a reviewed recovery migration.')
