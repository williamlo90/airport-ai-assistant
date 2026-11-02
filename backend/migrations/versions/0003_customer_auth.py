"""Anonymous/customer authentication; no staff login or business case tables."""

from alembic import context,op

revision="0003_customer_auth"
down_revision="0002_runtime_rls"
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,owner=(quote(attrs[key]) for key in ("schema","runtime_role","auth_role"))
    common=f"""tenant_id uuid NOT NULL REFERENCES {q}.tenants(id) ON DELETE RESTRICT,
        id uuid NOT NULL DEFAULT gen_random_uuid(),created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(tenant_id,id)"""
    op.execute(f"""CREATE TABLE {q}.sessions({common},principal_id uuid,
        session_kind text NOT NULL CHECK(session_kind IN('anonymous','customer','staff')),
        token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{{64}}$'),
        csrf_secret_hash text NOT NULL CHECK(csrf_secret_hash ~ '^[a-f0-9]{{64}}$'),
        expires_at timestamptz NOT NULL,revoked_at timestamptz,replaced_by_session_id uuid,
        CHECK((session_kind='anonymous')=(principal_id IS NULL)),
        FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,replaced_by_session_id) REFERENCES {q}.sessions(tenant_id,id) ON DELETE RESTRICT)""")
    op.execute(f"""CREATE TABLE {q}.verification_challenges({common},contact_id uuid NOT NULL,
        purpose text NOT NULL CHECK(purpose='customer_login'),contact_hash text NOT NULL CHECK(contact_hash ~ '^[a-f0-9]{{64}}$'),
        hashed_code text NOT NULL CHECK(hashed_code ~ '^[a-f0-9]{{64}}$'),origin_session_id uuid NOT NULL,
        binding_nonce_hash text NOT NULL CHECK(binding_nonce_hash ~ '^[a-f0-9]{{64}}$'),expires_at timestamptz NOT NULL,
        attempts integer NOT NULL DEFAULT 0,max_attempts integer NOT NULL CHECK(max_attempts BETWEEN 1 AND 10),
        consumed_at timestamptz,CHECK(attempts BETWEEN 0 AND max_attempts),
        FOREIGN KEY(tenant_id,contact_id) REFERENCES {q}.contacts(tenant_id,id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,origin_session_id) REFERENCES {q}.sessions(tenant_id,id) ON DELETE RESTRICT)""")
    op.execute(f"""CREATE TABLE {q}.auth_rate_limits({common},scope text NOT NULL
        CHECK(scope IN('boot','contact','issue_ip','issue_session','verify_ip','verify_session')),
        subject_hash text NOT NULL CHECK(subject_hash ~ '^[a-f0-9]{{64}}$'),window_start timestamptz NOT NULL,
        hits integer NOT NULL CHECK(hits>0),UNIQUE(tenant_id,scope,subject_hash,window_start))""")
    for table in ("sessions","verification_challenges","auth_rate_limits"):
        op.execute(f"ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {q}.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT ON {q}.{table} TO {owner}")
        columns={"sessions":"revoked_at,replaced_by_session_id","verification_challenges":"attempts,consumed_at","auth_rate_limits":"hits"}[table]
        op.execute(f"GRANT UPDATE({columns}) ON {q}.{table} TO {owner}")
        op.execute(f"CREATE POLICY auth_owner_access ON {q}.{table} TO {owner} USING(true) WITH CHECK(true)")
        op.execute(f"CREATE TRIGGER touch_updated_at BEFORE UPDATE ON {q}.{table} FOR EACH ROW EXECUTE FUNCTION {q}.touch_updated_at()")
    for table in ("principals","contacts"):
        op.execute(f"GRANT SELECT,INSERT ON {q}.{table} TO {owner}")
        op.execute(f"CREATE POLICY auth_owner_access ON {q}.{table} TO {owner} USING(true) WITH CHECK(true)")
    op.execute(f"GRANT UPDATE(principal_id,verified_at) ON {q}.contacts TO {owner}")
    op.execute(f"GRANT SELECT ON {q}.tenants TO {owner}")
    op.execute(f"CREATE POLICY auth_owner_read ON {q}.tenants FOR SELECT TO {owner} USING(true)")
    op.execute(f"GRANT USAGE,CREATE ON SCHEMA {q} TO {owner}")
    op.execute(f"CREATE INDEX sessions_expiry_idx ON {q}.sessions(expires_at)")
    op.execute(f"CREATE INDEX sessions_principal_idx ON {q}.sessions(tenant_id,principal_id)")
    op.execute(f"CREATE INDEX sessions_replacement_idx ON {q}.sessions(tenant_id,replaced_by_session_id) WHERE replaced_by_session_id IS NOT NULL")
    op.execute(f"CREATE INDEX challenge_origin_idx ON {q}.verification_challenges(tenant_id,origin_session_id,purpose)")
    op.execute(f"CREATE INDEX challenge_contact_idx ON {q}.verification_challenges(tenant_id,contact_id)")
    op.execute(f"CREATE INDEX challenge_expiry_idx ON {q}.verification_challenges(expires_at)")
    op.execute(f"CREATE INDEX auth_rate_window_idx ON {q}.auth_rate_limits(window_start)")
    # Each function only accepts keyed digests; raw token/code/key never reach SQL.
    definitions=[
    ("auth_rate(uuid,text,text,integer,integer)",f"""
    CREATE FUNCTION {q}.auth_rate(p_tenant uuid,p_scope text,p_key text,p_window integer,p_limit integer)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE v_hits integer;v_start timestamptz;
    BEGIN
      IF p_window IS NULL OR p_limit IS NULL OR p_window NOT BETWEEN 60 AND 3600 OR p_limit NOT BETWEEN 1 AND 2000 THEN RETURN false;END IF;
      v_start:=to_timestamp(floor(extract(epoch FROM clock_timestamp())/p_window)*p_window);
      INSERT INTO {q}.auth_rate_limits(tenant_id,scope,subject_hash,window_start,hits)
        VALUES(p_tenant,p_scope,p_key,v_start,1)
        ON CONFLICT(tenant_id,scope,subject_hash,window_start) DO UPDATE SET
          hits=LEAST({q}.auth_rate_limits.hits::bigint+1,2147483647)::integer RETURNING hits INTO v_hits;
      RETURN v_hits<=p_limit;
    END $$"""),
    ("auth_session(uuid,text)",f"""
    CREATE FUNCTION {q}.auth_session(p_tenant uuid,p_hash text) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
      SELECT jsonb_build_object('id',s.id,'tenant_id',s.tenant_id,'principal_id',s.principal_id,
        'kind',s.session_kind,'expires_at',s.expires_at,'csrf_hash',s.csrf_secret_hash)
      FROM {q}.sessions s JOIN {q}.tenants t ON t.id=s.tenant_id
      LEFT JOIN {q}.principals p ON(p.tenant_id,p.id)=(s.tenant_id,s.principal_id)
      WHERE s.tenant_id=p_tenant AND s.token_hash=p_hash AND s.revoked_at IS NULL
        AND s.expires_at>clock_timestamp() AND t.active
        AND s.session_kind IN('anonymous','customer') AND (s.principal_id IS NULL OR p.active)
    $$"""),
    ("auth_new_anonymous(uuid,uuid,text,text,text,integer,integer,integer)",f"""
    CREATE FUNCTION {q}.auth_new_anonymous(p_tenant uuid,p_id uuid,p_token text,p_csrf text,p_ip text,p_ttl integer,p_window integer,p_limit integer)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF p_ttl IS NULL OR p_ttl NOT BETWEEN 300 AND 172800 OR NOT EXISTS(SELECT 1 FROM {q}.tenants WHERE id=p_tenant AND active)
        THEN RETURN NULL;END IF;
      IF NOT {q}.auth_rate(p_tenant,'boot',p_ip,p_window,p_limit) THEN RETURN NULL;END IF;
      INSERT INTO {q}.sessions(tenant_id,id,session_kind,token_hash,csrf_secret_hash,expires_at)
        VALUES(p_tenant,p_id,'anonymous',p_token,p_csrf,clock_timestamp()+make_interval(secs=>p_ttl));
      RETURN {q}.auth_session(p_tenant,p_token);
    END $$"""),
    ("auth_issue(uuid,text,text,uuid,text,text,text,text,integer,integer,integer,integer,integer,integer)",f"""
    CREATE FUNCTION {q}.auth_issue(p_tenant uuid,p_token text,p_csrf text,p_id uuid,p_contact text,p_contact_hash text,
      p_code_hash text,p_ip text,p_ttl integer,p_attempts integer,p_window integer,p_contact_limit integer,p_ip_limit integer,p_session_limit integer)
    RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE v_session {q}.sessions%ROWTYPE;v_contact uuid;v_contact_ok boolean;v_ip_ok boolean;v_session_ok boolean;
    BEGIN
      SELECT * INTO v_session FROM {q}.sessions WHERE tenant_id=p_tenant AND token_hash=p_token FOR UPDATE;
      IF NOT FOUND OR v_session.session_kind<>'anonymous' OR v_session.revoked_at IS NOT NULL OR v_session.expires_at<=clock_timestamp()
        THEN RETURN 'invalid_session';END IF;
      IF NOT EXISTS(SELECT 1 FROM {q}.tenants WHERE id=p_tenant AND active) THEN RETURN 'invalid_session';END IF;
      IF v_session.csrf_secret_hash IS DISTINCT FROM p_csrf THEN RETURN 'csrf_failed';END IF;
      IF p_ttl IS NULL OR p_attempts IS NULL OR p_ttl NOT BETWEEN 30 AND 600 OR p_attempts NOT BETWEEN 1 AND 10 THEN RETURN 'invalid';END IF;
      v_contact_ok:={q}.auth_rate(p_tenant,'contact',p_contact_hash,p_window,p_contact_limit);
      v_ip_ok:={q}.auth_rate(p_tenant,'issue_ip',p_ip,p_window,p_ip_limit);
      v_session_ok:={q}.auth_rate(p_tenant,'issue_session',v_session.token_hash,p_window,p_session_limit);
      IF NOT(v_contact_ok AND v_ip_ok AND v_session_ok) THEN RETURN 'rate_limited';END IF;
      INSERT INTO {q}.contacts(tenant_id,email_address,email_lookup_key) VALUES(p_tenant,p_contact,p_contact)
        ON CONFLICT(tenant_id,email_lookup_key) DO NOTHING;
      SELECT id INTO v_contact FROM {q}.contacts WHERE tenant_id=p_tenant AND email_lookup_key=p_contact;
      UPDATE {q}.verification_challenges SET consumed_at=clock_timestamp()
        WHERE tenant_id=p_tenant AND origin_session_id=v_session.id AND purpose='customer_login' AND consumed_at IS NULL;
      INSERT INTO {q}.verification_challenges(tenant_id,id,contact_id,purpose,contact_hash,hashed_code,origin_session_id,binding_nonce_hash,expires_at,max_attempts)
        VALUES(p_tenant,p_id,v_contact,'customer_login',p_contact_hash,p_code_hash,v_session.id,p_csrf,
          clock_timestamp()+make_interval(secs=>p_ttl),p_attempts);
      RETURN 'ok';
    END $$"""),
    ("auth_cancel(uuid,uuid,text)",f"""
    CREATE FUNCTION {q}.auth_cancel(p_tenant uuid,p_id uuid,p_token text) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
      UPDATE {q}.verification_challenges c SET consumed_at=clock_timestamp()
      FROM {q}.sessions s WHERE c.tenant_id=p_tenant AND c.id=p_id AND c.consumed_at IS NULL
        AND(s.tenant_id,s.id)=(c.tenant_id,c.origin_session_id) AND s.token_hash=p_token
    $$"""),
    ("auth_verify(uuid,uuid,text,text,text,uuid,text,text,integer,text,integer,integer,integer)",f"""
    CREATE FUNCTION {q}.auth_verify(p_tenant uuid,p_id uuid,p_token text,p_csrf text,p_code_hash text,
      p_new_id uuid,p_new_token text,p_new_csrf text,p_ttl integer,p_ip text,p_window integer,p_ip_limit integer,p_session_limit integer)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE v_session {q}.sessions%ROWTYPE;v_challenge {q}.verification_challenges%ROWTYPE;
      v_contact {q}.contacts%ROWTYPE;v_principal uuid;v_ip_ok boolean;v_session_ok boolean;
    BEGIN
      SELECT * INTO v_session FROM {q}.sessions WHERE tenant_id=p_tenant AND token_hash=p_token FOR UPDATE;
      IF NOT FOUND OR v_session.session_kind<>'anonymous' OR v_session.revoked_at IS NOT NULL OR v_session.expires_at<=clock_timestamp()
        THEN RETURN jsonb_build_object('status','invalid_session');END IF;
      IF NOT EXISTS(SELECT 1 FROM {q}.tenants WHERE id=p_tenant AND active) THEN RETURN jsonb_build_object('status','invalid_session');END IF;
      IF v_session.csrf_secret_hash IS DISTINCT FROM p_csrf THEN RETURN jsonb_build_object('status','csrf_failed');END IF;
      v_ip_ok:={q}.auth_rate(p_tenant,'verify_ip',p_ip,p_window,p_ip_limit);
      v_session_ok:={q}.auth_rate(p_tenant,'verify_session',v_session.token_hash,p_window,p_session_limit);
      IF NOT(v_ip_ok AND v_session_ok) THEN RETURN jsonb_build_object('status','rate_limited');END IF;
      SELECT * INTO v_challenge FROM {q}.verification_challenges WHERE tenant_id=p_tenant AND id=p_id FOR UPDATE;
      IF NOT FOUND OR v_challenge.origin_session_id IS DISTINCT FROM v_session.id OR v_challenge.binding_nonce_hash IS DISTINCT FROM p_csrf
        OR v_challenge.purpose<>'customer_login'
        OR v_challenge.consumed_at IS NOT NULL OR v_challenge.expires_at<=clock_timestamp()
        OR v_challenge.attempts>=v_challenge.max_attempts THEN RETURN jsonb_build_object('status','invalid');END IF;
      UPDATE {q}.verification_challenges SET attempts=attempts+1 WHERE tenant_id=p_tenant AND id=p_id;
      IF v_challenge.hashed_code IS DISTINCT FROM p_code_hash OR p_ttl IS NULL OR p_ttl NOT BETWEEN 300 AND 86400
        THEN RETURN jsonb_build_object('status','invalid');END IF;
      SELECT * INTO v_contact FROM {q}.contacts WHERE tenant_id=p_tenant AND id=v_challenge.contact_id FOR UPDATE;
      v_principal:=v_contact.principal_id;
      IF v_principal IS NULL THEN
        INSERT INTO {q}.principals(tenant_id) VALUES(p_tenant) RETURNING id INTO v_principal;
      ELSIF NOT EXISTS(SELECT 1 FROM {q}.principals WHERE tenant_id=p_tenant AND id=v_principal AND active)
        THEN RETURN jsonb_build_object('status','invalid');END IF;
      UPDATE {q}.contacts SET principal_id=v_principal,verified_at=COALESCE(verified_at,clock_timestamp())
        WHERE tenant_id=p_tenant AND id=v_contact.id;
      INSERT INTO {q}.sessions(tenant_id,id,principal_id,session_kind,token_hash,csrf_secret_hash,expires_at)
        VALUES(p_tenant,p_new_id,v_principal,'customer',p_new_token,p_new_csrf,clock_timestamp()+make_interval(secs=>p_ttl));
      UPDATE {q}.verification_challenges SET consumed_at=clock_timestamp() WHERE tenant_id=p_tenant AND id=p_id;
      UPDATE {q}.sessions SET revoked_at=clock_timestamp(),replaced_by_session_id=p_new_id WHERE tenant_id=p_tenant AND id=v_session.id;
      RETURN jsonb_build_object('status','ok','session',{q}.auth_session(p_tenant,p_new_token));
    END $$"""),
    ("auth_revoke(uuid,text,text)",f"""
    CREATE FUNCTION {q}.auth_revoke(p_tenant uuid,p_token text,p_csrf text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      UPDATE {q}.sessions SET revoked_at=clock_timestamp() WHERE tenant_id=p_tenant AND token_hash=p_token
        AND csrf_secret_hash=p_csrf AND revoked_at IS NULL AND expires_at>clock_timestamp();
      RETURN FOUND;
    END $$"""),
    ]
    for signature,definition in definitions:
        op.execute(definition)
        op.execute(f"REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION {q}.{signature} OWNER TO {owner}")
        if not signature.startswith("auth_rate("):
            op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")


def downgrade():
    raise RuntimeError("Authentication rollback requires a reviewed forward/recovery migration.")
