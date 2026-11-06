"""Optional owner-scoped feedback; no case or conversation lifecycle mutation."""
from pathlib import Path
from alembic import context,op

revision='0037_customer_ratings'
down_revision='0036_private_attachments'
branch_labels=depends_on=None


def upgrade():
    a=context.config.attributes;bind=op.get_bind();quote=bind.dialect.identifier_preparer.quote_identifier
    s=a['schema'];q,runtime,policy,worker=(quote(a[key]) for key in ('schema','runtime_role','policy_role','worker_role'))
    def execute(statement):bind.exec_driver_sql(statement,execution_options={'no_parameters':True})
    execute(f'GRANT CREATE ON SCHEMA {q} TO {policy}; GRANT EXECUTE ON FUNCTION {q}.current_principal_id(),{q}.current_session_id() TO {policy}')
    source=(Path(__file__).resolve().parent.parent/'sql'/'0037_customer_ratings.sql').read_text(encoding='utf-8')
    for key,value in (('@SCHEMA@',q),('@RUNTIME@',runtime),('@POLICY@',policy)):source=source.replace(key,value)
    execute(source)
    execute(f'REVOKE CREATE ON SCHEMA {q} FROM {policy}')
    checks=[]
    for table in ('ratings','rating_events'):
        checks.extend([f"EXISTS(SELECT 1 FROM pg_class WHERE oid='{q}.{table}'::regclass AND relrowsecurity AND relforcerowsecurity)",
            f"NOT has_any_column_privilege('{worker}'::regrole,'{q}.{table}','SELECT')"])
        digest=bind.exec_driver_sql("SELECT md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname)) FROM pg_constraint WHERE conrelid=%s::regclass",(s+'.'+table,)).scalar_one()
        checks.append(f"(SELECT bool_and(convalidated) AND md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname))='{digest}' FROM pg_constraint WHERE conrelid='{q}.{table}'::regclass)")
        digest=bind.exec_driver_sql("SELECT md5(string_agg(polname||':'||polcmd::text||':'||polroles::text||':'||polpermissive::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname)) FROM pg_policy WHERE polrelid=%s::regclass",(s+'.'+table,)).scalar_one()
        checks.append(f"(SELECT md5(string_agg(polname||':'||polcmd::text||':'||polroles::text||':'||polpermissive::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname))='{digest}' FROM pg_policy WHERE polrelid='{q}.{table}'::regclass)")
    for signature in ('rating_target(text,uuid,boolean)','guard_customer_rating()','audit_customer_rating()',
                      'source_labels_valid(jsonb)','source_labels_contains(jsonb,jsonb)','customer_case_ancestry(uuid)'):
        digest,owner,secdef=bind.exec_driver_sql('SELECT md5(pg_get_functiondef(p.oid)),r.rolname,p.prosecdef FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid=%s::regprocedure',(s+'.'+signature,)).one()
        checks.append(f"EXISTS(SELECT 1 FROM pg_proc WHERE oid='{q}.{signature}'::regprocedure AND md5(pg_get_functiondef(oid))='{digest}' AND proowner='{quote(owner)}'::regrole AND prosecdef={'true' if secdef else 'false'} AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) WHERE grantee=0 AND privilege_type='EXECUTE'))")
    # Partial target uniqueness is implemented by standalone indexes rather
    # than pg_constraint, so constraint fingerprints alone do not cover it.
    for name in ('rating_one_case','rating_one_conversation'):
        digest=bind.exec_driver_sql('SELECT md5(pg_get_indexdef(to_regclass(%s)))',(s+'.'+name,)).scalar_one()
        checks.append(f"EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('{q}.{name}') AND indrelid='{q}.ratings'::regclass AND indisunique AND indisvalid AND indisready AND md5(pg_get_indexdef(indexrelid))='{digest}')")
    for name,signature,kind in (('rating_guard','guard_customer_rating()',23),('rating_audit','audit_customer_rating()',21)):
        checks.append(f"EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='{q}.ratings'::regclass AND tgname='{name}' AND tgfoid='{q}.{signature}'::regprocedure AND tgenabled='O' AND tgtype={kind} AND NOT tgdeferrable)")
    checks.extend([f"NOT has_table_privilege('{runtime}'::regrole,'{q}.ratings','DELETE')",
        f"NOT has_column_privilege('{runtime}'::regrole,'{q}.ratings','resource_security_labels','INSERT')",
        f"NOT has_column_privilege('{runtime}'::regrole,'{q}.ratings','resource_security_labels','UPDATE')",
        f"NOT has_any_column_privilege('{runtime}'::regrole,'{q}.rating_events','SELECT')",
        f"NOT has_any_column_privilege('{runtime}'::regrole,'{q}.rating_events','INSERT')",
        f"NOT has_any_column_privilege('{runtime}'::regrole,'{q}.rating_events','UPDATE')"])
    execute(f'''CREATE FUNCTION {q}.ratings_ready() RETURNS boolean LANGUAGE sql SET search_path=pg_catalog AS $$ SELECT {' AND '.join(checks)} $$;
      REVOKE ALL ON FUNCTION {q}.ratings_ready() FROM PUBLIC;GRANT EXECUTE ON FUNCTION {q}.ratings_ready() TO {runtime};''')


def downgrade():raise RuntimeError('Use a reviewed forward recovery migration.')
