"""Private classification schema foundation; existing empty-only gates stay ON.

This revision installs storage contracts, not private graph/worker activation.
The next graph/capability revisions must replace each old guard deliberately.
"""
from alembic import context, op
import hashlib

revision = '0029_private_source_schema'
down_revision = '0028_public_answers'
branch_labels = depends_on = None

NEW_LABEL_TABLES = ('agent_runs', 'ai_generations', 'staff_translation_drafts',
                    'case_history', 'action_consents', 'audit_events', 'idempotency_records',
                    'email_reply_state', 'email_reply_challenges', 'conversation_events')
OLD_LABEL_TABLES = ('conversations', 'messages', 'action_drafts', 'cases', 'attachments',
                    'outbox_jobs', 'email_messages')


def upgrade():
    attrs = context.config.attributes
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q = quote(attrs['schema'])
    suffix = hashlib.sha256(attrs['schema'].encode()).hexdigest()[:24]
    mail = 'airport_assistant_mail_owner' if attrs['schema'] == 'app' else 'mail_owner_' + suffix
    reply = 'airport_assistant_reply_owner' if attrs['schema'] == 'app' else 'reply_owner_' + suffix
    roles = ','.join(quote(value) for value in
        [*(attrs[key] for key in ('runtime_role', 'policy_role', 'auth_role', 'worker_role', 'job_owner_role')), mail, reply])
    _labels(q)
    _evidence(q)
    for signature in ('source_labels_union(jsonb[])', 'source_labels_valid(jsonb)',
                      'source_labels_contains(jsonb,jsonb)', 'source_evidence_requirements(jsonb)',
                      'source_evidence_valid(jsonb,jsonb)'):
        op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {roles}')
    for table in NEW_LABEL_TABLES:
        op.execute(f'''ALTER TABLE {q}.{table} ADD COLUMN resource_security_labels jsonb NOT NULL DEFAULT '[]',
          ADD CONSTRAINT private_labels_hold CHECK(resource_security_labels='[]'::jsonb)''')
    for table in (*OLD_LABEL_TABLES, *NEW_LABEL_TABLES):
        op.execute(f'''ALTER TABLE {q}.{table} ADD CONSTRAINT private_labels_canonical
          CHECK({q}.source_labels_valid(resource_security_labels))''')
        op.execute(f'''CREATE TRIGGER private_labels_monotone BEFORE UPDATE OF resource_security_labels ON {q}.{table}
          FOR EACH ROW EXECUTE FUNCTION {q}.guard_source_labels_monotone()''')
    op.execute(f'''ALTER TABLE {q}.action_drafts ADD COLUMN source_observations jsonb NOT NULL DEFAULT '[]',
      ADD CONSTRAINT private_evidence_hold CHECK(source_observations='[]'::jsonb)''')
    for table in ('action_drafts', 'cases'):
        op.execute(f'''ALTER TABLE {q}.{table} ADD CONSTRAINT private_evidence_classification
          CHECK({q}.source_evidence_valid(source_observations,resource_security_labels))''')
        op.execute(f'''CREATE TRIGGER private_evidence_immutable BEFORE UPDATE OF source_observations ON {q}.{table}
          FOR EACH ROW EXECUTE FUNCTION {q}.guard_source_evidence_immutable()''')
    op.execute(f'''ALTER TABLE {q}.staff_translation_drafts ADD COLUMN source_context_hash text,
      ADD CONSTRAINT private_translation_context CHECK(source_context_hash IS NULL OR source_context_hash~'^[a-f0-9]{{64}}$')''')
    _status(q, attrs['schema'])
    op.execute(f'REVOKE ALL ON FUNCTION {q}.private_source_schema_status() FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.private_source_schema_status() TO {roles}')


