"""Test-only expand/cyclic-FK example. Never a production product revision."""

from alembic import context,op

revision="0003_expand_cycle"
down_revision="0002_runtime_rls"
branch_labels=None
depends_on=None


def upgrade():
    attrs=context.config.attributes
    schema=attrs["schema"]
    if not schema.startswith("db04_test_"):
        raise RuntimeError("This demonstration is restricted to DB-04 test schemas.")
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q=quote(schema);role=quote(attrs["runtime_role"])
    op.execute(f"ALTER TABLE {q}.principals ADD COLUMN migration_test_note text")
    for table,other in (("upgrade_parent","child"),("upgrade_child","parent")):
        op.execute(f"""CREATE TABLE {q}.{table} (
            tenant_id uuid NOT NULL REFERENCES {q}.tenants(id) ON DELETE RESTRICT,
            id uuid NOT NULL,principal_id uuid NOT NULL,{other}_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,id,principal_id),
            FOREIGN KEY(tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id) ON DELETE RESTRICT)""")
    # Both tables exist before either cyclic FK is installed. Only FK checks
    # may be deferred in the data transaction; RLS stays FORCE+ENABLE throughout.
    for table,other in (("upgrade_parent","child"),("upgrade_child","parent")):
        op.execute(f"""ALTER TABLE {q}.{table} ADD CONSTRAINT {table}_link_fk
            FOREIGN KEY(tenant_id,{other}_id,principal_id)
            REFERENCES {q}.upgrade_{other}(tenant_id,id,principal_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE""")
        op.execute(f"CREATE INDEX {table}_link_idx ON {q}.{table}(tenant_id,{other}_id)")
        op.execute(f"ALTER TABLE {q}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {q}.{table} FORCE ROW LEVEL SECURITY")
        condition=f"tenant_id={q}.current_tenant_id() AND principal_id={q}.current_principal_id()"
        op.execute(f"CREATE POLICY scoped_data ON {q}.{table} TO {role} USING({condition}) WITH CHECK({condition})")
        op.execute(f"REVOKE ALL ON {q}.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT ON {q}.{table} TO {role}")


def downgrade():
    raise RuntimeError("Test-only example; cleanup drops its exact isolated schema.")
