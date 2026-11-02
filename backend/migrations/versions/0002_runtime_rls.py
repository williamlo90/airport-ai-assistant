"""Restricted runtime privileges and tenant/identity row policies."""

from alembic import context, op

revision = "0002_runtime_rls"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    attrs = context.config.attributes
    quote = op.get_bind().dialect.identifier_preparer.quote_identifier
    q, runtime, owner = (quote(attrs[key]) for key in ("schema", "runtime_role", "policy_role"))
    tables = ("tenants", "tenant_hosts", "organizations", "service_queues", "principals", "contacts",
              "staff_memberships", "membership_roles", "membership_queues")
    for table in tables:
        op.execute(f'ALTER TABLE {q}."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {q}."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(f"GRANT USAGE ON SCHEMA {q} TO {runtime}, {owner}")
    # This NOLOGIN role can only service small, explicitly projected definer functions.
    for table in ("tenants", "tenant_hosts", "principals", "staff_memberships", "membership_roles"):
        op.execute(f'GRANT SELECT ON {q}."{table}" TO {owner}')
        op.execute(f'CREATE POLICY policy_owner_read ON {q}."{table}" FOR SELECT TO {owner} USING (true)')
    for name, setting in (("current_tenant_id", "app.tenant_id"), ("current_principal_id", "app.principal_id")):
        actor_check = " AND current_setting('app.actor_kind',true) IN ('customer','staff')" if name == "current_principal_id" else ""
        op.execute(f"""CREATE FUNCTION {q}.{name}() RETURNS uuid LANGUAGE sql STABLE
            SET search_path=pg_catalog AS $$
            SELECT CASE WHEN current_setting('{setting}',true) ~
                '^[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'
                {actor_check}
                THEN current_setting('{setting}',true)::uuid ELSE NULL END $$""")
    op.execute(f"""CREATE FUNCTION {q}.current_actor_kind() RETURNS text LANGUAGE sql STABLE
        SET search_path=pg_catalog AS $$ SELECT CASE WHEN current_setting('app.actor_kind',true)
        IN ('customer','staff') THEN current_setting('app.actor_kind',true) ELSE 'anonymous' END $$""")
    op.execute(f"""CREATE FUNCTION {q}.has_staff_role(org uuid, required_roles text[]) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        SELECT {q}.current_actor_kind()='staff' AND EXISTS (
            SELECT 1 FROM {q}.staff_memberships m
            JOIN {q}.principals p ON (p.tenant_id,p.id)=(m.tenant_id,m.principal_id)
            JOIN {q}.membership_roles r ON (r.tenant_id,r.membership_id)=(m.tenant_id,m.id)
            WHERE m.tenant_id={q}.current_tenant_id() AND m.principal_id={q}.current_principal_id()
              AND m.active AND p.active AND (org IS NULL OR org=m.organization_id)
              AND r.role=ANY(required_roles)) $$""")
    for name, condition in (
        ("can_read_membership", f"m.principal_id={q}.current_principal_id() OR {q}.has_staff_role(m.organization_id,ARRAY['admin'])"),
        ("can_manage_membership", f"m.principal_id<>{q}.current_principal_id() AND {q}.has_staff_role(m.organization_id,ARRAY['admin'])"),
    ):
        op.execute(f"""CREATE FUNCTION {q}.{name}(membership uuid) RETURNS boolean LANGUAGE sql STABLE
            SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT EXISTS (SELECT 1 FROM {q}.staff_memberships m
                WHERE m.tenant_id={q}.current_tenant_id() AND m.id=membership AND ({condition})) $$""")
    op.execute(f"""CREATE FUNCTION {q}.lookup_tenant_host(host text) RETURNS uuid LANGUAGE sql STABLE
        SECURITY DEFINER SET search_path=pg_catalog AS $$
        SELECT t.id FROM {q}.tenants t JOIN {q}.tenant_hosts h ON h.tenant_id=t.id
        WHERE h.hostname=host AND h.active AND t.active $$""")
    signatures = ("current_tenant_id()", "current_principal_id()", "current_actor_kind()",
                  "has_staff_role(uuid,text[])", "can_read_membership(uuid)", "can_manage_membership(uuid)",
                  "lookup_tenant_host(text)")
    op.execute(f"GRANT CREATE ON SCHEMA {q} TO {owner}")
    for signature in signatures:
        op.execute(f"REVOKE ALL ON FUNCTION {q}.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {q}.{signature} TO {runtime}, {owner}")
        if signature.startswith(("has_staff", "can_", "lookup_")):
            op.execute(f"ALTER FUNCTION {q}.{signature} OWNER TO {owner}")
    op.execute(f"REVOKE CREATE ON SCHEMA {q} FROM {owner}")
    tenant = f"tenant_id={q}.current_tenant_id()"
    rules = {
        "tenants": f"id={q}.current_tenant_id()",
        "tenant_hosts": tenant,
        "organizations": tenant,
        "service_queues": tenant,
        "principals": f"{tenant} AND id={q}.current_principal_id()",
        "contacts": f"{tenant} AND principal_id={q}.current_principal_id()",
        "staff_memberships": f"{tenant} AND (principal_id={q}.current_principal_id() OR {q}.has_staff_role(organization_id,ARRAY['admin']))",
        "membership_roles": f"{tenant} AND {q}.can_read_membership(membership_id)",
        "membership_queues": f"{tenant} AND {q}.can_read_membership(membership_id)",
    }
    for table, condition in rules.items():
        op.execute(f'GRANT SELECT ON {q}."{table}" TO {runtime}')
        op.execute(f'CREATE POLICY runtime_read ON {q}."{table}" FOR SELECT TO {runtime} USING ({condition})')
    op.execute(f"GRANT UPDATE(display_name) ON {q}.principals TO {runtime}")
    op.execute(f"""CREATE POLICY runtime_self_update ON {q}.principals FOR UPDATE TO {runtime}
        USING ({rules['principals']}) WITH CHECK ({rules['principals']})""")
    for table in ("staff_memberships", "membership_roles", "membership_queues"):
        op.execute(f'GRANT INSERT, DELETE ON {q}."{table}" TO {runtime}')
        columns={"staff_memberships":"active,version","membership_roles":"role","membership_queues":"queue_id"}[table]
        op.execute(f'GRANT UPDATE({columns}) ON {q}."{table}" TO {runtime}')
        condition = (f"{tenant} AND principal_id<>{q}.current_principal_id() AND {q}.has_staff_role(organization_id,ARRAY['admin'])" if table == "staff_memberships"
                     else f"{tenant} AND {q}.can_manage_membership(membership_id)")
        for action in ("INSERT", "UPDATE", "DELETE"):
            using = f"USING ({condition})" if action != "INSERT" else ""
            check = f"WITH CHECK ({condition})" if action != "DELETE" else ""
            op.execute(f'CREATE POLICY runtime_{action.lower()} ON {q}."{table}" FOR {action} TO {runtime} {using} {check}')


def downgrade():
    raise RuntimeError("Security downgrade requires a reviewed recovery migration.")