def _labels(q):
    op.execute(f'''CREATE FUNCTION {q}.source_labels_union(groups jsonb[]) RETURNS jsonb
      LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
      DECLARE g jsonb;x jsonb;s jsonb;all_items jsonb:='[]';result jsonb;allowed jsonb;BEGIN
        IF groups IS NULL OR cardinality(groups) NOT BETWEEN 1 AND 64 OR array_ndims(groups)<>1
        THEN RAISE EXCEPTION 'Invalid source classification' USING ERRCODE='23514';END IF;
        FOREACH g IN ARRAY groups LOOP
          IF g IS NULL OR jsonb_typeof(g)<>'array' OR jsonb_array_length(g)>64 OR octet_length(g::text)>65536
          THEN RAISE EXCEPTION 'Invalid source classification' USING ERRCODE='23514';END IF;
          FOR x IN SELECT value FROM jsonb_array_elements(g) LOOP
            IF jsonb_typeof(x)<>'object' OR NOT(x ?& ARRAY['source','resource_type','reference','required_source_scopes'])
              OR x-ARRAY['source','resource_type','reference','required_source_scopes']<>'{{}}'::jsonb
              OR x->>'source' IS DISTINCT FROM 'reference' OR x->>'resource_type' IS NULL OR x->>'resource_type' NOT IN('booking','transaction')
              OR jsonb_typeof(x->'reference') IS DISTINCT FROM 'string' OR(x->>'reference')!~'^[A-Za-z0-9_-]{{1,80}}$'
              OR jsonb_typeof(x->'required_source_scopes') IS DISTINCT FROM 'array'
            THEN RAISE EXCEPTION 'Invalid source classification' USING ERRCODE='23514';END IF;
            allowed:=CASE WHEN x->>'resource_type'='booking' THEN '["booking:read"]'::jsonb
                          ELSE '["payment_details:read","payment_status:read"]'::jsonb END;
            IF jsonb_array_length(x->'required_source_scopes') NOT BETWEEN 1 AND 2
              OR(SELECT count(DISTINCT value) FROM jsonb_array_elements(x->'required_source_scopes'))<>jsonb_array_length(x->'required_source_scopes')
            THEN RAISE EXCEPTION 'Invalid source scopes' USING ERRCODE='23514';END IF;
            FOR s IN SELECT value FROM jsonb_array_elements(x->'required_source_scopes') LOOP
              IF jsonb_typeof(s)<>'string' OR NOT(allowed @> jsonb_build_array(s))
              THEN RAISE EXCEPTION 'Invalid source scopes' USING ERRCODE='23514';END IF;
            END LOOP;
          END LOOP;
          all_items:=all_items||g;
        END LOOP;
        WITH scopes AS (
          SELECT DISTINCT label_row.value->>'resource_type' kind,label_row.value->>'reference' ref,scope_row.value#>>'{{}}' scope
          FROM jsonb_array_elements(all_items)label_row(value)
          CROSS JOIN LATERAL jsonb_array_elements(label_row.value->'required_source_scopes')scope_row(value)),
        resources AS (SELECT kind,ref,jsonb_agg(scope ORDER BY scope COLLATE "C") scopes FROM scopes GROUP BY kind,ref)
        SELECT COALESCE(jsonb_agg(jsonb_build_object('source','reference','resource_type',kind,'reference',ref,
          'required_source_scopes',scopes) ORDER BY kind COLLATE "C",ref COLLATE "C"),'[]'::jsonb) INTO result FROM resources;
        IF jsonb_array_length(result)>64 THEN RAISE EXCEPTION 'Too many source requirements' USING ERRCODE='23514';END IF;
        RETURN result;
      END $$''')
    op.execute(f'''CREATE FUNCTION {q}.source_labels_valid(value jsonb) RETURNS boolean
      LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$ BEGIN
        RETURN {q}.source_labels_union(ARRAY[value])=value;
      EXCEPTION WHEN check_violation OR invalid_parameter_value THEN RETURN false;END $$''')
    op.execute(f'''CREATE FUNCTION {q}.source_labels_contains(whole jsonb,part jsonb) RETURNS boolean
      LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$ BEGIN
        IF NOT {q}.source_labels_valid(whole) OR NOT {q}.source_labels_valid(part) THEN RETURN false;END IF;
        RETURN {q}.source_labels_union(ARRAY[whole,part])=whole;
      END $$''')
    op.execute(f'''CREATE FUNCTION {q}.guard_source_labels_monotone() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NOT {q}.source_labels_contains(NEW.resource_security_labels,OLD.resource_security_labels)
        THEN RAISE EXCEPTION 'Source requirements cannot be removed' USING ERRCODE='23514';END IF;
        RETURN NEW;END $$''')
    op.execute(f'''CREATE FUNCTION {q}.guard_source_evidence_immutable() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NEW.source_observations IS DISTINCT FROM OLD.source_observations
        THEN RAISE EXCEPTION 'Reviewed source observations are immutable' USING ERRCODE='23514';END IF;
        RETURN NEW;END $$''')
    for function in ('guard_source_labels_monotone', 'guard_source_evidence_immutable'):
        op.execute(f'REVOKE ALL ON FUNCTION {q}.{function}() FROM PUBLIC')


