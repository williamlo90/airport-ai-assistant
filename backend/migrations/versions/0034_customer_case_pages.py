"""Owner-only ancestry for bounded customer case pages."""
import hashlib
from alembic import context, op

revision = '0034_customer_case_pages'
down_revision = '0033_case_email_translation'
branch_labels = depends_on = None


def upgrade():
    a = context.config.attributes
    s = a['schema']
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, runtime = quote(s), quote(a['runtime_role'])
    reply = quote('airport_assistant_reply_owner' if s == 'app' else 'reply_owner_' + hashlib.sha256(s.encode()).hexdigest()[:24])
    op.get_bind().exec_driver_sql(f'''GRANT EXECUTE ON FUNCTION {q}.conversation_session_valid(),
        {q}.current_actor_kind(),{q}.current_tenant_id(),{q}.current_principal_id() TO {reply};
      GRANT CREATE ON SCHEMA {q} TO {reply};
      CREATE FUNCTION {q}.customer_case_ancestry(case_uuid uuid) RETURNS jsonb
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
      SELECT {q}.source_labels_union(ARRAY[c.resource_security_labels,d.resource_security_labels,v.resource_security_labels])
      FROM {q}.cases c JOIN {q}.action_drafts d ON(d.tenant_id,d.id)=(c.tenant_id,c.origin_draft_id)
      JOIN {q}.conversations v ON(v.tenant_id,v.id)=(c.tenant_id,c.origin_conversation_id)
      WHERE c.tenant_id={q}.current_tenant_id() AND c.id=case_uuid
        AND {q}.current_actor_kind()='customer' AND {q}.conversation_session_valid()
        AND c.owner_principal_id={q}.current_principal_id()
        AND EXISTS(SELECT 1 FROM {q}.case_participants p WHERE p.tenant_id=c.tenant_id AND p.case_id=c.id
          AND p.principal_id={q}.current_principal_id() AND p.revoked_at IS NULL)
      $$;
      REVOKE ALL ON FUNCTION {q}.customer_case_ancestry(uuid) FROM PUBLIC;
      ALTER FUNCTION {q}.customer_case_ancestry(uuid) OWNER TO {reply};
      REVOKE CREATE ON SCHEMA {q} FROM {reply};
      GRANT EXECUTE ON FUNCTION {q}.customer_case_ancestry(uuid) TO {runtime};
      CREATE INDEX customer_cases_created ON {q}.cases(tenant_id,owner_principal_id,created_at,id);
      CREATE INDEX customer_cases_status_created ON {q}.cases(tenant_id,owner_principal_id,status,created_at,id);
      ''', execution_options={'no_parameters': True})


def downgrade():
    raise RuntimeError('Use a reviewed forward recovery migration.')
