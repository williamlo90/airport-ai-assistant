"""Durable private evidence, parent locking and atomic confirmed-case binding."""
from alembic import context,op

revision='0010_attachments'
down_revision='0009_case_lifecycle'
branch_labels=None
depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime=(quote(context.config.attributes[k]) for k in ('schema','runtime_role'))
    op.execute(f"""ALTER TABLE {q}.action_drafts DROP CONSTRAINT action_drafts_payload_check,
      ADD CONSTRAINT action_drafts_payload_check CHECK(jsonb_typeof(payload)='object'
        AND payload ?& ARRAY['category','service_type','summary','customer_report','attachment_ids']
        AND payload-ARRAY['category','service_type','summary','customer_report','booking_reference','transaction_reference','contact_email','attachment_ids']='{{}}'::jsonb
        AND jsonb_typeof(payload->'attachment_ids')='array' AND jsonb_array_length(payload->'attachment_ids')<=3)""")
    op.execute(f"""CREATE TABLE {q}.attachments(
      tenant_id uuid NOT NULL REFERENCES {q}.tenants(id),id uuid NOT NULL,draft_id uuid NOT NULL,case_id uuid,
      uploader_principal_id uuid,uploader_session_id uuid,created_session_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),body bytea NOT NULL,
      mime_type text NOT NULL CHECK(mime_type IN('image/png','image/jpeg','application/pdf')),
      byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 5242880 AND byte_size=octet_length(body)),
      content_hash text NOT NULL CHECK(content_hash=encode(sha256(body),'hex')),
      scan_status text NOT NULL CHECK(scan_status='clean'),
      resource_security_labels jsonb NOT NULL DEFAULT '[]' CHECK(resource_security_labels='[]'::jsonb),
      CHECK((uploader_principal_id IS NULL)<>(uploader_session_id IS NULL)),
      CHECK(uploader_session_id IS NULL OR uploader_session_id=created_session_id),
      PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,draft_id) REFERENCES {q}.action_drafts(tenant_id,id),
      FOREIGN KEY(tenant_id,case_id) REFERENCES {q}.cases(tenant_id,id),
      FOREIGN KEY(tenant_id,uploader_principal_id) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,uploader_session_id) REFERENCES {q}.sessions(tenant_id,id),
      FOREIGN KEY(tenant_id,created_session_id) REFERENCES {q}.sessions(tenant_id,id))""")
    op.execute(f"CREATE INDEX attachment_parent ON {q}.attachments(tenant_id,draft_id)")
    op.execute(f"CREATE INDEX attachment_case ON {q}.attachments(tenant_id,case_id) WHERE case_id IS NOT NULL")
    op.execute(f"ALTER TABLE {q}.attachments ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {q}.attachments FORCE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON {q}.attachments FROM PUBLIC")
    op.execute(f"GRANT SELECT ON {q}.attachments TO {runtime}")
    op.execute(f"GRANT INSERT(tenant_id,id,draft_id,uploader_principal_id,uploader_session_id,created_session_id,body,mime_type,byte_size,content_hash,scan_status) ON {q}.attachments TO {runtime}")
    op.execute(f"GRANT UPDATE(case_id) ON {q}.attachments TO {runtime}")
    parent=f"""EXISTS(SELECT 1 FROM {q}.action_drafts d WHERE d.tenant_id=attachments.tenant_id AND d.id=attachments.draft_id
      AND d.created_session_id=attachments.created_session_id AND d.created_session_id={q}.current_session_id()
      AND d.actor_principal_id IS NOT DISTINCT FROM attachments.uploader_principal_id
      AND d.actor_session_id IS NOT DISTINCT FROM attachments.uploader_session_id)"""
    pending=f"""{parent} AND EXISTS(SELECT 1 FROM {q}.action_drafts d WHERE d.tenant_id=attachments.tenant_id AND d.id=attachments.draft_id AND d.state='pending' AND d.expires_at>clock_timestamp())"""
    base=f"tenant_id={q}.current_tenant_id() AND resource_security_labels='[]'::jsonb"
    op.execute(f"""CREATE POLICY runtime_read ON {q}.attachments FOR SELECT TO {runtime} USING({base} AND(
      (case_id IS NULL AND ({pending} OR ({parent} AND EXISTS(SELECT 1 FROM {q}.action_drafts d WHERE d.tenant_id=attachments.tenant_id AND d.id=attachments.draft_id AND d.state='committed' AND d.payload->'attachment_ids' ? attachments.id::text)))) OR(case_id IS NOT NULL AND EXISTS(SELECT 1 FROM {q}.cases c WHERE c.tenant_id=attachments.tenant_id AND c.id=attachments.case_id))))""")
    op.execute(f"CREATE POLICY runtime_insert ON {q}.attachments FOR INSERT TO {runtime} WITH CHECK({base} AND case_id IS NULL AND {pending})")
    op.execute(f"""CREATE POLICY runtime_bind ON {q}.attachments FOR UPDATE TO {runtime} USING({base} AND {parent}) WITH CHECK({base} AND {parent}
       AND EXISTS(SELECT 1 FROM {q}.cases c JOIN {q}.action_drafts d ON(d.tenant_id,d.id)=(c.tenant_id,c.origin_draft_id)
         WHERE c.tenant_id=attachments.tenant_id AND c.id=attachments.case_id AND d.id=attachments.draft_id AND d.state='committed'))""")
    op.execute(f"""CREATE FUNCTION {q}.guard_attachment() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE d {q}.action_drafts%ROWTYPE;BEGIN
        SELECT * INTO d FROM {q}.action_drafts WHERE tenant_id=NEW.tenant_id AND id=NEW.draft_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Attachment parent unavailable' USING ERRCODE='23514';END IF;
        IF TG_OP='INSERT' THEN
          IF d.state<>'pending' OR d.expires_at<=clock_timestamp() OR NEW.case_id IS NOT NULL
            OR d.created_session_id IS DISTINCT FROM NEW.created_session_id
            OR d.actor_principal_id IS DISTINCT FROM NEW.uploader_principal_id OR d.actor_session_id IS DISTINCT FROM NEW.uploader_session_id
            OR(SELECT count(*) FROM {q}.attachments WHERE tenant_id=NEW.tenant_id AND draft_id=NEW.draft_id)>=3 THEN
            RAISE EXCEPTION 'Attachment parent invalid' USING ERRCODE='23514';END IF;
        ELSE
          IF OLD.case_id IS NOT NULL OR NEW.case_id IS NULL OR(to_jsonb(NEW)-'case_id') IS DISTINCT FROM(to_jsonb(OLD)-'case_id')
            OR d.state<>'committed' OR d.result_case_id IS DISTINCT FROM NEW.case_id
            OR NOT(d.payload->'attachment_ids' ? NEW.id::text) THEN
            RAISE EXCEPTION 'Immutable attachment' USING ERRCODE='23514';END IF;
        END IF;RETURN NEW;
      END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.guard_attachment() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER attachment_guard BEFORE INSERT OR UPDATE ON {q}.attachments FOR EACH ROW EXECUTE FUNCTION {q}.guard_attachment()")
    op.execute(f"""CREATE FUNCTION {q}.validate_draft_attachments() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE aid text;seen uuid[]:=ARRAY[]::uuid[];item {q}.attachments%ROWTYPE;BEGIN
        IF jsonb_typeof(NEW.payload->'attachment_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.payload->'attachment_ids')>3 THEN
          RAISE EXCEPTION 'Invalid attachments' USING ERRCODE='23514';END IF;
        FOR aid IN SELECT jsonb_array_elements_text(NEW.payload->'attachment_ids') LOOP
          IF aid::uuid=ANY(seen) THEN RAISE EXCEPTION 'Repeated attachment' USING ERRCODE='23514';END IF;
          seen:=array_append(seen,aid::uuid);
          SELECT * INTO item FROM {q}.attachments WHERE tenant_id=NEW.tenant_id AND id=aid::uuid FOR SHARE;
          IF NOT FOUND OR item.draft_id<>NEW.id OR item.scan_status<>'clean' OR item.resource_security_labels<>'[]'::jsonb
            OR item.created_session_id<>NEW.created_session_id THEN
            RAISE EXCEPTION 'Invalid attachment selection' USING ERRCODE='23514';END IF;
        END LOOP;RETURN NEW;
      END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.validate_draft_attachments() FROM PUBLIC")
    # Terminal expiry/adoption changes must not need access to old file bodies.
    op.execute(f"CREATE TRIGGER draft_attachment_selection BEFORE INSERT OR UPDATE OF payload ON {q}.action_drafts FOR EACH ROW EXECUTE FUNCTION {q}.validate_draft_attachments()")
    op.execute(f"""CREATE FUNCTION {q}.bind_case_attachments() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF OLD.state='pending' AND NEW.state='committed' THEN
        UPDATE {q}.attachments SET case_id=NEW.result_case_id WHERE tenant_id=NEW.tenant_id AND draft_id=NEW.id AND NEW.payload->'attachment_ids' ? id::text;
        IF (SELECT count(*) FROM {q}.attachments WHERE tenant_id=NEW.tenant_id AND draft_id=NEW.id AND case_id=NEW.result_case_id)<>jsonb_array_length(NEW.payload->'attachment_ids') THEN
          RAISE EXCEPTION 'Incomplete attachment binding' USING ERRCODE='23514';END IF;
      END IF;RETURN NEW;END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION {q}.bind_case_attachments() FROM PUBLIC")
    op.execute(f"CREATE TRIGGER case_attachment_binding AFTER UPDATE OF state ON {q}.action_drafts FOR EACH ROW EXECUTE FUNCTION {q}.bind_case_attachments()")


def downgrade():
    raise RuntimeError('Attachment downgrade requires a reviewed recovery migration.')
