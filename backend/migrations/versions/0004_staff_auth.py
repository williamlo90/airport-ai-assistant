"""Provisioned staff credentials and live membership-scoped sessions."""

from alembic import context, op

revision = "0004_staff_auth"
down_revision = "0003_customer_auth"
branch_labels = None
depends_on = None


def upgrade():
    attrs = context.config.attributes
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, runtime, owner = (quote(attrs[key]) for key in ("schema", "runtime_role", "auth_role"))
    op.execute(f"""CREATE TABLE {q}.staff_credentials(
        tenant_id uuid NOT NULL, principal_id uuid NOT NULL, contact_id uuid NOT NULL,
        credential_version uuid NOT NULL DEFAULT gen_random_uuid(), password_hash text NOT NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(tenant_id,principal_id), UNIQUE(tenant_id,contact_id),
        CHECK(length(password_hash) BETWEEN 50 AND 256 AND password_hash LIKE '$argon2id$%'),
        FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,contact_id) REFERENCES {q}.contacts(tenant_id,id) ON DELETE RESTRICT)""")
    op.execute(f"ALTER TABLE {q}.staff_credentials ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {q}.staff_credentials FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {q}.staff_credentials FROM PUBLIC")
    op.execute(f"GRANT SELECT ON {q}.staff_credentials TO {owner}")
    # PostgreSQL row locks require UPDATE privilege on at least one column.
    # UPDATE policy permits lock visibility but WITH CHECK(false) forbids writes.
    op.execute(f"GRANT UPDATE(credential_version) ON {q}.staff_credentials TO {owner}")
    op.execute(f"CREATE POLICY auth_owner_read ON {q}.staff_credentials FOR SELECT TO {owner} USING(true)")
    op.execute(f"CREATE POLICY auth_owner_lock ON {q}.staff_credentials FOR UPDATE TO {owner} USING(true) WITH CHECK(false)")
    op.execute(f"CREATE TRIGGER touch_updated_at BEFORE UPDATE ON {q}.staff_credentials FOR EACH ROW EXECUTE FUNCTION {q}.touch_updated_at()")
    op.execute(f"""CREATE FUNCTION {q}.rotate_staff_credential_version() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN NEW.credential_version:=gen_random_uuid();RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.rotate_staff_credential_version() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER rotate_version BEFORE UPDATE ON {q}.staff_credentials FOR EACH ROW EXECUTE FUNCTION {q}.rotate_staff_credential_version()")
    op.execute(f"ALTER TABLE {q}.sessions ADD COLUMN staff_credential_version uuid")
    op.execute(f"ALTER TABLE {q}.sessions ADD CONSTRAINT staff_session_version CHECK((session_kind='staff')=(staff_credential_version IS NOT NULL))")
    op.execute(f"ALTER TABLE {q}.auth_rate_limits DROP CONSTRAINT auth_rate_limits_scope_check")
    op.execute(f"ALTER TABLE {q}.auth_rate_limits ADD CONSTRAINT auth_rate_limits_scope_check CHECK(scope IN('boot','contact','issue_ip','issue_session','verify_ip','verify_session','staff_ip','staff_contact','staff_session'))")
    for table in ("organizations", "service_queues", "staff_memberships", "membership_roles", "membership_queues"):
        op.execute(f"GRANT SELECT ON {q}.{table} TO {owner}")
        op.execute(f"CREATE POLICY auth_owner_read ON {q}.{table} FOR SELECT TO {owner} USING(true)")
    op.execute(f"GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}")
    definitions = [
        ("staff_eligible(uuid,uuid)", False, f"""
        CREATE FUNCTION {q}.staff_eligible(p_tenant uuid,p_principal uuid) RETURNS boolean
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT EXISTS(SELECT 1 FROM {q}.principals p JOIN {q}.tenants t ON t.id=p.tenant_id
            JOIN {q}.staff_memberships m ON(m.tenant_id,m.principal_id)=(p.tenant_id,p.id)
            JOIN {q}.organizations o ON(o.tenant_id,o.id)=(m.tenant_id,m.organization_id)
            JOIN {q}.membership_roles r ON(r.tenant_id,r.membership_id)=(m.tenant_id,m.id)
            WHERE p.tenant_id=p_tenant AND p.id=p_principal AND p.active AND t.active AND m.active AND o.active)
        $$"""),
        ("auth_session(uuid,text)", True, f"""
        CREATE OR REPLACE FUNCTION {q}.auth_session(p_tenant uuid,p_hash text) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT jsonb_build_object('id',s.id,'tenant_id',s.tenant_id,'principal_id',s.principal_id,
            'kind',s.session_kind,'expires_at',s.expires_at,'csrf_hash',s.csrf_secret_hash)
          FROM {q}.sessions s JOIN {q}.tenants t ON t.id=s.tenant_id
          LEFT JOIN {q}.principals p ON(p.tenant_id,p.id)=(s.tenant_id,s.principal_id)
          WHERE s.tenant_id=p_tenant AND s.token_hash=p_hash AND s.revoked_at IS NULL
            AND s.expires_at>clock_timestamp() AND t.active AND (s.principal_id IS NULL OR p.active)
            AND (s.session_kind IN('anonymous','customer') OR (s.session_kind='staff'
              AND {q}.staff_eligible(p_tenant,s.principal_id)
              AND EXISTS(SELECT 1 FROM {q}.staff_credentials c JOIN {q}.contacts ct
                ON(ct.tenant_id,ct.id)=(c.tenant_id,c.contact_id)
                WHERE c.tenant_id=p_tenant AND c.principal_id=s.principal_id AND c.active
                  AND c.credential_version=s.staff_credential_version AND ct.principal_id=c.principal_id AND ct.verified_at IS NOT NULL)))
        $$"""),
        ("auth_staff_begin(uuid,text,text,text,text,text,integer,integer,integer,integer)", True, f"""
        CREATE FUNCTION {q}.auth_staff_begin(p_tenant uuid,p_token text,p_csrf text,p_contact text,p_contact_key text,p_ip text,
          p_window integer,p_ip_limit integer,p_contact_limit integer,p_session_limit integer) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE s {q}.sessions%ROWTYPE;v_ip boolean;v_contact boolean;v_session boolean;v_credential jsonb;
        BEGIN
          SELECT * INTO s FROM {q}.sessions WHERE tenant_id=p_tenant AND token_hash=p_token FOR UPDATE;
          IF NOT FOUND OR s.session_kind<>'anonymous' OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp()
            OR NOT EXISTS(SELECT 1 FROM {q}.tenants WHERE id=p_tenant AND active) THEN RETURN jsonb_build_object('status','invalid_session');END IF;
          IF s.csrf_secret_hash IS DISTINCT FROM p_csrf THEN RETURN jsonb_build_object('status','csrf_failed');END IF;
          v_ip:={q}.auth_rate(p_tenant,'staff_ip',p_ip,p_window,p_ip_limit);
          v_contact:={q}.auth_rate(p_tenant,'staff_contact',p_contact_key,p_window,p_contact_limit);
          v_session:={q}.auth_rate(p_tenant,'staff_session',s.token_hash,p_window,p_session_limit);
          IF NOT(v_ip AND v_contact AND v_session) THEN RETURN jsonb_build_object('status','rate_limited');END IF;
          SELECT jsonb_build_object('principal_id',c.principal_id,'version',c.credential_version,'password_hash',c.password_hash)
            INTO v_credential FROM {q}.staff_credentials c JOIN {q}.contacts ct ON(ct.tenant_id,ct.id)=(c.tenant_id,c.contact_id)
            WHERE c.tenant_id=p_tenant AND ct.email_lookup_key=p_contact AND ct.verified_at IS NOT NULL
              AND ct.principal_id=c.principal_id AND c.active AND {q}.staff_eligible(p_tenant,c.principal_id);
          RETURN jsonb_build_object('status','ok','credential',v_credential);
        END $$"""),
        ("auth_staff_finish(uuid,text,text,uuid,uuid,uuid,text,text,integer)", True, f"""
        CREATE FUNCTION {q}.auth_staff_finish(p_tenant uuid,p_token text,p_csrf text,p_principal uuid,p_version uuid,
          p_id uuid,p_new_token text,p_new_csrf text,p_ttl integer) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE s {q}.sessions%ROWTYPE;c {q}.staff_credentials%ROWTYPE;
        BEGIN
          SELECT * INTO s FROM {q}.sessions WHERE tenant_id=p_tenant AND token_hash=p_token FOR UPDATE;
          IF NOT FOUND OR s.session_kind<>'anonymous' OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp()
            THEN RETURN jsonb_build_object('status','invalid_session');END IF;
          IF s.csrf_secret_hash IS DISTINCT FROM p_csrf THEN RETURN jsonb_build_object('status','csrf_failed');END IF;
          SELECT * INTO c FROM {q}.staff_credentials WHERE tenant_id=p_tenant AND principal_id=p_principal FOR SHARE;
          IF NOT FOUND OR NOT c.active OR c.credential_version IS DISTINCT FROM p_version OR p_version IS NULL
            OR p_ttl IS NULL OR p_ttl NOT BETWEEN 300 AND 28800
            OR NOT {q}.staff_eligible(p_tenant,p_principal)
            OR NOT EXISTS(SELECT 1 FROM {q}.contacts ct WHERE ct.tenant_id=p_tenant AND ct.id=c.contact_id
              AND ct.principal_id=c.principal_id AND ct.verified_at IS NOT NULL)
            THEN RETURN jsonb_build_object('status','invalid');END IF;
          INSERT INTO {q}.sessions(tenant_id,id,principal_id,session_kind,token_hash,csrf_secret_hash,expires_at,staff_credential_version)
            VALUES(p_tenant,p_id,p_principal,'staff',p_new_token,p_new_csrf,clock_timestamp()+make_interval(secs=>p_ttl),p_version);
          UPDATE {q}.sessions SET revoked_at=clock_timestamp(),replaced_by_session_id=p_id WHERE tenant_id=p_tenant AND id=s.id;
          RETURN jsonb_build_object('status','ok','session',{q}.auth_session(p_tenant,p_new_token));
        END $$"""),
        ("auth_staff_memberships(uuid,text)", True, f"""
        CREATE FUNCTION {q}.auth_staff_memberships(p_tenant uuid,p_token text) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT COALESCE(jsonb_agg(jsonb_build_object('id',m.id,'organization_id',m.organization_id,
            'version',m.version,'roles',(SELECT jsonb_agg(r.role ORDER BY r.role) FROM {q}.membership_roles r WHERE(r.tenant_id,r.membership_id)=(m.tenant_id,m.id)),
            'queue_ids',COALESCE((SELECT jsonb_agg(mq.queue_id ORDER BY mq.queue_id) FROM {q}.membership_queues mq
              JOIN {q}.service_queues sq ON(sq.tenant_id,sq.id)=(mq.tenant_id,mq.queue_id)
              WHERE(mq.tenant_id,mq.membership_id)=(m.tenant_id,m.id) AND sq.active),'[]'::jsonb)) ORDER BY m.id),'[]'::jsonb)
          FROM {q}.staff_memberships m JOIN {q}.organizations o ON(o.tenant_id,o.id)=(m.tenant_id,m.organization_id)
          WHERE m.tenant_id=p_tenant AND m.principal_id=({q}.auth_session(p_tenant,p_token)->>'principal_id')::uuid
            AND {q}.auth_session(p_tenant,p_token)->>'kind'='staff' AND m.active AND o.active
            AND EXISTS(SELECT 1 FROM {q}.membership_roles r WHERE(r.tenant_id,r.membership_id)=(m.tenant_id,m.id))
        $$"""),
    ]
    for signature, exposed, definition in definitions:
        op.execute(definition)
        op.execute(f"REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION {q}.{signature} OWNER TO {owner}")
        if exposed:op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")


def downgrade():
    raise RuntimeError("Staff authentication rollback requires a reviewed recovery migration.")
