"""Atomic browser-bound history adoption and draft invalidation."""

from alembic import context,op

revision='0007_anonymous_adoption'
down_revision='0006_action_drafts'
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime,owner=(quote(attrs[key]) for key in ('schema','runtime_role','auth_role'))
    op.execute(f"""ALTER TABLE {q}.conversations
        ADD COLUMN adopted_from_session_id uuid,
        ADD COLUMN adopted_at timestamptz,
        ADD CONSTRAINT conversation_adoption_origin FOREIGN KEY(tenant_id,adopted_from_session_id) REFERENCES {q}.sessions(tenant_id,id),
        ADD CONSTRAINT conversation_adoption_shape CHECK(
            (adopted_from_session_id IS NULL AND adopted_at IS NULL)
            OR(adopted_from_session_id IS NOT NULL AND adopted_at IS NOT NULL AND customer_principal_id IS NOT NULL AND anonymous_session_id IS NULL))""")
    op.execute(f"""ALTER TABLE {q}.messages ADD COLUMN origin_session_id uuid,
        ADD CONSTRAINT message_origin_session FOREIGN KEY(tenant_id,origin_session_id) REFERENCES {q}.sessions(tenant_id,id),
        ADD CONSTRAINT message_origin_shape CHECK(origin_session_id IS NULL OR(author_session_id IS NULL AND author_principal_id IS NOT NULL))""")
    op.execute(f"CREATE INDEX conversation_adoption_idx ON {q}.conversations(tenant_id,adopted_from_session_id) WHERE adopted_from_session_id IS NOT NULL")
    op.execute(f"CREATE INDEX message_origin_idx ON {q}.messages(tenant_id,origin_session_id) WHERE origin_session_id IS NOT NULL")
    # Provenance can only be set by the authenticated adoption function. Ordinary
    # customer inserts retain their current DAO columns and server defaults.
    op.execute(f"REVOKE INSERT ON {q}.conversations,{q}.messages FROM {runtime}")
    op.execute(f"GRANT INSERT(tenant_id,id,customer_principal_id,anonymous_session_id,language) ON {q}.conversations TO {runtime}")
    op.execute(f"GRANT INSERT(tenant_id,id,conversation_id,author_principal_id,author_session_id,author_type,body,language,client_message_id,sequence) ON {q}.messages TO {runtime}")
    scope_t="nullif(current_setting('app.adoption_tenant',true),'')::uuid"
    scope_s="nullif(current_setting('app.adoption_session',true),'')::uuid"
    scope_p="nullif(current_setting('app.adoption_principal',true),'')::uuid"
    op.execute(f"GRANT SELECT(tenant_id,id,customer_principal_id,anonymous_session_id,case_id,resource_security_labels,version,generation_epoch,adopted_from_session_id,adopted_at) ON {q}.conversations TO {owner}")
    op.execute(f"GRANT UPDATE(customer_principal_id,anonymous_session_id,version,generation_epoch,adopted_from_session_id,adopted_at) ON {q}.conversations TO {owner}")
    old=f"tenant_id={scope_t} AND anonymous_session_id={scope_s} AND customer_principal_id IS NULL"
    new=f"tenant_id={scope_t} AND adopted_from_session_id={scope_s} AND customer_principal_id={scope_p} AND anonymous_session_id IS NULL AND adopted_at IS NOT NULL"
    op.execute(f"CREATE POLICY auth_adoption_read ON {q}.conversations FOR SELECT TO {owner} USING(({old}) OR ({new}))")
    op.execute(f"CREATE POLICY auth_adoption_write ON {q}.conversations FOR UPDATE TO {owner} USING({old}) WITH CHECK(({new}) AND case_id IS NULL AND resource_security_labels='[]'::jsonb)")
    op.execute(f"GRANT SELECT(tenant_id,id,conversation_id,author_principal_id,author_session_id,origin_session_id) ON {q}.messages TO {owner}")
    op.execute(f"GRANT UPDATE(author_principal_id,author_session_id,origin_session_id) ON {q}.messages TO {owner}")
    parent=f"EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.conversation_id AND c.adopted_from_session_id={scope_s} AND c.customer_principal_id={scope_p})"
    message_scope=f"tenant_id={scope_t} AND ({parent})"
    message_new=f"author_principal_id={scope_p} AND author_session_id IS NULL AND origin_session_id={scope_s}"
    op.execute(f"CREATE POLICY auth_adoption_read ON {q}.messages FOR SELECT TO {owner} USING({message_scope} AND (author_session_id={scope_s} OR ({message_new})))")
    op.execute(f"CREATE POLICY auth_adoption_write ON {q}.messages FOR UPDATE TO {owner} USING({message_scope} AND author_session_id={scope_s} AND author_principal_id IS NULL) WITH CHECK({message_scope} AND ({message_new}))")
    op.execute(f"GRANT SELECT(tenant_id,id,created_session_id,actor_session_id,actor_principal_id,state) ON {q}.action_drafts TO {owner}")
    op.execute(f"GRANT UPDATE(state) ON {q}.action_drafts TO {owner}")
    draft_scope=f"tenant_id={scope_t} AND created_session_id={scope_s} AND actor_session_id={scope_s} AND actor_principal_id IS NULL"
    op.execute(f"CREATE POLICY auth_adoption_read ON {q}.action_drafts FOR SELECT TO {owner} USING({draft_scope})")
    op.execute(f"CREATE POLICY auth_adoption_write ON {q}.action_drafts FOR UPDATE TO {owner} USING({draft_scope} AND state='pending') WITH CHECK({draft_scope} AND state IN('cancelled','expired'))")
    op.execute(f"""CREATE OR REPLACE FUNCTION {q}.auth_verify(p_tenant uuid,p_id uuid,p_token text,p_csrf text,p_code_hash text,
      p_new_id uuid,p_new_token text,p_new_csrf text,p_ttl integer,p_ip text,p_window integer,p_ip_limit integer,p_session_limit integer)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE v_session {q}.sessions%ROWTYPE;v_challenge {q}.verification_challenges%ROWTYPE;
      v_contact {q}.contacts%ROWTYPE;v_principal uuid;v_ip_ok boolean;v_session_ok boolean;v_result jsonb;
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
        OR v_challenge.purpose<>'customer_login' OR v_challenge.consumed_at IS NOT NULL OR v_challenge.expires_at<=clock_timestamp()
        OR v_challenge.attempts>=v_challenge.max_attempts THEN RETURN jsonb_build_object('status','invalid');END IF;
      UPDATE {q}.verification_challenges SET attempts=attempts+1 WHERE tenant_id=p_tenant AND id=p_id;
      IF v_challenge.hashed_code IS DISTINCT FROM p_code_hash OR p_ttl IS NULL OR p_ttl NOT BETWEEN 300 AND 86400
        THEN RETURN jsonb_build_object('status','invalid');END IF;
      SELECT * INTO v_contact FROM {q}.contacts WHERE tenant_id=p_tenant AND id=v_challenge.contact_id FOR UPDATE;
      v_principal:=v_contact.principal_id;
      IF v_principal IS NOT NULL AND NOT EXISTS(SELECT 1 FROM {q}.principals WHERE tenant_id=p_tenant AND id=v_principal AND active)
        THEN RETURN jsonb_build_object('status','invalid');END IF;
      PERFORM set_config('app.adoption_tenant',p_tenant::text,true);
      PERFORM set_config('app.adoption_session',v_session.id::text,true);
      PERFORM set_config('app.adoption_principal',coalesce(v_principal::text,''),true);
      PERFORM id FROM {q}.conversations WHERE tenant_id=p_tenant AND anonymous_session_id=v_session.id ORDER BY id FOR UPDATE;
      PERFORM id FROM {q}.action_drafts WHERE tenant_id=p_tenant AND created_session_id=v_session.id AND actor_session_id=v_session.id ORDER BY id FOR UPDATE;
      IF EXISTS(SELECT 1 FROM {q}.conversations WHERE tenant_id=p_tenant AND anonymous_session_id=v_session.id
          AND(case_id IS NOT NULL OR resource_security_labels<>'[]'::jsonb)) THEN RETURN jsonb_build_object('status','invalid');END IF;
      IF v_session.expires_at<=clock_timestamp() THEN RETURN jsonb_build_object('status','invalid_session');END IF;
      IF v_challenge.expires_at<=clock_timestamp() THEN RETURN jsonb_build_object('status','invalid');END IF;
      -- A subtransaction preserves ordinary attempt/rate accounting while rolling
      -- back every adoption/identity mutation if eligibility expires at the end.
      BEGIN
        IF v_principal IS NULL THEN INSERT INTO {q}.principals(tenant_id) VALUES(p_tenant) RETURNING id INTO v_principal;END IF;
        PERFORM set_config('app.adoption_principal',v_principal::text,true);
        UPDATE {q}.contacts SET principal_id=v_principal,verified_at=COALESCE(verified_at,clock_timestamp()) WHERE tenant_id=p_tenant AND id=v_contact.id;
        INSERT INTO {q}.sessions(tenant_id,id,principal_id,session_kind,token_hash,csrf_secret_hash,expires_at)
          VALUES(p_tenant,p_new_id,v_principal,'customer',p_new_token,p_new_csrf,clock_timestamp()+make_interval(secs=>p_ttl));
        UPDATE {q}.conversations SET customer_principal_id=v_principal,anonymous_session_id=NULL,
          adopted_from_session_id=v_session.id,adopted_at=clock_timestamp(),version=version+1,generation_epoch=generation_epoch+1
          WHERE tenant_id=p_tenant AND anonymous_session_id=v_session.id AND customer_principal_id IS NULL;
        UPDATE {q}.messages SET author_principal_id=v_principal,author_session_id=NULL,origin_session_id=v_session.id
          WHERE tenant_id=p_tenant AND author_session_id=v_session.id AND author_principal_id IS NULL
            AND EXISTS(SELECT 1 FROM {q}.conversations c WHERE c.tenant_id=messages.tenant_id AND c.id=messages.conversation_id
              AND c.adopted_from_session_id=v_session.id AND c.customer_principal_id=v_principal);
        UPDATE {q}.action_drafts SET state='cancelled' WHERE tenant_id=p_tenant AND created_session_id=v_session.id
          AND actor_session_id=v_session.id AND actor_principal_id IS NULL AND state='pending';
        UPDATE {q}.verification_challenges SET consumed_at=clock_timestamp() WHERE tenant_id=p_tenant AND id=p_id;
        UPDATE {q}.sessions SET revoked_at=clock_timestamp(),replaced_by_session_id=p_new_id WHERE tenant_id=p_tenant AND id=v_session.id;
        v_result:={q}.auth_session(p_tenant,p_new_token);
        IF v_result IS NULL OR v_session.expires_at<=clock_timestamp() THEN
          RAISE EXCEPTION 'Verification eligibility ended' USING ERRCODE='Z0001';END IF;
        IF v_challenge.expires_at<=clock_timestamp() THEN
          RAISE EXCEPTION 'Challenge expired' USING ERRCODE='Z0002';END IF;
        RETURN jsonb_build_object('status','ok','session',v_result);
      EXCEPTION WHEN SQLSTATE 'Z0001' THEN RETURN jsonb_build_object('status','invalid_session');
        WHEN SQLSTATE 'Z0002' THEN RETURN jsonb_build_object('status','invalid');
      END;
    END $$""")
    signature='auth_verify(uuid,uuid,text,text,text,uuid,text,text,integer,text,integer,integer,integer)'
    op.execute(f"REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}")


def downgrade():
    raise RuntimeError('Identity adoption downgrade requires a reviewed recovery migration.')
