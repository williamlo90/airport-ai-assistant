"""Case-owned human email, without manufacturing a customer live chat."""
import hashlib
from pathlib import Path
from alembic import context,op
from airport_assistant.infrastructure.database.provisioning import role_name

revision='0032_case_email'
down_revision='0031_private_mail'
branch_labels=depends_on=None


def patch(schema,signature,old,new):
    definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(schema+'.'+signature,)).scalar_one()
    if definition.count(old)!=1:raise RuntimeError('Unexpected reviewed function: '+signature)
    op.get_bind().exec_driver_sql(definition.replace(old,new),execution_options={'no_parameters':True})


def upgrade():
    a=context.config.attributes;s=a['schema'];suffix=hashlib.sha256(s.encode()).hexdigest()[:24]
    names=dict(SCHEMA=s,RUNTIME=a['runtime_role'],WORKER=a['worker_role'],
        MAIL='airport_assistant_mail_owner' if s=='app' else 'mail_owner_'+suffix,
        REPLY='airport_assistant_reply_owner' if s=='app' else 'reply_owner_'+suffix)
    for k,v in names.items():
        if k!='SCHEMA':role_name(v)
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    n={k:quote(v) for k,v in names.items()};q=n['SCHEMA']
    # Preserve every existing author shape verbatim, adding one disjoint branch.
    old=op.get_bind().exec_driver_sql("SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint WHERE conrelid=%s::regclass AND conname='message_author_shape'",(s+'.messages',)).scalar_one()
    extra="""author_type='staff' AND author_principal_id IS NOT NULL AND author_session_id IS NULL
      AND origin_session_id IS NULL AND source_kind='staff_reply' AND conversation_id IS NULL AND case_id IS NOT NULL
      AND delivery_channel='email' AND created_session_id IS NOT NULL AND sender_membership_id IS NOT NULL
      AND staff_request_key IS NOT NULL AND length(staff_request_key) BETWEEN 16 AND 128
      AND request_hash IS NOT NULL AND request_hash ~ '^[a-f0-9]{64}$' AND generation_id IS NULL
      AND basis_case_version IS NOT NULL AND basis_case_version>0 AND basis_conversation_version IS NULL AND basis_epoch IS NULL"""
    op.execute(f'ALTER TABLE {q}.messages DROP CONSTRAINT message_author_shape, ADD CONSTRAINT message_author_shape CHECK(({old}) OR({extra}))')
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {n["REPLY"]}')
    sql=(Path(__file__).resolve().parent.parent/'sql'/'0032_case_email.sql').read_text(encoding='utf-8')
    for k,v in n.items():sql=sql.replace('@'+k+'@',v)
    op.get_bind().exec_driver_sql(sql,execution_options={'no_parameters':True})
    # Deferred graph checks still require a real source message and its origin draft.
    old=f"FROM {q}.messages m JOIN {q}.conversations v ON v.tenant_id=m.tenant_id AND v.id=m.conversation_id JOIN {q}.cases c ON c.tenant_id=v.tenant_id AND c.id=v.case_id"
    new=f"FROM {q}.messages m LEFT JOIN {q}.conversations v ON v.tenant_id=m.tenant_id AND v.id=m.conversation_id JOIN {q}.cases c ON c.tenant_id=m.tenant_id AND c.id=coalesce(m.case_id,v.case_id)"
    patch(s,'guard_email_job_graph()',old,new)
    # The worker still receives only the existing fenced projection; its
    # non-login projection owner needs these two additional provenance columns.
    op.execute(f'GRANT SELECT(basis_conversation_version,basis_epoch) ON {q}.messages TO {n["MAIL"]}')
    patch(s,'mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)',
        "AND mv.case_id=c.id)",
        "AND(mv.case_id=c.id OR(m.conversation_id IS NULL AND m.case_id=c.id AND m.source_kind='staff_reply' AND m.basis_conversation_version IS NULL AND m.basis_epoch IS NULL)))")
    # The creation-graph trigger is for the initial customer report. Staff
    # cannot read that customer's draft via runtime RLS; their independent
    # email guard + message/job constraints validate subsequent correspondence.
    patch(s,'validate_case_commit()',
        'IF NEW.case_id IS NULL THEN RETURN NULL;END IF;',
        "IF NEW.case_id IS NULL OR(NEW.author_type='staff' AND NEW.conversation_id IS NULL AND NEW.delivery_channel='email') THEN RETURN NULL;END IF;")
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {n["REPLY"]}')
    # Admission attests installed semantics, not just a function/trigger name.
    checks=[]
    for signature in ('case_email_context(uuid)','case_correspondence_ancestry(uuid)','guard_case_email()','guard_email_job_graph()',
                      'mail_private_snapshot(uuid,uuid,uuid,uuid,bigint)','validate_case_commit()'):
        digest,owner=op.get_bind().exec_driver_sql('SELECT md5(pg_get_functiondef(p.oid)),r.rolname FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid=%s::regprocedure',(s+'.'+signature,)).one()
        role_name(owner)
        checks.append(f"EXISTS(SELECT 1 FROM pg_proc WHERE oid='{q}.{signature}'::regprocedure AND md5(pg_get_functiondef(oid))='{digest}' AND proowner='{quote(owner)}'::regrole)")
    digest=op.get_bind().exec_driver_sql("SELECT md5(pg_get_constraintdef(oid)) FROM pg_constraint WHERE conrelid=%s::regclass AND conname='message_author_shape'",(s+'.messages',)).scalar_one()
    checks.append(f"EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='{q}.messages'::regclass AND conname='message_author_shape' AND convalidated AND md5(pg_get_constraintdef(oid))='{digest}')")
    digest=op.get_bind().exec_driver_sql("SELECT md5(pg_get_expr(polwithcheck,polrelid)) FROM pg_policy WHERE polrelid=%s::regclass AND polname='runtime_case_email'",(s+'.messages',)).scalar_one()
    checks.append(f"EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='{q}.messages'::regclass AND polname='runtime_case_email' AND polcmd='a' AND polroles=ARRAY['{n['RUNTIME']}'::regrole::oid] AND md5(pg_get_expr(polwithcheck,polrelid))='{digest}')")
    for signature in ('case_email_context(uuid)','case_correspondence_ancestry(uuid)'):
        checks.append(f"has_function_privilege('{n['RUNTIME']}'::regrole,'{q}.{signature}','EXECUTE') AND NOT has_function_privilege('{n['WORKER']}'::regrole,'{q}.{signature}','EXECUTE') AND NOT EXISTS(SELECT 1 FROM pg_proc p,aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE p.oid='{q}.{signature}'::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')")
    guards=[('messages','email_job_graph','guard_email_job_graph()',5),('outbox_jobs','email_job_graph','guard_email_job_graph()',5)]
    guards += [(table,'validate_case_commit','validate_case_commit()',21 if table=='action_drafts' else 5)
               for table in ('messages','cases','action_drafts','action_consents','idempotency_records')]
    for table,name,signature,kind in guards:
        checks.append(f"EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='{q}.{table}'::regclass AND tgname='{name}' AND tgfoid='{q}.{signature}'::regprocedure AND tgenabled='O' AND tgtype={kind} AND tgdeferrable AND tginitdeferred AND tgconstraint<>0)")
    patch(s,'case_email_ready()',f'SELECT {q}.private_mail_graph_ready()',
          f'SELECT {q}.private_mail_graph_ready() AND '+' AND '.join(checks))


def downgrade():raise RuntimeError('Case email recovery requires a reviewed forward migration.')
