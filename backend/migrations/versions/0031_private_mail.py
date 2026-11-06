"""Private fenced mail capabilities and operational history; no activation."""
import hashlib
from pathlib import Path
from alembic import context,op
from airport_assistant.infrastructure.database.provisioning import role_name

revision='0031_private_mail'
down_revision='0030_private_source_graph'
branch_labels=depends_on=None


def upgrade():
    a=context.config.attributes;s=a['schema']
    suffix=hashlib.sha256(s.encode()).hexdigest()[:24]
    names=dict(SCHEMA=s,RUNTIME=a['runtime_role'],WORKER=a['worker_role'],
        MAIL='airport_assistant_mail_owner' if s=='app' else 'mail_owner_'+suffix,
        REPLY='airport_assistant_reply_owner' if s=='app' else 'reply_owner_'+suffix,
        GRAPH='source_graph_'+suffix)
    for key,value in names.items():
        if key!='SCHEMA':role_name(value)
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    substitutions={key:quote(value) for key,value in names.items()}
    for role in ('MAIL','REPLY','GRAPH'):
        op.execute(f'GRANT CREATE ON SCHEMA {substitutions["SCHEMA"]} TO {substitutions[role]}')
    for file in ('common','outbound','inbound','history'):
        sql=(Path(__file__).resolve().parent.parent/'sql'/f'0031_{file}.sql').read_text(encoding='utf-8')
        for key,value in substitutions.items():sql=sql.replace('@'+key+'@',value)
        op.get_bind().exec_driver_sql(sql,execution_options={'no_parameters':True})
    # Reuse the reviewed monotone reconciliation engine with a separately
    # callable private metadata port. Public legacy entry points stay closed.
    signatures=[('mail_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)','mail_private_delivery_snapshot(uuid,uuid,uuid,uuid,bigint)'),
        ('mail_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)','mail_private_delivery_observe(uuid,uuid,uuid,uuid,bigint,uuid,bigint,jsonb,text)')]
    for old,new in signatures:
        definition=op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',(s+'.'+old,)).scalar_one()
        definition=definition.replace(old.split('(')[0],new.split('(')[0])
        if old.startswith('mail_delivery_snapshot'):
            guard=f' IF NOT {substitutions["SCHEMA"]}.legacy_source_job_public(t,jid) THEN RETURN NULL;END IF; '
            if definition.count(guard)!=1:raise RuntimeError('Unexpected legacy delivery fence.')
            definition=definition.replace(guard,'',1)
        else:definition=definition.replace('mail_delivery_snapshot','mail_private_delivery_snapshot')
        op.get_bind().exec_driver_sql(definition,execution_options={'no_parameters':True})
        op.execute(f'REVOKE ALL ON FUNCTION {substitutions["SCHEMA"]}.{new} FROM PUBLIC')
        op.execute(f'ALTER FUNCTION {substitutions["SCHEMA"]}.{new} OWNER TO {substitutions["MAIL"]}')
        op.execute(f'GRANT EXECUTE ON FUNCTION {substitutions["SCHEMA"]}.{new} TO {substitutions["WORKER"]}')
    for role in ('MAIL','REPLY','GRAPH'):
        op.execute(f'REVOKE CREATE ON SCHEMA {substitutions["SCHEMA"]} FROM {substitutions[role]}')


def downgrade():raise RuntimeError('Private mail recovery requires a reviewed forward migration.')
