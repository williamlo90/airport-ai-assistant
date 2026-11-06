"""Read-only bounded API capability for current authentication mail routing."""
import hashlib
from alembic import context,op

revision='0019_auth_mail'
down_revision='0018_email_replies'
branch_labels=None
depends_on=None


def upgrade():
    a=context.config.attributes;s=a['schema'];quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    name='airport_assistant_mail_owner' if s=='app' else 'mail_owner_'+hashlib.sha256(s.encode()).hexdigest()[:24]
    q,owner,runtime=(quote(v) for v in (s,name,a['runtime_role']))
    op.execute(f'GRANT CREATE ON SCHEMA {q} TO {owner}')
    signature='auth_mail_route_allowed(uuid,uuid,text,text,text)'
    op.execute(f"""CREATE FUNCTION {q}.auth_mail_route_allowed(t uuid,aid uuid,ref text,sender text,reply text) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
      SELECT EXISTS(SELECT 1 FROM {q}.email_accounts a JOIN {q}.tenants tn ON tn.id=a.tenant_id AND tn.active
        WHERE a.tenant_id=t AND a.id=aid AND a.active AND a.provider='resend' AND a.provider_account_ref=ref AND a.sending_identity=sender AND a.reply_address=reply)
      $$""")
    op.execute(f'REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC')
    op.execute(f'ALTER FUNCTION {q}.{signature} OWNER TO {owner}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}')
    op.execute(f'REVOKE CREATE ON SCHEMA {q} FROM {owner}')


def downgrade():raise RuntimeError('Authentication mail needs a reviewed recovery migration.')
