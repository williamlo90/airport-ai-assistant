"""Standalone customer conversations and immutable user messages."""

from alembic import context, op

revision = "0005_conversations"
down_revision = "0004_staff_auth"
branch_labels = None
depends_on = None


def upgrade():
    attrs = context.config.attributes
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, runtime, owner = (quote(attrs[key]) for key in ("schema", "runtime_role", "auth_role"))
    op.execute(f"""CREATE FUNCTION {q}.current_session_id() RETURNS uuid LANGUAGE sql STABLE
        SET search_path=pg_catalog AS $$ SELECT CASE WHEN current_setting('app.session_id',true) ~
        '^[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'
        THEN current_setting('app.session_id',true)::uuid ELSE NULL END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.current_session_id() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {q}.current_session_id() TO {runtime},{owner}")
    for function in ('current_tenant_id','current_principal_id','current_actor_kind'):
        op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{function}() TO {owner}")
    op.execute(f"GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}")
    op.execute(f"""CREATE FUNCTION {q}.conversation_session_valid() RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE s {q}.sessions%ROWTYPE;
        BEGIN
          SELECT * INTO s FROM {q}.sessions WHERE tenant_id={q}.current_tenant_id()
            AND id={q}.current_session_id() FOR SHARE;
          IF NOT FOUND THEN RETURN false; END IF;
          RETURN s.session_kind IN('anonymous','customer') AND s.session_kind={q}.current_actor_kind()
            AND s.principal_id IS NOT DISTINCT FROM {q}.current_principal_id()
            AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
            AND EXISTS(SELECT 1 FROM {q}.tenants WHERE id=s.tenant_id AND active)
            AND (s.principal_id IS NULL OR EXISTS(SELECT 1 FROM {q}.principals
                WHERE tenant_id=s.tenant_id AND id=s.principal_id AND active));
        END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.conversation_session_valid() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {q}.conversation_session_valid() TO {runtime}")
    op.execute(f"ALTER FUNCTION {q}.conversation_session_valid() OWNER TO {owner}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")
    common = f"""tenant_id uuid NOT NULL REFERENCES {q}.tenants(id) ON DELETE RESTRICT,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(tenant_id,id)"""
    op.execute(f"""CREATE TABLE {q}.conversations({common},
        customer_principal_id uuid, anonymous_session_id uuid,
        language text NOT NULL CHECK(language IN('id','en')),
        mode text NOT NULL DEFAULT 'ai' CHECK(mode='ai'),
        generation_epoch bigint NOT NULL DEFAULT 1 CHECK(generation_epoch>0),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        last_sequence bigint NOT NULL DEFAULT 0 CHECK(last_sequence>=0),
        case_id uuid CHECK(case_id IS NULL),
        resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
        CHECK((customer_principal_id IS NULL)<>(anonymous_session_id IS NULL)),
        FOREIGN KEY(tenant_id,customer_principal_id) REFERENCES {q}.principals(tenant_id,id),
        FOREIGN KEY(tenant_id,anonymous_session_id) REFERENCES {q}.sessions(tenant_id,id))""")
    op.execute(f"""CREATE TABLE {q}.messages({common}, conversation_id uuid NOT NULL,
        author_principal_id uuid, author_session_id uuid,
        author_type text NOT NULL CHECK(author_type='customer'),
        channel text NOT NULL DEFAULT 'web' CHECK(channel='web'),
        visibility text NOT NULL DEFAULT 'customer' CHECK(visibility='customer'),
        content_class text NOT NULL DEFAULT 'private' CHECK(content_class='private'),
        source_kind text NOT NULL DEFAULT 'user_report' CHECK(source_kind='user_report'),
        body text NOT NULL CHECK(length(body) BETWEEN 1 AND 4000 AND body ~ '[^[:space:]]'),
        language text NOT NULL CHECK(language IN('id','en')),
        client_message_id uuid NOT NULL, sequence bigint NOT NULL CHECK(sequence>0),
        resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
        CHECK((author_principal_id IS NULL)<>(author_session_id IS NULL)),
        FOREIGN KEY(tenant_id,conversation_id) REFERENCES {q}.conversations(tenant_id,id),
        FOREIGN KEY(tenant_id,author_principal_id) REFERENCES {q}.principals(tenant_id,id),
        FOREIGN KEY(tenant_id,author_session_id) REFERENCES {q}.sessions(tenant_id,id),
        UNIQUE(tenant_id,conversation_id,sequence), UNIQUE(tenant_id,id,conversation_id))""")
    op.execute(f"CREATE UNIQUE INDEX messages_principal_dedupe ON {q}.messages(tenant_id,author_principal_id,conversation_id,client_message_id) WHERE author_principal_id IS NOT NULL")
    op.execute(f"CREATE UNIQUE INDEX messages_session_dedupe ON {q}.messages(tenant_id,author_session_id,conversation_id,client_message_id) WHERE author_session_id IS NOT NULL")
    op.execute(f"CREATE INDEX conversations_principal_idx ON {q}.conversations(tenant_id,customer_principal_id) WHERE customer_principal_id IS NOT NULL")
    op.execute(f"CREATE INDEX conversations_session_idx ON {q}.conversations(tenant_id,anonymous_session_id) WHERE anonymous_session_id IS NOT NULL")
    tenant = f"tenant_id={q}.current_tenant_id()"
    own = f"""({q}.current_actor_kind()='anonymous' AND anonymous_session_id={q}.current_session_id()
        OR {q}.current_actor_kind()='customer' AND customer_principal_id={q}.current_principal_id())"""
    condition = f"{tenant} AND {q}.conversation_session_valid() AND {own} AND case_id IS NULL AND resource_security_labels='[]'::jsonb"
    for table in ('conversations','messages'):
        op.execute(f"ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {q}.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT ON {q}.{table} TO {runtime}")
    op.execute(f"GRANT UPDATE(language,generation_epoch,version,last_sequence) ON {q}.conversations TO {runtime}")
    for action in ('SELECT','INSERT','UPDATE'):
        using = f'USING ({condition})' if action!='INSERT' else ''
        check = f'WITH CHECK ({condition})' if action!='SELECT' else ''
        op.execute(f"CREATE POLICY runtime_{action.lower()} ON {q}.conversations FOR {action} TO {runtime} {using} {check}")
    visible = f"{tenant} AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.conversation_id)"
    author = f"""({q}.current_actor_kind()='anonymous' AND author_session_id={q}.current_session_id() AND author_principal_id IS NULL
        OR {q}.current_actor_kind()='customer' AND author_principal_id={q}.current_principal_id() AND author_session_id IS NULL)"""
    op.execute(f"CREATE POLICY runtime_read ON {q}.messages FOR SELECT TO {runtime} USING ({visible})")
    op.execute(f"CREATE POLICY runtime_insert ON {q}.messages FOR INSERT TO {runtime} WITH CHECK ({visible} AND {author})")
    op.execute(f"CREATE TRIGGER touch_updated_at BEFORE UPDATE ON {q}.conversations FOR EACH ROW EXECUTE FUNCTION {q}.touch_updated_at()")


def downgrade():
    raise RuntimeError("Conversation downgrade requires a reviewed recovery migration.")
