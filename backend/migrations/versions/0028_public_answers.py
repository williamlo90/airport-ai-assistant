"""Commit-time public source validation and immutable message citation snapshots."""
from alembic import context,op
import hashlib
revision='0028_public_answers'
down_revision='0027_agent_delivery'
branch_labels=depends_on=None


def upgrade():
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,r,o=(quote(context.config.attributes[k]) for k in ('schema','runtime_role','auth_role'))
    lock_name='knowledge_lock_'+hashlib.sha256(context.config.attributes['schema'].encode()).hexdigest()[:24]
    lock=quote(lock_name)
    op.execute(f'CREATE ROLE {lock} NOLOGIN NOINHERIT NOBYPASSRLS')
    op.execute(f'GRANT {lock} TO CURRENT_USER')
    op.execute(f'GRANT USAGE,CREATE ON SCHEMA {q} TO {lock}')
    version=int(op.get_bind().exec_driver_sql('SHOW server_version_num').scalar_one())
    # MAINTAIN is available from PG17. Older PG needs table UPDATE for LOCK,
    # but this isolated NOLOGIN role has no RLS row policies or callable DML port.
    privilege='MAINTAIN' if version>=170000 else 'UPDATE'
    op.execute(f'GRANT {privilege} ON {q}.knowledge_documents,{q}.knowledge_versions TO {lock}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.current_actor_kind(),{q}.conversation_session_valid() TO {lock}')
    op.execute(f"ALTER TABLE {q}.agent_runs DROP CONSTRAINT agent_runs_mode_check,ADD CONSTRAINT agent_runs_mode_check CHECK(mode IN('assist','draft','answer'))")
    op.execute(f'''ALTER TABLE {q}.messages ADD COLUMN knowledge_evidence jsonb,
      ADD CONSTRAINT knowledge_evidence_shape CHECK(knowledge_evidence IS NULL OR(author_type='ai' AND jsonb_typeof(knowledge_evidence)='object' AND octet_length(knowledge_evidence::text)<=16000))''')
    op.execute(f'GRANT INSERT(knowledge_evidence) ON {q}.messages TO {r}')
    op.execute(f'''CREATE FUNCTION {q}.knowledge_answer_lock() RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF {q}.current_actor_kind() NOT IN('anonymous','customer') OR NOT {q}.conversation_session_valid()
      THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Knowledge answer access denied';END IF;
      -- NOWAIT prevents a lock-order cycle with admin document/version writes.
      -- Published chunks cannot be modified or inserted through runtime grants.
      LOCK TABLE {q}.knowledge_documents,{q}.knowledge_versions IN SHARE MODE NOWAIT;
    END $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.knowledge_answer_lock() FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.knowledge_answer_lock() OWNER TO {lock}')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {lock}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.knowledge_answer_lock() TO {r}')
    op.execute(f'''CREATE FUNCTION {q}.guard_knowledge_answer() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE evidence jsonb;item jsonb;citation_count integer;i integer:=0;matching integer;token text;ambiguous boolean;rendered text;source_document uuid;
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF NEW.knowledge_evidence IS DISTINCT FROM OLD.knowledge_evidence THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='Immutable message citations';END IF;
          RETURN NEW;
        END IF;
        IF NEW.knowledge_evidence IS NULL THEN RETURN NEW;END IF;
        evidence:=NEW.knowledge_evidence;
        IF NEW.author_type<>'ai' OR evidence-ARRAY['service_type','document_key','scope_token','issued_at','expires_at','citations']<>'{{}}'::jsonb
          OR NOT(evidence ?& ARRAY['service_type','document_key','scope_token','issued_at','expires_at','citations'])
          OR jsonb_typeof(evidence->'service_type') IS DISTINCT FROM 'string' OR jsonb_typeof(evidence->'scope_token') IS DISTINCT FROM 'string'
          OR jsonb_typeof(evidence->'issued_at') IS DISTINCT FROM 'string' OR jsonb_typeof(evidence->'expires_at') IS DISTINCT FROM 'string'
          OR jsonb_typeof(evidence->'document_key') NOT IN('string','null')
          OR evidence->>'service_type' NOT IN('general','parking','lounge') OR jsonb_typeof(evidence->'citations')<>'array'
          OR (evidence->>'issued_at')::timestamptz>clock_timestamp() OR(evidence->>'expires_at')::timestamptz<=clock_timestamp()
          OR(evidence->>'expires_at')::timestamptz>(evidence->>'issued_at')::timestamptz+interval '300 seconds'
        THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Invalid current knowledge evidence';END IF;
        PERFORM {q}.knowledge_answer_lock();
        citation_count:=jsonb_array_length(evidence->'citations');
        IF citation_count NOT BETWEEN 1 AND 3 OR(SELECT count(DISTINCT (x->>'version_id',x->>'generation',x->>'ordinal')) FROM jsonb_array_elements(evidence->'citations')x)<>citation_count
        THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Invalid citation set';END IF;
        WITH versions AS MATERIALIZED(
          SELECT v.id,v.index_generation,v.basis_hash,v.document_id,d.revision
          FROM {q}.knowledge_versions v JOIN {q}.knowledge_documents d ON(d.tenant_id,d.id)=(v.tenant_id,v.document_id)
          WHERE v.tenant_id=NEW.tenant_id AND v.status='published' AND v.visibility='public' AND v.policy_reference IS NULL
            AND v.service_type=evidence->>'service_type' AND v.language=NEW.language
            AND(evidence->>'document_key' IS NULL OR d.document_key=evidence->>'document_key')
            AND v.valid_from<=clock_timestamp() AND(v.valid_to IS NULL OR clock_timestamp()<v.valid_to)
            AND v.applicability->>'terminal' IS NULL AND v.applicability->>'booking_created_from' IS NULL AND v.applicability->>'booking_created_to' IS NULL)
        SELECT encode(sha256(convert_to(COALESCE(string_agg(id::text||':'||index_generation::text||':'||basis_hash||':'||revision::text,',' ORDER BY id),''),'UTF8')),'hex'),
          EXISTS(SELECT 1 FROM versions GROUP BY document_id HAVING count(*)>1) INTO token,ambiguous FROM versions;
        IF ambiguous OR token IS DISTINCT FROM evidence->>'scope_token' THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Knowledge sources changed';END IF;
        rendered:=CASE WHEN NEW.language='id' THEN 'Informasi bandara yang dipublikasikan:' ELSE 'Published airport information:' END||E'\n\n';
        FOR item IN SELECT value FROM jsonb_array_elements(evidence->'citations') LOOP
          i:=i+1;
          IF item-ARRAY['version_id','generation','ordinal','basis_hash','document_id','document_revision','quote','source_uri']<>'{{}}'::jsonb
            OR NOT(item ?& ARRAY['version_id','generation','ordinal','basis_hash','document_id','document_revision','quote','source_uri'])
            OR EXISTS(SELECT 1 FROM unnest(ARRAY['version_id','generation','basis_hash','document_id','quote','source_uri'])k WHERE jsonb_typeof(item->k) IS DISTINCT FROM 'string')
            OR jsonb_typeof(item->'ordinal') IS DISTINCT FROM 'number' OR jsonb_typeof(item->'document_revision') IS DISTINCT FROM 'number'
          THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Invalid citation';END IF;
          IF source_document IS NOT NULL AND source_document<>(item->>'document_id')::uuid THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Ambiguous answer authority';END IF;
          source_document:=(item->>'document_id')::uuid;
          SELECT count(*) INTO matching FROM {q}.knowledge_versions v JOIN {q}.knowledge_documents d ON(d.tenant_id,d.id)=(v.tenant_id,v.document_id)
            JOIN {q}.knowledge_chunks ch ON(ch.tenant_id,ch.version_id,ch.generation,ch.basis_hash)=(v.tenant_id,v.id,v.index_generation,v.basis_hash)
            WHERE v.tenant_id=NEW.tenant_id AND v.id=(item->>'version_id')::uuid AND v.document_id=source_document
              AND v.index_generation=(item->>'generation')::uuid AND v.basis_hash=item->>'basis_hash' AND d.revision=(item->>'document_revision')::integer
              AND ch.ordinal=(item->>'ordinal')::integer AND ch.body=item->>'quote' AND v.source_uri=item->>'source_uri'
              AND v.status='published' AND v.visibility='public' AND v.policy_reference IS NULL
              AND v.service_type=evidence->>'service_type' AND v.language=NEW.language
              AND(evidence->>'document_key' IS NULL OR d.document_key=evidence->>'document_key')
              AND v.valid_from<=clock_timestamp() AND(v.valid_to IS NULL OR clock_timestamp()<v.valid_to)
              AND v.applicability->>'terminal' IS NULL AND v.applicability->>'booking_created_from' IS NULL AND v.applicability->>'booking_created_to' IS NULL;
          IF matching<>1 THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Citation no longer current';END IF;
          IF i>1 THEN rendered:=rendered||E'\n\n';END IF;
          rendered:=rendered||(item->>'quote')||E'\n['||i::text||'] '||(item->>'source_uri');
        END LOOP;
        IF rendered IS DISTINCT FROM NEW.body OR(evidence->>'expires_at')::timestamptz<=clock_timestamp()
        THEN RAISE EXCEPTION USING ERRCODE='Z0028',MESSAGE='Answer text does not match current evidence';END IF;
        RETURN NEW;
      END $$''')
    op.execute(f'REVOKE ALL ON FUNCTION {q}.guard_knowledge_answer() FROM PUBLIC')
    op.execute(f'CREATE TRIGGER knowledge_answer_guard BEFORE INSERT OR UPDATE ON {q}.messages FOR EACH ROW EXECUTE FUNCTION {q}.guard_knowledge_answer()')


def downgrade():raise RuntimeError('Published citation history requires a reviewed forward migration.')
