"""One confirmed customer preview publishes one message and case revision."""
from pathlib import Path
from alembic import context, op

revision='0035_customer_information'
down_revision='0034_customer_case_pages'
branch_labels=depends_on=None


def upgrade():
    a=context.config.attributes;s=a['schema']
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q,runtime=quote(s),quote(a['runtime_role'])
    bind=op.get_bind()
    def execute(sql):bind.exec_driver_sql(sql,execution_options={'no_parameters':True})
    def definition(signature):return bind.exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.'+signature,)).scalar_one()
    def patch(signature,old,new):
        sql=definition(signature)
        if sql.count(old)!=1:raise RuntimeError('Unexpected reviewed customer information boundary: '+signature)
        execute(sql.replace(old,new))
    old_commit_hash=bind.exec_driver_sql('SELECT md5(pg_get_functiondef(%s::regprocedure))',(s+'.validate_case_commit()',)).scalar_one()
    old_shape_hash=bind.exec_driver_sql("SELECT md5(pg_get_constraintdef(oid)) FROM pg_constraint WHERE conrelid=%s::regclass AND conname='message_author_shape'",(s+'.messages',)).scalar_one()
    old_shape=bind.exec_driver_sql("SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint WHERE conrelid=%s::regclass AND conname='message_author_shape'",(s+'.messages',)).scalar_one()
    shape="""source_kind='customer_update' AND author_type='customer' AND author_principal_id IS NOT NULL
      AND author_session_id IS NULL AND origin_session_id IS NULL AND conversation_id IS NULL AND case_id IS NOT NULL
      AND created_session_id IS NOT NULL AND sender_membership_id IS NULL AND staff_request_key IS NULL
      AND request_hash IS NOT NULL AND request_hash ~ '^[a-f0-9]{64}$' AND generation_id IS NULL
      AND client_message_id IS NOT NULL AND basis_case_version IS NOT NULL AND basis_case_version>0
      AND basis_conversation_version IS NULL AND basis_epoch IS NULL AND channel='web' AND delivery_channel='chat'"""
    execute(f'''ALTER TABLE {q}.messages DROP CONSTRAINT messages_source_kind_check,DROP CONSTRAINT message_author_shape,
      ADD CONSTRAINT messages_source_kind_check CHECK(source_kind IN('user_report','staff_reply','ai_response','email_reply','customer_update')),
      ADD CONSTRAINT message_author_shape CHECK(({old_shape}) OR({shape}));
      ALTER TABLE {q}.case_history DROP CONSTRAINT case_history_event_type_check,
      ADD CONSTRAINT case_history_event_type_check CHECK(event_type IN('created','identity_verified','assigned','status_changed','reopened','email_received','customer_information'));
      CREATE UNIQUE INDEX customer_information_history_once ON {q}.case_history(tenant_id,case_id,client_request_id) WHERE event_type='customer_information';''')
    sql=(Path(__file__).resolve().parent.parent/'sql'/'0035_customer_information.sql').read_text(encoding='utf-8')
    execute(sql.replace('@SCHEMA@',q).replace('@RUNTIME@',runtime))
    # Only an otherwise immutable open case with a fresh owner-ledger row can
    # advance its revision. The original continuation/staff/adoption branches stay.
    marker=f"IF {q}.current_actor_kind()='customer' AND {q}.conversation_session_valid() AND OLD.owner_principal_id={q}.current_principal_id() THEN"
    special=f'''IF {q}.current_actor_kind()='customer' AND {q}.conversation_session_valid()
      AND OLD.owner_principal_id={q}.current_principal_id() AND OLD.status<>'closed'
      AND (to_jsonb(NEW)-ARRAY['version','updated_at'])=(to_jsonb(OLD)-ARRAY['version','updated_at'])
      AND EXISTS(SELECT 1 FROM {q}.customer_case_updates u WHERE u.tenant_id=OLD.tenant_id AND u.case_id=OLD.id
        AND u.principal_id=OLD.owner_principal_id AND u.session_id={q}.current_session_id()
        AND u.expected_version=OLD.version AND u.preview_expires_at>clock_timestamp()
        AND u.resource_security_labels={q}.customer_case_ancestry(OLD.id)) THEN RETURN NEW;END IF;
        '''
    patch('guard_case_lifecycle()',marker,special+marker)
    patch('validate_case_lifecycle()',"h.event_type='reopened'","h.event_type IN('reopened','customer_information')")
    # The existing full lifecycle snapshot still must match; the new deferred
    # graph additionally requires an exact ledger/message/history triple.
    patch('validate_chat_transition()',
        f'SELECT chat_transition_id INTO rid FROM {q}.case_history WHERE tenant_id=NEW.tenant_id AND case_id=NEW.id AND resulting_version=NEW.version;',
        f'''IF {q}.current_actor_kind()='customer'
          AND (to_jsonb(NEW)-ARRAY['version','updated_at'])=(to_jsonb(OLD)-ARRAY['version','updated_at'])
          AND {q}.customer_information_pair(NEW.id,OLD.version,NEW.version) THEN RETURN NULL;END IF;
          SELECT chat_transition_id INTO rid FROM {q}.case_history WHERE tenant_id=NEW.tenant_id AND case_id=NEW.id AND resulting_version=NEW.version;''')
    # Subsequent original customer information has its own reciprocal graph;
    # never make the initial creation graph accept arbitrary follow-up messages.
    patch('validate_case_commit()',"IF NEW.case_id IS NULL OR(NEW.author_type='staff' AND NEW.conversation_id IS NULL AND NEW.delivery_channel='email') THEN RETURN NULL;END IF;",
        f"""IF NEW.source_kind='customer_update' AND NEW.author_type='customer' AND NEW.conversation_id IS NULL
          AND EXISTS(SELECT 1 FROM {q}.customer_case_updates u WHERE u.tenant_id=NEW.tenant_id AND u.case_id=NEW.case_id
            AND u.message_id=NEW.id AND u.id=NEW.client_message_id AND u.principal_id=NEW.author_principal_id)
          THEN RETURN NULL;END IF;
          IF NEW.case_id IS NULL OR(NEW.author_type='staff' AND NEW.conversation_id IS NULL AND NEW.delivery_channel='email') THEN RETURN NULL;END IF;""")
    # Refresh only the two attestations whose reviewed definitions changed.
    new_commit_hash=bind.exec_driver_sql('SELECT md5(pg_get_functiondef(%s::regprocedure))',(s+'.validate_case_commit()',)).scalar_one()
    new_shape_hash=bind.exec_driver_sql("SELECT md5(pg_get_constraintdef(oid)) FROM pg_constraint WHERE conrelid=%s::regclass AND conname='message_author_shape'",(s+'.messages',)).scalar_one()
    patch('case_email_ready()',old_commit_hash,new_commit_hash)
    patch('case_email_ready()',old_shape_hash,new_shape_hash)
    worker=quote(a['worker_role'])
    checks=[f'{q}.case_email_ready()',
        f"EXISTS(SELECT 1 FROM pg_class WHERE oid='{q}.customer_case_updates'::regclass AND relrowsecurity AND relforcerowsecurity)",
        f"has_table_privilege('{runtime}'::regrole,'{q}.customer_case_updates','SELECT')",
        f"NOT has_any_column_privilege('{worker}'::regrole,'{q}.customer_case_updates','SELECT')",
        f"NOT has_any_column_privilege('{runtime}'::regrole,'{q}.customer_case_updates','UPDATE')",
        f"NOT has_table_privilege('{runtime}'::regrole,'{q}.customer_case_updates','DELETE')"]
    for signature in ('guard_customer_update()','guard_customer_information_message()','customer_information_pair(uuid,bigint,bigint)',
                      'validate_customer_information()','guard_case_lifecycle()','validate_case_lifecycle()',
                      'validate_chat_transition()','guard_lifecycle_history()','customer_case_ancestry(uuid)'):
        digest,owner,secdef=bind.exec_driver_sql('SELECT md5(pg_get_functiondef(p.oid)),r.rolname,p.prosecdef FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid=%s::regprocedure',(s+'.'+signature,)).one()
        checks.append(f"EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='{q}.{signature}'::regprocedure AND md5(pg_get_functiondef(p.oid))='{digest}' AND p.proowner='{quote(owner)}'::regrole AND p.prosecdef={'true' if secdef else 'false'} AND NOT EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))")
    for signature in ('customer_case_ancestry(uuid)','customer_information_pair(uuid,bigint,bigint)'):
        checks.append(f"has_function_privilege('{runtime}'::regrole,'{q}.{signature}','EXECUTE') AND NOT has_function_privilege('{worker}'::regrole,'{q}.{signature}','EXECUTE')")
    constraint_hash=bind.exec_driver_sql("SELECT md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname)) FROM pg_constraint WHERE conrelid=%s::regclass",(s+'.customer_case_updates',)).scalar_one()
    policy_hash=bind.exec_driver_sql("SELECT md5(string_agg(polname||':'||polcmd::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname)) FROM pg_policy WHERE polrelid=%s::regclass",(s+'.customer_case_updates',)).scalar_one()
    checks.append(f"(SELECT bool_and(convalidated) AND md5(string_agg(conname||':'||pg_get_constraintdef(oid),',' ORDER BY conname))='{constraint_hash}' FROM pg_constraint WHERE conrelid='{q}.customer_case_updates'::regclass)")
    checks.append(f"(SELECT count(*)=2 AND bool_and(polpermissive) AND bool_and(polroles=ARRAY['{runtime}'::regrole::oid]) AND md5(string_agg(polname||':'||polcmd::text||':'||coalesce(pg_get_expr(polqual,polrelid),'')||':'||coalesce(pg_get_expr(polwithcheck,polrelid),''),',' ORDER BY polname))='{policy_hash}' FROM pg_policy WHERE polrelid='{q}.customer_case_updates'::regclass)")
    for table,name,signature,kind,deferred in (
        ('customer_case_updates','customer_update_guard','guard_customer_update()',31,False),
        ('messages','customer_information_message','guard_customer_information_message()',7,False),
        ('customer_case_updates','customer_information_graph','validate_customer_information()',5,True),
        ('messages','customer_information_graph','validate_customer_information()',5,True),
        ('case_history','customer_information_graph','validate_customer_information()',5,True),
        ('cases','guard_case_lifecycle','guard_case_lifecycle()',19,False),
        ('cases','validate_case_lifecycle','validate_case_lifecycle()',17,True),
        ('cases','case_chat_pair_check','validate_chat_transition()',17,True),
        ('case_history','guard_lifecycle_history','guard_lifecycle_history()',7,False)):
        checks.append(f"EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='{q}.{table}'::regclass AND tgname='{name}' AND tgfoid='{q}.{signature}'::regprocedure AND tgenabled='O' AND tgtype={kind} AND tgdeferrable={'true' if deferred else 'false'} AND tginitdeferred={'true' if deferred else 'false'})")
    columns=['tenant_id','id','case_id','principal_id','session_id','request_key','request_hash','message_id','message','language','expected_version','preview_expires_at','resource_security_labels']
    checks.append(f"(SELECT bool_and(has_column_privilege('{runtime}'::regrole,'{q}.customer_case_updates',col,'INSERT')) FROM unnest(ARRAY["+','.join("'"+c+"'" for c in columns)+']) col)')
    execute(f'''CREATE FUNCTION {q}.customer_information_ready() RETURNS boolean LANGUAGE sql SET search_path=pg_catalog AS $$
      SELECT {' AND '.join(checks)} $$;
      REVOKE ALL ON FUNCTION {q}.customer_information_ready() FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION {q}.customer_information_ready() TO {runtime};''')


def downgrade():raise RuntimeError('Customer correspondence requires a reviewed forward recovery migration.')
