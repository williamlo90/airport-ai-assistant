"""Immutable version-bound chunks, pgvector and publication readiness enforcement."""
from alembic import context, op

revision = '0025_knowledge_vectors'
down_revision = '0024_knowledge'
branch_labels = depends_on = None


def upgrade():
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, r = (quote(context.config.attributes[k]) for k in ('schema', 'runtime_role'))
    op.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
    op.execute(f'''CREATE TABLE {q}.knowledge_chunks(
        tenant_id uuid NOT NULL, version_id uuid NOT NULL, generation uuid NOT NULL,
        basis_hash text NOT NULL, ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 62),
        start_offset integer NOT NULL CHECK(start_offset=ordinal*320),
        body text NOT NULL CHECK(length(body) BETWEEN 1 AND 384),
        model text NOT NULL DEFAULT 'text-embedding-3-small' CHECK(model='text-embedding-3-small'),
        chunker text NOT NULL DEFAULT 'chars384-overlap64-v1' CHECK(chunker='chars384-overlap64-v1'),
        embedding public.vector(1536) NOT NULL CHECK(public.vector_norm(embedding) BETWEEN 0.999 AND 1.001),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant_id,version_id,generation,ordinal),
        FOREIGN KEY(tenant_id,version_id) REFERENCES {q}.knowledge_versions(tenant_id,id))''')
    op.execute(f'ALTER TABLE {q}.knowledge_chunks ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {q}.knowledge_chunks FORCE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON {q}.knowledge_chunks FROM PUBLIC')
    op.execute(f'GRANT SELECT,INSERT ON {q}.knowledge_chunks TO {r}')
    # Admin management access must not become answer eligibility. Published chunks
    # are visible only for the current version basis/generation and read ACL.
    op.execute(f'''CREATE POLICY knowledge_chunks_read ON {q}.knowledge_chunks FOR SELECT TO {r}
      USING(tenant_id={q}.current_tenant_id() AND EXISTS(SELECT 1 FROM {q}.knowledge_versions v
        WHERE v.tenant_id=knowledge_chunks.tenant_id AND v.id=knowledge_chunks.version_id
        AND v.basis_hash=knowledge_chunks.basis_hash AND
        ((v.status='draft' AND {q}.knowledge_document_admin(v.document_id)) OR
         (v.status='published' AND v.index_generation=knowledge_chunks.generation
          AND {q}.knowledge_can_read(v.visibility,v.organization_id,v.allowed_roles)))))''')
    op.execute(f'''CREATE POLICY knowledge_chunks_insert ON {q}.knowledge_chunks FOR INSERT TO {r}
      WITH CHECK(tenant_id={q}.current_tenant_id() AND EXISTS(SELECT 1 FROM {q}.knowledge_versions v
        WHERE v.tenant_id=knowledge_chunks.tenant_id AND v.id=knowledge_chunks.version_id
        AND v.status='draft' AND v.basis_hash=knowledge_chunks.basis_hash
        AND {q}.knowledge_document_admin(v.document_id)))''')
    op.execute(f'''CREATE FUNCTION {q}.knowledge_chunk_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$ DECLARE v record; BEGIN
        SELECT * INTO v FROM {q}.knowledge_versions WHERE tenant_id=NEW.tenant_id AND id=NEW.version_id;
        IF NOT FOUND OR NEW.tenant_id IS DISTINCT FROM {q}.current_tenant_id()
          OR v.status<>'draft' OR v.basis_hash<>NEW.basis_hash
          OR {q}.knowledge_scope(NULLIF(current_setting('app.knowledge_membership_id',true),'')::uuid,
             (SELECT organization_id FROM {q}.knowledge_documents WHERE tenant_id=v.tenant_id AND id=v.document_id)) IS NULL
          OR NEW.ordinal>=greatest(1,ceil((length(v.body)-64)::numeric/320)::integer)
          OR NEW.body IS DISTINCT FROM substring(v.body FROM NEW.start_offset+1 FOR 384)
        THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Invalid knowledge chunk'; END IF;
        RETURN NEW; END $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.knowledge_chunk_guard() FROM PUBLIC')
    op.execute(f'''CREATE TRIGGER knowledge_chunk_guard BEFORE INSERT ON {q}.knowledge_chunks
        FOR EACH ROW EXECUTE FUNCTION {q}.knowledge_chunk_guard()''')
    op.execute(f'''CREATE FUNCTION {q}.knowledge_index_complete(vid uuid,basis text,gen uuid) RETURNS boolean
      LANGUAGE sql SET search_path=pg_catalog AS $$
      SELECT EXISTS(SELECT 1 FROM {q}.knowledge_versions v WHERE v.tenant_id={q}.current_tenant_id()
        AND v.id=vid AND v.basis_hash=basis AND
        (SELECT count(*) FROM {q}.knowledge_chunks c WHERE c.tenant_id=v.tenant_id
          AND c.version_id=v.id AND c.basis_hash=basis AND c.generation=gen
          AND c.model='text-embedding-3-small' AND c.chunker='chars384-overlap64-v1'
          AND c.body=substring(v.body FROM c.start_offset+1 FOR 384))
          =greatest(1,ceil((length(v.body)-64)::numeric/320)::integer)) $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.knowledge_index_complete(uuid,text,uuid) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.knowledge_index_complete(uuid,text,uuid) TO {r}')
    op.execute(f'''CREATE FUNCTION {q}.knowledge_publication_index_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NEW.status='published' AND OLD.status='draft' AND
          NOT {q}.knowledge_index_complete(NEW.id,NEW.basis_hash,NEW.index_generation)
        THEN RAISE EXCEPTION USING ERRCODE='Z0024',MESSAGE='Knowledge index incomplete'; END IF;
        RETURN NEW; END $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.knowledge_publication_index_guard() FROM PUBLIC')
    # The lifecycle guard recomputes basis first; Postgres orders triggers by name.
    op.execute(f'''CREATE TRIGGER knowledge_z_publication_index_guard BEFORE UPDATE ON {q}.knowledge_versions
        FOR EACH ROW EXECUTE FUNCTION {q}.knowledge_publication_index_guard()''')


def downgrade():
    raise RuntimeError('Knowledge recovery requires a reviewed forward migration.')
