"""Keep email-only SQL out of identity adoption's restricted execution role."""
from alembic import context, op
import hashlib

revision = '0023_adoption_guard'
down_revision = '0022_staff_queue'
branch_labels = None
depends_on = None


def rewrite(definition, schema, reply_owner):
    literal = "'" + reply_owner.replace("'", "''") + "'"
    predicate = f"EXISTS(SELECT 1 FROM {schema}.case_history h WHERE h.tenant_id=NEW.tenant_id AND h.case_id=NEW.id AND h.resulting_version=NEW.version AND h.email_source_id IS NOT NULL)"
    old = f"BEGIN IF TG_TABLE_NAME='cases' THEN IF current_user={literal} AND {predicate} THEN RETURN NULL;END IF;END IF;"
    new = f"BEGIN IF TG_TABLE_NAME='cases' THEN IF current_user={literal} THEN IF {predicate} THEN RETURN NULL;END IF;END IF;END IF;"
    if definition.count(old) != 1:
        raise RuntimeError('Unexpected chat transition guard; adoption fix aborted.')
    return definition.replace(old, new, 1)


def upgrade():
    attrs = context.config.attributes
    schema = op.get_bind().dialect.identifier_preparer.quote_identifier(attrs['schema'])
    definition = op.get_bind().exec_driver_sql('SELECT pg_get_functiondef(%s::regprocedure)',
        (attrs['schema'] + '.validate_chat_transition()',)).scalar_one()
    # SQL AND may plan the email-column subquery before checking the role.
    # Separate PL/pgSQL branches avoid that query entirely for the auth owner.
    # CREATE OR REPLACE preserves invoker mode, ownership, ACL and all other guards.
    reply_owner = 'airport_assistant_reply_owner' if attrs['schema'] == 'app' else 'reply_owner_' + hashlib.sha256(attrs['schema'].encode()).hexdigest()[:24]
    op.execute(rewrite(definition, schema, reply_owner))


def downgrade():
    raise RuntimeError('Adoption guard recovery requires a reviewed forward migration.')
