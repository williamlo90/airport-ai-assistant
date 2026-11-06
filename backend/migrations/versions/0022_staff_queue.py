"""Own-membership queue labels without a case, plus stable created-order index."""
from alembic import context, op
revision = '0022_staff_queue'
down_revision = '0021_staff_capabilities'
branch_labels = None
depends_on = None


def upgrade():
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, runtime, owner = (quote(context.config.attributes[k]) for k in ('schema', 'runtime_role', 'auth_role'))
    # Existing queue/status index cannot provide all-queue, status-free created ordering.
    op.execute(f'CREATE INDEX cases_workspace_created ON {q}.cases(tenant_id,organization_id,created_at,id)')
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {owner}')
    op.execute(f"""CREATE FUNCTION {q}.case_staff_queue_workspace(mid uuid,requested_queue uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE m record; candidate record; meta jsonb; queues jsonb:='[]'::jsonb; found_queue boolean:=false; BEGIN
        IF NOT {q}.case_staff_session_valid() THEN RETURN NULL; END IF;
        SELECT id,organization_id,principal_id INTO m FROM {q}.staff_memberships
          WHERE tenant_id={q}.current_tenant_id() AND id=mid AND principal_id={q}.current_principal_id() AND active;
        IF NOT FOUND THEN RETURN NULL; END IF;
        FOR candidate IN SELECT sq.id,sq.name FROM {q}.membership_queues mq
          JOIN {q}.service_queues sq ON(sq.tenant_id,sq.id,sq.organization_id)=(mq.tenant_id,mq.queue_id,mq.organization_id)
          WHERE mq.tenant_id={q}.current_tenant_id() AND mq.membership_id=mid AND sq.organization_id=m.organization_id AND sq.active
          ORDER BY sq.id LOOP
          meta:={q}.case_member_metadata(mid,m.organization_id,candidate.id);
          IF meta IS NOT NULL AND (meta->>'principal_id')::uuid={q}.current_principal_id() THEN
            queues:=queues||jsonb_build_array(jsonb_build_object('id',candidate.id,'name',candidate.name));
            IF candidate.id=requested_queue THEN found_queue:=true; END IF;
          END IF;
        END LOOP;
        IF queues='[]'::jsonb OR (requested_queue IS NOT NULL AND NOT found_queue) THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('membership_id',mid,
          'membership_version',(SELECT version FROM {q}.staff_memberships WHERE tenant_id={q}.current_tenant_id() AND id=mid),
          'organization',(SELECT jsonb_build_object('id',id,'name',name) FROM {q}.organizations WHERE tenant_id={q}.current_tenant_id() AND id=m.organization_id),
          'queues',queues);
      END $$""")
    signature = 'case_staff_queue_workspace(uuid,uuid)'
    op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {owner}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def downgrade():
    raise RuntimeError('Staff queue recovery requires a reviewed forward migration.')
