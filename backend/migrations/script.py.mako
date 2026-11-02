${repr(message)}

from alembic import context, op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade():
    # Freeze this revision's DDL here; do not import mutable application models.
    # See docs/backend/migration_workflow.md for PK/FK/index/RLS and expand/contract.
    raise NotImplementedError("Complete feature DDL, constraints, grants and RLS before applying this revision.")


def downgrade():
    raise RuntimeError("Destructive downgrade requires a reviewed recovery migration.")
