"""Versioned organization-managed knowledge, forced RLS and immutable audit."""
from alembic import context,op
revision='0024_knowledge'
down_revision='0023_adoption_guard'
branch_labels=None
depends_on=None

def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,r,o=(quote(context.config.attributes[k]) for k in ('schema','runtime_role','auth_role'))
    owner_literal="'"+context.config.attributes['auth_role'].replace("'","''")+"'"
    op.execute(f"GRANT CREATE ON SCHEMA {q} TO {o}")
    statements=[
    f"""CREATE TABLE {q}.knowledge_documents(
      tenant_id uuid NOT NULL REFERENCES {q}.tenants(id),id uuid NOT NULL DEFAULT gen_random_uuid(),
      organization_id uuid NOT NULL,document_key text NOT NULL CHECK(length(document_key)<=100 AND document_key~'^[a-z0-9]+(-[a-z0-9]+)*$'),
      title text NOT NULL CHECK(length(title) BETWEEN 1 AND 200 AND length(btrim(title))>0),revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,document_key),
      FOREIGN KEY(tenant_id,organization_id) REFERENCES {q}.organizations(tenant_id,id),
      FOREIGN KEY(tenant_id,created_by) REFERENCES {q}.principals(tenant_id,id))""",
    f"""CREATE TABLE {q}.knowledge_versions(
      tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),document_id uuid NOT NULL,version integer NOT NULL CHECK(version>0),revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
      body text NOT NULL CHECK(length(body) BETWEEN 1 AND 20000 AND length(btrim(body))>0),source_uri text NOT NULL CHECK(length(source_uri) BETWEEN 1 AND 2048),
      service_type text NOT NULL CHECK(service_type IN('general','parking','lounge')),language text NOT NULL CHECK(language IN('en','id')),
      visibility text NOT NULL CHECK(visibility IN('public','staff')),organization_id uuid,allowed_roles text[] NOT NULL DEFAULT '{{}}',
      valid_from timestamptz NOT NULL,valid_to timestamptz CHECK(valid_to IS NULL OR valid_to>valid_from),applicability jsonb NOT NULL,
      policy_reference text CHECK(policy_reference IS NULL OR length(policy_reference) BETWEEN 1 AND 200),supersedes_version_id uuid,
      content_hash text NOT NULL,basis_hash text NOT NULL,status text NOT NULL DEFAULT 'draft' CHECK(status IN('draft','published','withdrawn')),
      published_at timestamptz,published_by uuid,withdrawn_at timestamptz,withdrawn_by uuid,withdrawal_reason text,
      index_generation uuid,index_basis_hash text,index_valid_until timestamptz,created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,document_id,version),UNIQUE(tenant_id,document_id,id),
      FOREIGN KEY(tenant_id,document_id) REFERENCES {q}.knowledge_documents(tenant_id,id),
      FOREIGN KEY(tenant_id,document_id,supersedes_version_id) REFERENCES {q}.knowledge_versions(tenant_id,document_id,id),
      FOREIGN KEY(tenant_id,organization_id) REFERENCES {q}.organizations(tenant_id,id),
      FOREIGN KEY(tenant_id,created_by) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,published_by) REFERENCES {q}.principals(tenant_id,id),FOREIGN KEY(tenant_id,withdrawn_by) REFERENCES {q}.principals(tenant_id,id),
      CHECK(array_ndims(allowed_roles)=1 OR cardinality(allowed_roles)=0),CHECK(array_position(allowed_roles,NULL) IS NULL AND allowed_roles<@ARRAY['admin','cs','supervisor']::text[]),
      CHECK((visibility='public' AND organization_id IS NULL AND cardinality(allowed_roles)=0) OR(visibility='staff' AND organization_id IS NOT NULL AND cardinality(allowed_roles)>0)),
      CHECK((status='draft' AND published_at IS NULL AND published_by IS NULL AND index_generation IS NULL AND index_basis_hash IS NULL AND index_valid_until IS NULL AND withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawal_reason IS NULL)
        OR(status='published' AND published_at IS NOT NULL AND published_by IS NOT NULL AND index_generation IS NOT NULL AND index_basis_hash IS NOT NULL AND index_basis_hash=basis_hash AND index_valid_until IS NOT NULL AND published_at<index_valid_until AND withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawal_reason IS NULL)
        OR(status='withdrawn' AND published_at IS NOT NULL AND published_by IS NOT NULL AND index_generation IS NOT NULL AND index_basis_hash IS NOT NULL AND index_basis_hash=basis_hash AND index_valid_until IS NOT NULL AND published_at<index_valid_until AND withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL AND withdrawal_reason IS NOT NULL AND length(btrim(withdrawal_reason))>0 AND length(withdrawal_reason)<=1000)))""",
    f"""CREATE TABLE {q}.knowledge_audit(
      tenant_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),document_id uuid NOT NULL,version_id uuid,
      document_revision integer NOT NULL,version_revision integer,actor_principal_id uuid NOT NULL,actor_membership_id uuid NOT NULL,
      event_type text NOT NULL CHECK(event_type IN('document_created','version_created','draft_updated','published','withdrawn')),
      basis_hash text,index_generation uuid,reason text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,document_id,document_revision),
      FOREIGN KEY(tenant_id,document_id) REFERENCES {q}.knowledge_documents(tenant_id,id),
      FOREIGN KEY(tenant_id,document_id,version_id) REFERENCES {q}.knowledge_versions(tenant_id,document_id,id),
      FOREIGN KEY(tenant_id,actor_principal_id) REFERENCES {q}.principals(tenant_id,id),
      FOREIGN KEY(tenant_id,actor_membership_id) REFERENCES {q}.staff_memberships(tenant_id,id))""",
    f"CREATE INDEX knowledge_select_idx ON {q}.knowledge_versions(tenant_id,document_id,service_type,language,policy_reference,valid_from) WHERE status='published'",
    ]
    for s in statements:op.execute(s)
    for table in ('knowledge_documents','knowledge_versions','knowledge_audit'):
        op.execute(f'ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY');op.execute(f'ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON {q}.{table} FROM PUBLIC');op.execute(f'GRANT SELECT ON {q}.{table} TO {r},{o}')
        op.execute(f'CREATE POLICY knowledge_owner_read ON {q}.{table} FOR SELECT TO {o} USING(true)')
    op.execute(f'GRANT UPDATE(revision,updated_at) ON {q}.knowledge_documents TO {o}')
    op.execute(f'CREATE POLICY knowledge_owner_update ON {q}.knowledge_documents FOR UPDATE TO {o} USING(true) WITH CHECK(true)')
    op.execute(f'GRANT INSERT ON {q}.knowledge_audit TO {o}')
    op.execute(f'CREATE POLICY knowledge_owner_insert ON {q}.knowledge_audit FOR INSERT TO {o} WITH CHECK(true)')
    definitions=[
    ('knowledge_session_valid()',True,f"""CREATE FUNCTION {q}.knowledge_session_valid() RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF {q}.current_actor_kind()='staff' THEN RETURN {q}.case_staff_session_valid();END IF;
      RETURN {q}.conversation_session_valid();END $$"""),
    ('knowledge_scope(uuid,uuid)',True,f"""CREATE FUNCTION {q}.knowledge_scope(mid uuid,org uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE m record;BEGIN
      IF NOT {q}.case_staff_session_valid() THEN RETURN NULL;END IF;
      SELECT sm.id,sm.organization_id INTO m FROM {q}.staff_memberships sm JOIN {q}.membership_roles mr ON(mr.tenant_id,mr.membership_id)=(sm.tenant_id,sm.id)
        JOIN {q}.organizations oo ON(oo.tenant_id,oo.id)=(sm.tenant_id,sm.organization_id)
        WHERE sm.tenant_id={q}.current_tenant_id() AND sm.principal_id={q}.current_principal_id() AND sm.active AND oo.active AND mr.role='admin'
          AND (mid IS NULL OR sm.id=mid) AND(org IS NULL OR sm.organization_id=org) ORDER BY sm.id LIMIT 1 FOR SHARE OF sm,mr,oo;
      IF NOT FOUND THEN RETURN NULL;END IF;RETURN jsonb_build_object('membership_id',m.id,'organization_id',m.organization_id);END $$"""),
    ('knowledge_document_admin(uuid)',True,f"""CREATE FUNCTION {q}.knowledge_document_admin(did uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE org uuid;BEGIN SELECT organization_id INTO org FROM {q}.knowledge_documents WHERE tenant_id={q}.current_tenant_id() AND id=did;
      RETURN FOUND AND {q}.knowledge_scope(NULL,org) IS NOT NULL;END $$"""),
    ('knowledge_can_read(text,uuid,text[])',True,f"""CREATE FUNCTION {q}.knowledge_can_read(vis text,org uuid,roles text[]) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF NOT {q}.knowledge_session_valid() THEN RETURN false;END IF;IF vis='public' THEN RETURN true;END IF;
      IF {q}.current_actor_kind()<>'staff' THEN RETURN false;END IF;
      PERFORM sm.id FROM {q}.staff_memberships sm JOIN {q}.membership_roles mr ON(mr.tenant_id,mr.membership_id)=(sm.tenant_id,sm.id)
        JOIN {q}.organizations oo ON(oo.tenant_id,oo.id)=(sm.tenant_id,sm.organization_id)
        WHERE sm.tenant_id={q}.current_tenant_id() AND sm.principal_id={q}.current_principal_id() AND sm.organization_id=org AND sm.active AND oo.active AND mr.role=ANY(roles) FOR SHARE OF sm,mr,oo;
      RETURN FOUND;END $$"""),
    ('knowledge_document_guard()',False,f"""CREATE FUNCTION {q}.knowledge_document_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE mid uuid;BEGIN
      IF TG_OP='UPDATE' THEN
        IF current_user<>{owner_literal} OR NEW.revision<>OLD.revision+1 OR (to_jsonb(NEW)-ARRAY['revision','updated_at']) IS DISTINCT FROM(to_jsonb(OLD)-ARRAY['revision','updated_at']) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Immutable knowledge document';END IF;
        RETURN NEW;
      END IF;
      mid:=NULLIF(current_setting('app.knowledge_membership_id',true),'')::uuid;
      IF NEW.tenant_id IS DISTINCT FROM {q}.current_tenant_id() OR mid IS NULL OR {q}.knowledge_scope(mid,NEW.organization_id) IS NULL OR NEW.revision<>1 THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Knowledge access denied';END IF;
      NEW.created_by:={q}.current_principal_id();RETURN NEW;END $$"""),
    ('knowledge_version_guard()',False,f"""CREATE FUNCTION {q}.knowledge_version_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE org uuid;mid uuid;lo timestamptz;hi timestamptz;BEGIN
      SELECT organization_id INTO org FROM {q}.knowledge_documents WHERE tenant_id=NEW.tenant_id AND id=NEW.document_id FOR UPDATE;
      mid:=NULLIF(current_setting('app.knowledge_membership_id',true),'')::uuid;
      IF org IS NULL OR NEW.tenant_id IS DISTINCT FROM {q}.current_tenant_id() OR mid IS NULL OR {q}.knowledge_scope(mid,org) IS NULL THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Knowledge access denied';END IF;
      IF NEW.visibility='staff' AND NEW.organization_id IS DISTINCT FROM org THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid knowledge organization';END IF;
      IF NEW.allowed_roles IS DISTINCT FROM COALESCE((SELECT array_agg(DISTINCT x ORDER BY x) FROM unnest(NEW.allowed_roles)x),ARRAY[]::text[]) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid knowledge roles';END IF;
      IF jsonb_typeof(NEW.applicability)<>'object' OR NEW.applicability-ARRAY['terminal','booking_created_from','booking_created_to']<>'{{}}'::jsonb OR NOT NEW.applicability ?& ARRAY['terminal','booking_created_from','booking_created_to'] THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid applicability';END IF;
      IF NEW.applicability->>'terminal' IS NOT NULL AND NOT(NEW.applicability->>'terminal' ~ '^[A-Za-z0-9_-]{{1,32}}$') THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid terminal';END IF;
      lo:=(NEW.applicability->>'booking_created_from')::timestamptz;hi:=(NEW.applicability->>'booking_created_to')::timestamptz;
      IF hi IS NOT NULL AND lo IS NOT NULL AND hi<=lo THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid applicability interval';END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.status<>'draft' OR NEW.revision<>1 OR NEW.version<>(SELECT COALESCE(max(v.version),0)+1 FROM {q}.knowledge_versions v WHERE v.tenant_id=NEW.tenant_id AND v.document_id=NEW.document_id) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid knowledge version';END IF;
        NEW.created_by:={q}.current_principal_id();
      ELSE
        IF (NEW.tenant_id,NEW.id,NEW.document_id,NEW.version,NEW.created_by,NEW.created_at) IS DISTINCT FROM(OLD.tenant_id,OLD.id,OLD.document_id,OLD.version,OLD.created_by,OLD.created_at) OR NEW.revision<>OLD.revision+1 THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid knowledge revision';END IF;
        IF OLD.status<>'draft' AND NOT(OLD.status='published' AND NEW.status='withdrawn' AND(to_jsonb(NEW)-ARRAY['status','revision','updated_at','withdrawn_at','withdrawn_by','withdrawal_reason'])=(to_jsonb(OLD)-ARRAY['status','revision','updated_at','withdrawn_at','withdrawn_by','withdrawal_reason'])) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Immutable published knowledge';END IF;
        IF OLD.status='draft' AND NEW.status NOT IN('draft','published') THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid knowledge transition';END IF;
      END IF;
      IF NEW.supersedes_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM {q}.knowledge_versions v WHERE v.tenant_id=NEW.tenant_id AND v.document_id=NEW.document_id AND v.id=NEW.supersedes_version_id AND v.version<NEW.version) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid predecessor';END IF;
      NEW.content_hash:=encode(sha256(convert_to(NEW.body,'UTF8')),'hex');
      NEW.basis_hash:=encode(sha256(convert_to(jsonb_build_object('id',NEW.id,'document_id',NEW.document_id,'content_hash',NEW.content_hash,'source_uri',NEW.source_uri,'service_type',NEW.service_type,'language',NEW.language,'visibility',NEW.visibility,'organization_id',NEW.organization_id,'allowed_roles',NEW.allowed_roles,'valid_from',extract(epoch FROM NEW.valid_from),'valid_to',extract(epoch FROM NEW.valid_to),'applicability',NEW.applicability,'policy_reference',NEW.policy_reference,'supersedes_version_id',NEW.supersedes_version_id)::text,'UTF8')),'hex');
      IF NEW.status='published' AND(TG_OP='INSERT' OR OLD.status='draft') THEN NEW.published_at:=clock_timestamp();NEW.published_by:={q}.current_principal_id();END IF;
      IF NEW.status='withdrawn' THEN NEW.withdrawn_at:=clock_timestamp();NEW.withdrawn_by:={q}.current_principal_id();END IF;
      IF NEW.status='published' AND (NEW.index_valid_until IS NULL OR NEW.index_valid_until<=clock_timestamp()) THEN RAISE EXCEPTION USING ERRCODE='Z0024',MESSAGE='Knowledge index proof expired';END IF;
      NEW.updated_at:=clock_timestamp();RETURN NEW;END $$"""),
    ('knowledge_log()',False,f"""CREATE FUNCTION {q}.knowledge_log() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE did uuid;dr integer;vid uuid;vr integer;kind text;basis text;gen uuid;why text;BEGIN
      IF TG_TABLE_NAME='knowledge_documents' THEN did:=NEW.id;dr:=NEW.revision;kind:='document_created';
      ELSE did:=NEW.document_id;vid:=NEW.id;vr:=NEW.revision;basis:=NEW.basis_hash;gen:=NEW.index_generation;why:=NEW.withdrawal_reason;
        kind:=CASE WHEN TG_OP='INSERT' THEN 'version_created' WHEN OLD.status=NEW.status THEN 'draft_updated' ELSE NEW.status END;
        UPDATE {q}.knowledge_documents SET revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=NEW.tenant_id AND id=did RETURNING revision INTO dr;
      END IF;
      INSERT INTO {q}.knowledge_audit(tenant_id,document_id,version_id,document_revision,version_revision,actor_principal_id,actor_membership_id,event_type,basis_hash,index_generation,reason)
        VALUES(NEW.tenant_id,did,vid,dr,vr,{q}.current_principal_id(),NULLIF(current_setting('app.knowledge_membership_id',true),'')::uuid,kind,basis,gen,why);RETURN NULL;END $$"""),
    ]
    for signature,exposed,definition in definitions:
        op.execute(definition);op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC');op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {o}')
        if exposed:op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {r}')
    # Invoker guards require runtime execution only as triggers; no table DELETE grants.
    op.execute(f'CREATE TRIGGER knowledge_document_guard BEFORE INSERT OR UPDATE ON {q}.knowledge_documents FOR EACH ROW EXECUTE FUNCTION {q}.knowledge_document_guard()')
    op.execute(f'CREATE TRIGGER knowledge_version_guard BEFORE INSERT OR UPDATE ON {q}.knowledge_versions FOR EACH ROW EXECUTE FUNCTION {q}.knowledge_version_guard()')
    op.execute(f'CREATE TRIGGER knowledge_document_audit AFTER INSERT ON {q}.knowledge_documents FOR EACH ROW EXECUTE FUNCTION {q}.knowledge_log()')
    op.execute(f'CREATE TRIGGER knowledge_version_audit AFTER INSERT OR UPDATE ON {q}.knowledge_versions FOR EACH ROW EXECUTE FUNCTION {q}.knowledge_log()')
    op.execute(f'GRANT INSERT(tenant_id,organization_id,document_key,title,created_by) ON {q}.knowledge_documents TO {r}')
    op.execute(f'GRANT UPDATE(revision) ON {q}.knowledge_documents TO {r}')
    op.execute(f'GRANT INSERT(tenant_id,document_id,version,body,source_uri,service_type,language,visibility,organization_id,allowed_roles,valid_from,valid_to,applicability,policy_reference,supersedes_version_id,created_by) ON {q}.knowledge_versions TO {r}')
    op.execute(f'GRANT UPDATE(body,source_uri,service_type,language,visibility,organization_id,allowed_roles,valid_from,valid_to,applicability,policy_reference,supersedes_version_id,revision,status,index_generation,index_basis_hash,index_valid_until,withdrawal_reason) ON {q}.knowledge_versions TO {r}')
    tenant=f'tenant_id={q}.current_tenant_id()'
    op.execute(f"CREATE POLICY knowledge_documents_read ON {q}.knowledge_documents FOR SELECT TO {r} USING({tenant} AND({q}.knowledge_scope(NULL,organization_id) IS NOT NULL OR EXISTS(SELECT 1 FROM {q}.knowledge_versions v WHERE v.tenant_id=knowledge_documents.tenant_id AND v.document_id=knowledge_documents.id AND v.status='published' AND {q}.knowledge_can_read(v.visibility,v.organization_id,v.allowed_roles))))")
    op.execute(f'CREATE POLICY knowledge_documents_insert ON {q}.knowledge_documents FOR INSERT TO {r} WITH CHECK({tenant} AND {q}.knowledge_scope(NULL,organization_id) IS NOT NULL)')
    op.execute(f'CREATE POLICY knowledge_documents_lock ON {q}.knowledge_documents FOR UPDATE TO {r} USING({tenant} AND {q}.knowledge_scope(NULL,organization_id) IS NOT NULL) WITH CHECK(false)')
    op.execute(f"CREATE POLICY knowledge_versions_read ON {q}.knowledge_versions FOR SELECT TO {r} USING({tenant} AND({q}.knowledge_document_admin(document_id) OR(status='published' AND {q}.knowledge_can_read(visibility,organization_id,allowed_roles))))")
    for operation in ('INSERT','UPDATE'):
        clause=f'WITH CHECK({tenant} AND {q}.knowledge_document_admin(document_id))' if operation=='INSERT' else f'USING({tenant} AND {q}.knowledge_document_admin(document_id)) WITH CHECK({tenant} AND {q}.knowledge_document_admin(document_id))'
        op.execute(f'CREATE POLICY knowledge_versions_{operation.lower()} ON {q}.knowledge_versions FOR {operation} TO {r} {clause}')
    op.execute(f'CREATE POLICY knowledge_audit_read ON {q}.knowledge_audit FOR SELECT TO {r} USING({tenant} AND {q}.knowledge_document_admin(document_id))')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {o}')

def downgrade():raise RuntimeError('Knowledge recovery requires a reviewed forward migration.')
