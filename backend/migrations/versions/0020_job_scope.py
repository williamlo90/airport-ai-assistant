"""Optional internal job allowlist narrows both claim and expired-lease recovery."""
from alembic import context,op
revision='0020_job_scope'
down_revision='0019_auth_mail'
branch_labels=None
depends_on=None


def upgrade():
    s=context.config.attributes['schema']
    fn=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.job_claim(uuid,text[],integer)',)).scalar_one()
    if fn.count('x.kind=ANY(kinds)')!=2:raise RuntimeError('Unexpected scheduler filters.')
    fn=fn.replace('DECLARE j ', 'DECLARE scope_raw text;scope_ids uuid[];j ',1)
    scope="""scope_raw:=NULLIF(current_setting('app.job_scope_ids',true),'');
      IF scope_raw IS NOT NULL THEN
        IF jsonb_typeof(scope_raw::jsonb)<>'array' OR jsonb_array_length(scope_raw::jsonb)>1000 THEN RAISE EXCEPTION 'Invalid job scope' USING ERRCODE='22023';END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(scope_raw::jsonb) v WHERE jsonb_typeof(v)<>'string') THEN RAISE EXCEPTION 'Invalid job scope' USING ERRCODE='22023';END IF;
        SELECT COALESCE(array_agg(v::uuid),ARRAY[]::uuid[]) INTO scope_ids FROM jsonb_array_elements_text(scope_raw::jsonb) v;
      END IF;
    """
    fn=fn.replace('BEGIN','BEGIN '+scope,1)
    fn=fn.replace('x.kind=ANY(kinds)','x.kind=ANY(kinds) AND (scope_ids IS NULL OR x.id=ANY(scope_ids))')
    op.get_bind().exec_driver_sql(fn.replace('%','%%'))
    a=context.config.attributes;q=op.get_bind().dialect.identifier_preparer.quote_identifier
    schema,owner,worker=(q(v) for v in (s,a['job_owner_role'],a['worker_role']))
    op.execute(f'GRANT CREATE ON SCHEMA {schema} TO {owner}')
    op.execute(f"CREATE FUNCTION {schema}.job_scope_supported() RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT true $$")
    op.execute(f'REVOKE ALL ON FUNCTION {schema}.job_scope_supported() FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {schema}.job_scope_supported() OWNER TO {owner}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {schema}.job_scope_supported() TO {worker}')
    op.execute(f'REVOKE CREATE ON SCHEMA {schema} FROM {owner}')


def downgrade():raise RuntimeError('Job scope requires a reviewed recovery migration.')
