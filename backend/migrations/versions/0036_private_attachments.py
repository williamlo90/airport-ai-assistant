"""Admit inherited private evidence without changing owner/session/file rules."""
from alembic import context,op

revision='0036_private_attachments'
down_revision='0035_customer_information'
branch_labels=depends_on=None


def upgrade():
    a=context.config.attributes;s=a['schema'];bind=op.get_bind()
    quote=bind.dialect.identifier_preparer.quote_identifier
    q,runtime,worker=quote(s),quote(a['runtime_role']),quote(a['worker_role'])
    def execute(sql):bind.exec_driver_sql(sql,execution_options={'no_parameters':True})
    for name in ('runtime_read','runtime_insert','runtime_bind'):
        using,check=bind.exec_driver_sql("SELECT pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid=%s::regclass AND polname=%s",(s+'.attachments',name)).one()
        clauses=[]
        for keyword,expression in (('USING',using),('WITH CHECK',check)):
            if expression is None:continue
            marker="resource_security_labels = '[]'::jsonb"
            if expression.count(marker)!=1:raise RuntimeError('Unexpected reviewed attachment policy: '+name)
            clauses.append(keyword+'('+expression.replace(marker,f'{q}.source_labels_valid(resource_security_labels)')+')')
        execute(f'ALTER POLICY {name} ON {q}.attachments '+ ' '.join(clauses))
    # Canonical classification and the actual inherited/deferred graph replace
    # this deliberately retained legacy empty-only hold, and nothing else.
    execute(f'ALTER TABLE {q}.attachments DROP CONSTRAINT attachments_resource_security_labels_check')
    selection=bind.exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.validate_draft_attachments()',)).scalar_one()
    marker="item.resource_security_labels<>'[]'::jsonb"
    if selection.count(marker)!=1:raise RuntimeError('Unexpected reviewed attachment selection guard.')
    execute(selection.replace(marker,f'NOT {q}.source_labels_valid(item.resource_security_labels) OR NOT {q}.source_labels_contains(NEW.resource_security_labels,item.resource_security_labels)'))
    guard=bind.exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.guard_attachment()',)).scalar_one()
    marker="IF d.state<>'pending' OR d.expires_at<=clock_timestamp() OR NEW.case_id IS NOT NULL"
    if guard.count(marker)!=1:raise RuntimeError('Unexpected reviewed attachment insertion guard.')
    execute(guard.replace(marker,marker+f' OR NOT {q}.source_labels_contains(d.resource_security_labels,NEW.resource_security_labels)'))
    checks=[f"EXISTS(SELECT 1 FROM pg_class WHERE oid='{q}.attachments'::regclass AND relrowsecurity AND relforcerowsecurity)",
        f"has_table_privilege('{runtime}'::regrole,'{q}.attachments','SELECT')",
        f"NOT has_any_column_privilege('{worker}'::regrole,'{q}.attachments','SELECT')",
        f"NOT has_table_privilege('{runtime}'::regrole,'{q}.attachments','DELETE')",
        f"NOT has_column_privilege('{runtime}'::regrole,'{q}.attachments','resource_security_labels','INSERT')",
        f"has_column_privilege('{runtime}'::regrole,'{q}.attachments','case_id','UPDATE')",
        f"(SELECT bool_and(NOT has_column_privilege('{runtime}'::regrole,'{q}.attachments',attname,'UPDATE')) FROM pg_attribute WHERE attrelid='{q}.attachments'::regclass AND attnum>0 AND NOT attisdropped AND attname<>'case_id')"]
    for signature in ('guard_attachment()','validate_draft_attachments()','bind_case_attachments()','inherit_private_source_graph()',
                      'validate_private_source_graph()','guard_source_labels_monotone()',
                      'source_labels_valid(jsonb)','source_labels_contains(jsonb,jsonb)','source_labels_union(jsonb[])'):
        digest,owner,secdef=bind.exec_driver_sql('SELECT md5(pg_get_functiondef(p.oid)),r.rolname,p.prosecdef FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid=%s::regprocedure',(s+'.'+signature,)).one()
        checks.append(f"EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='{q}.{signature}'::regprocedure AND md5(pg_get_functiondef(p.oid))='{digest}' AND p.proowner='{quote(owner)}'::regrole AND p.prosecdef={'true' if secdef else 'false'} AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))")
    constraint_hash=bind.exec_driver_sql("SELECT md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname)) FROM pg_constraint WHERE conrelid=%s::regclass",(s+'.attachments',)).scalar_one()
    checks.append(f"(SELECT bool_and(convalidated) AND md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname))='{constraint_hash}' FROM pg_constraint WHERE conrelid='{q}.attachments'::regclass)")
    condition=f"polrelid='{q}.attachments'::regclass AND('{runtime}'::regrole::oid=ANY(polroles) OR 0=ANY(polroles))"
    policy_hash=bind.exec_driver_sql(f"SELECT md5(string_agg(polname||':'||polcmd::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname)) FROM pg_policy WHERE {condition}").scalar_one()
    checks.append(f"(SELECT count(*)=3 AND bool_and(polpermissive) AND bool_and(polroles=ARRAY['{runtime}'::regrole::oid]) AND md5(string_agg(polname||':'||polcmd::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname))='{policy_hash}' FROM pg_policy WHERE {condition})")
    for table,name,signature,kind,deferred in (
        ('attachments','attachment_guard','guard_attachment()',23,False),
        ('attachments','a_private_source_inherit','inherit_private_source_graph()',23,False),
        ('attachments','private_source_graph','validate_private_source_graph()',21,True),
        ('attachments','private_labels_monotone','guard_source_labels_monotone()',19,False),
        ('action_drafts','draft_attachment_selection','validate_draft_attachments()',23,False),
        ('action_drafts','case_attachment_binding','bind_case_attachments()',17,False),
        ('action_drafts','a_private_source_inherit','inherit_private_source_graph()',7,False),
        ('action_drafts','private_source_graph','validate_private_source_graph()',21,True)):
        column={'private_labels_monotone':'resource_security_labels','draft_attachment_selection':'payload','case_attachment_binding':'state'}.get(name)
        attr=f"(SELECT attnum::text FROM pg_attribute WHERE attrelid='{q}.{table}'::regclass AND attname='{column}')" if column else "''"
        checks.append(f"EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='{q}.{table}'::regclass AND tgname='{name}' AND tgfoid='{q}.{signature}'::regprocedure AND tgenabled='O' AND tgtype={kind} AND tgattr::text={attr} AND tgdeferrable={'true' if deferred else 'false'} AND tginitdeferred={'true' if deferred else 'false'})")
    columns=['tenant_id','id','draft_id','uploader_principal_id','uploader_session_id','created_session_id','body','mime_type','byte_size','content_hash','scan_status']
    checks.append(f"(SELECT bool_and(has_column_privilege('{runtime}'::regrole,'{q}.attachments',col,'INSERT')) FROM unnest(ARRAY["+','.join("'"+col+"'" for col in columns)+']) col)')
    execute(f'''CREATE FUNCTION {q}.private_attachments_ready() RETURNS boolean LANGUAGE sql SET search_path=pg_catalog AS $$
      SELECT {' AND '.join(checks)} $$;
      REVOKE ALL ON FUNCTION {q}.private_attachments_ready() FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION {q}.private_attachments_ready() TO {runtime};''')


def downgrade():raise RuntimeError('Private evidence needs a reviewed forward recovery migration.')
