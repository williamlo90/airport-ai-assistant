"""Scoped administrative RPCs, versioned atomic membership changes and audit."""
from pathlib import Path
from alembic import context,op

revision='0038_staff_administration'
down_revision='0037_customer_ratings'
branch_labels=depends_on=None


def upgrade():
    a=context.config.attributes;bind=op.get_bind();quote=bind.dialect.identifier_preparer.quote_identifier
    s=a['schema'];q,runtime,policy,worker=(quote(a[key]) for key in ('schema','runtime_role','policy_role','worker_role'))
    def execute(statement):bind.exec_driver_sql(statement,execution_options={'no_parameters':True})
    execute(f'''GRANT CREATE ON SCHEMA {q} TO {policy};
      GRANT EXECUTE ON FUNCTION {q}.current_tenant_id(),{q}.current_principal_id(),{q}.current_actor_kind(),
        {q}.current_session_id(),{q}.case_staff_session_valid() TO {policy};''')
    source=(Path(__file__).resolve().parent.parent/'sql'/'0038_staff_administration.sql').read_text(encoding='utf-8')
    for key,value in (('@SCHEMA@',q),('@RUNTIME@',runtime),('@POLICY@',policy)):source=source.replace(key,value)
    execute(source)
    execute(f'REVOKE CREATE ON SCHEMA {q} FROM {policy}')
    checks=[]
    for table in ('staff_memberships','membership_roles','membership_queues','organizations','service_queues','membership_events'):
        checks.append(f"EXISTS(SELECT 1 FROM pg_class WHERE oid='{q}.{table}'::regclass AND relrowsecurity AND relforcerowsecurity)")
        digest=bind.exec_driver_sql("SELECT md5(string_agg(polname||':'||polcmd::text||':'||polroles::text||':'||polpermissive::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname)) FROM pg_policy WHERE polrelid=%s::regclass",(s+'.'+table,)).scalar_one()
        checks.append(f"(SELECT md5(string_agg(polname||':'||polcmd::text||':'||polroles::text||':'||polpermissive::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname))='{digest}' FROM pg_policy WHERE polrelid='{q}.{table}'::regclass)")
    for table in ('staff_memberships','membership_roles','membership_queues','membership_events'):
        for privilege in ('INSERT','UPDATE'):
            checks.append(f"NOT has_any_column_privilege('{runtime}'::regrole,'{q}.{table}','{privilege}')")
        checks.append(f"NOT has_table_privilege('{runtime}'::regrole,'{q}.{table}','DELETE')")
    checks.extend([f"NOT has_any_column_privilege('{runtime}'::regrole,'{q}.membership_events','SELECT')",
        f"NOT has_any_column_privilege('{worker}'::regrole,'{q}.membership_events','SELECT')",
        f"NOT has_any_column_privilege('{policy}'::regrole,'{q}.staff_credentials','SELECT')"])
    for signature in ('admin_workspace(uuid)','admin_member(uuid,uuid)','admin_directory(uuid,uuid,integer)',
                      'admin_change_membership(uuid,integer,uuid,integer,boolean,text[],uuid[])','case_staff_session_valid()','staff_eligible(uuid,uuid)'):
        digest,owner,secdef=bind.exec_driver_sql('SELECT md5(pg_get_functiondef(p.oid)),r.rolname,p.prosecdef FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid=%s::regprocedure',(s+'.'+signature,)).one()
        checks.append(f"EXISTS(SELECT 1 FROM pg_proc WHERE oid='{q}.{signature}'::regprocedure AND md5(pg_get_functiondef(oid))='{digest}' AND proowner='{quote(owner)}'::regrole AND prosecdef={'true' if secdef else 'false'} AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(proacl,acldefault('f',proowner))) WHERE grantee=0 AND privilege_type='EXECUTE'))")
        if signature.startswith('admin_'):
            checks.extend([f"has_function_privilege('{runtime}'::regrole,'{q}.{signature}','EXECUTE')",
                f"NOT has_function_privilege('{worker}'::regrole,'{q}.{signature}','EXECUTE')"])
    digest=bind.exec_driver_sql("SELECT md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname)) FROM pg_constraint WHERE conrelid=%s::regclass",(s+'.membership_events',)).scalar_one()
    checks.append(f"(SELECT bool_and(convalidated) AND md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname))='{digest}' FROM pg_constraint WHERE conrelid='{q}.membership_events'::regclass)")
    execute(f'''CREATE FUNCTION {q}.administration_ready() RETURNS boolean LANGUAGE sql SET search_path=pg_catalog AS $$ SELECT {' AND '.join(checks)} $$;
      REVOKE ALL ON FUNCTION {q}.administration_ready() FROM PUBLIC;GRANT EXECUTE ON FUNCTION {q}.administration_ready() TO {runtime};''')


def downgrade():raise RuntimeError('Use a reviewed forward recovery migration.')
