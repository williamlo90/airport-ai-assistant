"""Case-constrained staff labels and eligible membership directory, no raw grants."""
from alembic import context, op

revision = '0021_staff_capabilities'
down_revision = '0020_job_scope'
branch_labels = None
depends_on = None


def upgrade():
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, runtime, owner = (quote(context.config.attributes[k]) for k in ('schema', 'runtime_role', 'auth_role'))
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {owner}')
    definitions = [
        ('case_staff_workspace(uuid,uuid)', f"""
        CREATE FUNCTION {q}.case_staff_workspace(cid uuid, mid uuid) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c record; m record; meta jsonb; assigned jsonb; BEGIN
          IF NOT {q}.case_staff_session_valid() THEN RETURN NULL; END IF;
          SELECT organization_id,queue_id,assigned_membership_id INTO c FROM {q}.cases
            WHERE tenant_id={q}.current_tenant_id() AND id=cid;
          IF NOT FOUND THEN RETURN NULL; END IF;
          meta:={q}.case_member_metadata(mid,c.organization_id,c.queue_id);
          IF meta IS NULL OR (meta->>'principal_id')::uuid IS DISTINCT FROM {q}.current_principal_id() THEN RETURN NULL; END IF;
          SELECT id,version INTO m FROM {q}.staff_memberships WHERE tenant_id={q}.current_tenant_id() AND id=mid;
          IF {q}.case_member_metadata(c.assigned_membership_id,c.organization_id,c.queue_id) IS NOT NULL THEN
            SELECT jsonb_build_object('membership_id',sm.id,'display_name',p.display_name) INTO assigned
              FROM {q}.staff_memberships sm JOIN {q}.principals p ON(p.tenant_id,p.id)=(sm.tenant_id,sm.principal_id)
              WHERE sm.tenant_id={q}.current_tenant_id() AND sm.id=c.assigned_membership_id;
          END IF;
          RETURN jsonb_build_object('membership_id',m.id,'membership_version',m.version,'supervisor',(meta->>'supervisor')::boolean,
            'organization',(SELECT jsonb_build_object('id',id,'name',name) FROM {q}.organizations WHERE tenant_id={q}.current_tenant_id() AND id=c.organization_id),
            'queue',(SELECT jsonb_build_object('id',id,'name',name) FROM {q}.service_queues WHERE tenant_id={q}.current_tenant_id() AND id=c.queue_id),
            'assignee',assigned);
        END $$"""),
        ('case_staff_assignees(uuid,uuid,uuid,integer)', f"""
        CREATE FUNCTION {q}.case_staff_assignees(cid uuid, mid uuid, after_id uuid, page_size integer) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE scope jsonb; result jsonb; BEGIN
          IF page_size IS NULL OR page_size NOT BETWEEN 1 AND 101 THEN RETURN NULL; END IF;
          scope:={q}.case_staff_workspace(cid,mid);
          IF scope IS NULL OR NOT (scope->>'supervisor')::boolean THEN RETURN NULL; END IF;
          SELECT COALESCE(jsonb_agg(jsonb_build_object('membership_id',x.id,'display_name',x.display_name) ORDER BY x.id),'[]'::jsonb) INTO result
          FROM (SELECT m.id,p.display_name FROM {q}.staff_memberships m
            JOIN {q}.principals p ON(p.tenant_id,p.id)=(m.tenant_id,m.principal_id)
            WHERE m.tenant_id={q}.current_tenant_id() AND m.organization_id=(scope->'organization'->>'id')::uuid
              AND m.active AND p.active AND (after_id IS NULL OR m.id>after_id)
              AND EXISTS(SELECT 1 FROM {q}.membership_roles r WHERE r.tenant_id=m.tenant_id AND r.membership_id=m.id AND r.role IN('cs','supervisor'))
              AND EXISTS(SELECT 1 FROM {q}.membership_queues mq WHERE mq.tenant_id=m.tenant_id AND mq.membership_id=m.id AND mq.queue_id=(scope->'queue'->>'id')::uuid)
              AND {q}.case_member_metadata(m.id,(scope->'organization'->>'id')::uuid,(scope->'queue'->>'id')::uuid) IS NOT NULL
            ORDER BY m.id LIMIT page_size) x;
          RETURN result;
        END $$"""),
    ]
    for signature, definition in definitions:
        op.execute(definition)
        op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC')
        op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {owner}')
        op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def downgrade():
    raise RuntimeError('Staff capability recovery requires a reviewed forward migration.')
