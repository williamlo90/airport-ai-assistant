"""Staff-owned reviewed translation drafts for case-only email."""
from pathlib import Path
from alembic import context,op

revision='0033_case_email_translation'
down_revision='0032_case_email'
branch_labels=depends_on=None


def upgrade():
    a=context.config.attributes;s=a['schema'];quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q=quote(s);runtime=quote(a['runtime_role']);worker=quote(a['worker_role'])
    sql=(Path(__file__).resolve().parent.parent/'sql'/'0033_case_email_translation.sql').read_text(encoding='utf-8')
    sql=sql.replace('@SCHEMA@',q).replace('@RUNTIME@',runtime)
    op.get_bind().exec_driver_sql(sql,execution_options={'no_parameters':True})
    definition,owner=op.get_bind().exec_driver_sql('SELECT md5(pg_get_functiondef(p.oid)),r.rolname FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid=%s::regprocedure',(s+'.guard_case_email_translation()',)).one()
    constraints=op.get_bind().exec_driver_sql("SELECT md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname)) FROM pg_constraint WHERE conrelid=%s::regclass",(s+'.case_email_translation_drafts',)).scalar_one()
    policies=op.get_bind().exec_driver_sql("SELECT md5(string_agg(polname||':'||polcmd::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname)) FROM pg_policy WHERE polrelid=%s::regclass",(s+'.case_email_translation_drafts',)).scalar_one()
    op.get_bind().exec_driver_sql(f'''CREATE FUNCTION {q}.case_email_translation_ready() RETURNS boolean LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF to_regclass('{q}.case_email_translation_drafts') IS NULL OR to_regprocedure('{q}.guard_case_email_translation()') IS NULL THEN RETURN false;END IF;
      RETURN {q}.case_email_ready()
        AND EXISTS(SELECT 1 FROM pg_class WHERE oid='{q}.case_email_translation_drafts'::regclass AND relrowsecurity AND relforcerowsecurity)
        AND has_table_privilege('{runtime}'::regrole,'{q}.case_email_translation_drafts','SELECT')
        AND NOT has_table_privilege('{worker}'::regrole,'{q}.case_email_translation_drafts','SELECT')
        AND NOT has_any_column_privilege('{worker}'::regrole,'{q}.case_email_translation_drafts','SELECT')
        AND(SELECT bool_and(has_column_privilege('{runtime}'::regrole,'{q}.case_email_translation_drafts',col,'INSERT')) FROM unnest(ARRAY['tenant_id','id','case_id','principal_id','session_id','membership_id','request_key','request_hash','original_body','source_language','target_language','case_version','source_context_hash','resource_security_labels']) col)
        AND(SELECT bool_and(has_column_privilege('{runtime}'::regrole,'{q}.case_email_translation_drafts',col,'UPDATE')) FROM unnest(ARRAY['state','translated_body','reviewed_body','message_id','send_key','revision']) col)
        AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='{q}.case_email_translation_drafts'::regclass AND tgname='case_email_translation_guard' AND tgfoid='{q}.guard_case_email_translation()'::regprocedure AND tgenabled='O' AND tgtype=23)
        AND EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='{q}.guard_case_email_translation()'::regprocedure AND NOT p.prosecdef AND p.proowner='{quote(owner)}'::regrole AND md5(pg_get_functiondef(p.oid))='{definition}'
          AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
        AND(SELECT bool_and(convalidated) AND md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname))='{constraints}' FROM pg_constraint WHERE conrelid='{q}.case_email_translation_drafts'::regclass)
        AND(SELECT count(*)=3 AND bool_and(polpermissive) AND bool_and(polroles=ARRAY['{runtime}'::regrole::oid]) AND md5(string_agg(polname||':'||polcmd::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname))='{policies}' FROM pg_policy WHERE polrelid='{q}.case_email_translation_drafts'::regclass);
      END $$;
      REVOKE ALL ON FUNCTION {q}.case_email_translation_ready() FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION {q}.case_email_translation_ready() TO {runtime};''',execution_options={'no_parameters':True})


def downgrade():raise RuntimeError('Translation records require a reviewed forward recovery migration.')