def _evidence(q):
    # Full DTO semantics remain in SourceEvidence. SQL independently derives
    # the minimum requirements from EVERY stored observation/bound booking.
    op.execute(f'''CREATE FUNCTION {q}.source_evidence_requirements(records jsonb) RETURNS jsonb
      LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
      DECLARE item jsonb;d jsonb;b jsonb;kind text;requirements jsonb:='[]';scopes jsonb;BEGIN
        IF records IS NULL OR jsonb_typeof(records)<>'array' OR jsonb_array_length(records)>8 OR octet_length(records::text)>65536
        THEN RAISE EXCEPTION 'Invalid source observations' USING ERRCODE='23514';END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(records) LOOP
          kind:=item->>'kind';d:=item->'data';
          IF jsonb_typeof(item)<>'object' OR(item->'schema_version')::text IS DISTINCT FROM '1'
            OR kind IS NULL OR kind NOT IN('booking','transaction','policy') OR jsonb_typeof(d) IS DISTINCT FROM 'object'
            OR NOT(item ?& ARRAY['schema_version','kind','data'])
            OR(item-CASE WHEN kind='policy' THEN ARRAY['schema_version','kind','data','booking'] ELSE ARRAY['schema_version','kind','data'] END)<>'{{}}'::jsonb
            OR jsonb_typeof(d->'reference') IS DISTINCT FROM 'string' OR(d->>'reference')!~'^[A-Za-z0-9_-]{{1,80}}$'
          THEN RAISE EXCEPTION 'Invalid source observation' USING ERRCODE='23514';END IF;
          IF kind='policy' THEN
            b:=item->'booking';
            IF jsonb_typeof(b) IS DISTINCT FROM 'object' OR jsonb_typeof(b->'reference') IS DISTINCT FROM 'string'
              OR(b->>'reference')!~'^[A-Za-z0-9_-]{{1,80}}$' OR b->>'policy_reference' IS DISTINCT FROM d->>'reference'
              OR b->>'service_type' IS NULL OR b->>'service_type' NOT IN('parking','lounge')
              OR b->>'service_type' IS DISTINCT FROM d->>'service_type' OR b->'allowed_scopes' IS DISTINCT FROM '["booking:read"]'::jsonb
            THEN RAISE EXCEPTION 'Policy booking binding required' USING ERRCODE='23514';END IF;
          ELSIF kind='booking' THEN b:=d;
          ELSE b:=NULL;END IF;
          IF b IS NOT NULL THEN
            IF b->'allowed_scopes' IS DISTINCT FROM '["booking:read"]'::jsonb
            THEN RAISE EXCEPTION 'Booking scope required' USING ERRCODE='23514';END IF;
            requirements:=requirements||jsonb_build_array(jsonb_build_object('source','reference','resource_type','booking',
              'reference',b->>'reference','required_source_scopes',jsonb_build_array('booking:read')));
          END IF;
          IF kind='transaction' THEN
            scopes:=d->'allowed_scopes';
            IF jsonb_typeof(scopes) IS DISTINCT FROM 'array' OR NOT(scopes @> '["payment_status:read"]'::jsonb)
              OR((d ?| ARRAY['currency','amount_minor','currency_exponent','refund_status']) AND NOT(scopes @> '["payment_details:read"]'::jsonb))
              OR((scopes @> '["payment_details:read"]'::jsonb) AND NOT(d ?& ARRAY['currency','amount_minor','currency_exponent','refund_status']))
            THEN RAISE EXCEPTION 'Payment scopes do not cover observations' USING ERRCODE='23514';END IF;
            requirements:=requirements||jsonb_build_array(jsonb_build_object('source','reference','resource_type','transaction',
              'reference',d->>'reference','required_source_scopes',scopes));
            IF d->'booking_reference' IS NOT NULL AND d->'booking_reference'<>'null'::jsonb THEN
              IF jsonb_typeof(d->'booking_reference')<>'string' OR(d->>'booking_reference')!~'^[A-Za-z0-9_-]{{1,80}}$'
              THEN RAISE EXCEPTION 'Invalid payment booking reference' USING ERRCODE='23514';END IF;
              requirements:=requirements||jsonb_build_array(jsonb_build_object('source','reference','resource_type','booking',
                'reference',d->>'booking_reference','required_source_scopes',jsonb_build_array('booking:read')));
            END IF;
          END IF;
        END LOOP;
        RETURN {q}.source_labels_union(ARRAY[requirements]);
      END $$''')
    op.execute(f'''CREATE FUNCTION {q}.source_evidence_valid(records jsonb,labels jsonb) RETURNS boolean
      LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$ BEGIN
        IF NOT {q}.source_labels_valid(labels) THEN RETURN false;END IF;
        RETURN {q}.source_labels_contains(labels,{q}.source_evidence_requirements(records));
      EXCEPTION WHEN check_violation OR invalid_parameter_value THEN RETURN false;END $$''')


def _status(q, schema):
    # Metadata inventory only, never an authorization grant or activation flag.
    op.execute(f'''CREATE FUNCTION {q}.private_source_schema_status() RETURNS jsonb
      LANGUAGE sql STABLE SET search_path=pg_catalog AS $$ SELECT jsonb_build_object(
        'schema_version',1,'classification_schema',true,
        'graph_contract',to_regprocedure('{schema}.validate_private_source_graph()') IS NOT NULL,
        'outbound_contract',to_regprocedure('{schema}.mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)') IS NOT NULL
          AND to_regprocedure('{schema}.mail_private_begin(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)') IS NOT NULL
          AND to_regprocedure('{schema}.mail_private_send_snapshot(uuid,uuid,uuid,uuid,bigint,uuid)') IS NOT NULL
          AND to_regprocedure('{schema}.mail_private_abort_unsent(uuid,uuid,uuid,uuid,bigint,uuid,text)') IS NOT NULL
          AND to_regprocedure('{schema}.mail_private_complete(uuid,uuid,uuid,uuid,bigint,uuid,text,text,text)') IS NOT NULL,
        'inbound_contract',to_regprocedure('{schema}.email_private_reply_candidate(uuid,uuid,uuid,uuid,bigint)') IS NOT NULL
          AND to_regprocedure('{schema}.email_private_reply_apply(uuid,uuid,uuid,uuid,bigint,jsonb,jsonb)') IS NOT NULL,
        'history_contract',to_regprocedure('{schema}.mail_private_history_context(uuid,text)') IS NOT NULL)
      $$''')


def downgrade():
    raise RuntimeError('Source classification recovery requires a reviewed forward migration.')
