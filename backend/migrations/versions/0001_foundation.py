"""Tenant/identity foundation; no business data or runtime access grants."""

from alembic import context, op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    schema = context.config.attributes["schema"]
    q = op.get_bind().dialect.identifier_preparer.quote_identifier(schema)
    common = f"""
        tenant_id uuid NOT NULL REFERENCES {q}.tenants(id) ON DELETE RESTRICT,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, id)
    """
    statements = [
        f"""CREATE TABLE {q}.tenants (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug text NOT NULL UNIQUE CHECK (length(slug) BETWEEN 1 AND 63 AND slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            display_name text NOT NULL CHECK (length(display_name) <= 200 AND length(btrim(display_name)) >= 1),
            timezone text NOT NULL,
            supported_languages text[] NOT NULL CHECK (
                cardinality(supported_languages) > 0
                AND array_ndims(supported_languages) = 1
                AND supported_languages <@ ARRAY['id','en']::text[]
                AND array_position(supported_languages, NULL) IS NULL),
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE FUNCTION {q}.validate_tenant_timezone() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = NEW.timezone) THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invalid IANA timezone';
                END IF;
                RETURN NEW;
            END $$""",
        f"""CREATE TRIGGER validate_timezone BEFORE INSERT OR UPDATE OF timezone ON {q}.tenants
            FOR EACH ROW EXECUTE FUNCTION {q}.validate_tenant_timezone()""",
        f"""CREATE TABLE {q}.tenant_hosts (
            {common},
            hostname text NOT NULL UNIQUE CHECK (length(hostname) BETWEEN 1 AND 253
                AND hostname ~ '^[a-z0-9]([a-z0-9-]{{0,61}}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{{0,61}}[a-z0-9])?)*$'),
            active boolean NOT NULL DEFAULT true
        )""",
        f"""CREATE TABLE {q}.organizations (
            {common}, name text NOT NULL CHECK (length(name) <= 200 AND length(btrim(name)) >= 1),
            active boolean NOT NULL DEFAULT true
        )""",
        f"""CREATE TABLE {q}.service_queues (
            {common}, organization_id uuid NOT NULL,
            service_type text NOT NULL CHECK (service_type IN ('general','parking','lounge')),
            name text NOT NULL CHECK (length(name) <= 200 AND length(btrim(name)) >= 1),
            active boolean NOT NULL DEFAULT true,
            UNIQUE (tenant_id,id,organization_id),
            FOREIGN KEY (tenant_id,organization_id) REFERENCES {q}.organizations(tenant_id,id) ON DELETE RESTRICT
        )""",
        f"""CREATE TABLE {q}.principals (
            {common}, display_name text CHECK (length(display_name) <= 200 AND length(btrim(display_name)) >= 1),
            source_subject_ref text CHECK (length(source_subject_ref) <= 256 AND length(btrim(source_subject_ref)) >= 1),
            active boolean NOT NULL DEFAULT true
        )""",
        f"""CREATE UNIQUE INDEX principals_source_subject_uq ON {q}.principals(tenant_id,source_subject_ref)
            WHERE source_subject_ref IS NOT NULL""",
        f"""CREATE TABLE {q}.contacts (
            {common}, principal_id uuid,
            email_address text NOT NULL CHECK (length(email_address) BETWEEN 3 AND 320
                AND position('@' IN email_address) > 1 AND email_address !~ E'[\\r\\n]'),
            email_lookup_key text NOT NULL CHECK (length(email_lookup_key) BETWEEN 3 AND 320
                AND position('@' IN email_lookup_key) > 1 AND email_lookup_key !~ E'[\\r\\n]'),
            verified_at timestamptz,
            UNIQUE (tenant_id,email_lookup_key),
            CHECK (verified_at IS NULL OR principal_id IS NOT NULL),
            FOREIGN KEY (tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id) ON DELETE RESTRICT
        )""",
        f"""CREATE TABLE {q}.staff_memberships (
            {common}, principal_id uuid NOT NULL, organization_id uuid NOT NULL,
            active boolean NOT NULL DEFAULT true,
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            UNIQUE (tenant_id,principal_id,organization_id),
            UNIQUE (tenant_id,id,organization_id),
            FOREIGN KEY (tenant_id,principal_id) REFERENCES {q}.principals(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id,organization_id) REFERENCES {q}.organizations(tenant_id,id) ON DELETE RESTRICT
        )""",
        f"""CREATE TABLE {q}.membership_roles (
            {common}, membership_id uuid NOT NULL,
            role text NOT NULL CHECK (role IN ('cs','supervisor','admin')),
            UNIQUE (tenant_id,membership_id,role),
            FOREIGN KEY (tenant_id,membership_id) REFERENCES {q}.staff_memberships(tenant_id,id) ON DELETE RESTRICT
        )""",
        f"""CREATE TABLE {q}.membership_queues (
            {common}, membership_id uuid NOT NULL, queue_id uuid NOT NULL, organization_id uuid NOT NULL,
            UNIQUE (tenant_id,membership_id,queue_id),
            FOREIGN KEY (tenant_id,membership_id,organization_id)
                REFERENCES {q}.staff_memberships(tenant_id,id,organization_id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id,queue_id,organization_id)
                REFERENCES {q}.service_queues(tenant_id,id,organization_id) ON DELETE RESTRICT
        )""",
        f"""CREATE FUNCTION {q}.touch_updated_at() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END $$""",
    ]
    for statement in statements:
        op.execute(statement)
    tables = ("tenants", "tenant_hosts", "organizations", "service_queues", "principals", "contacts",
              "staff_memberships", "membership_roles", "membership_queues")
    for table in tables:
        op.execute(f'CREATE TRIGGER touch_updated_at BEFORE UPDATE ON {q}."{table}" FOR EACH ROW EXECUTE FUNCTION {q}.touch_updated_at()')
    indexes = {
        "service_queues": ("organization_id",), "contacts": ("principal_id",),
        "staff_memberships": ("organization_id",), "membership_queues": ("queue_id", "organization_id"),
    }
    for table, fields in indexes.items():
        for field in fields:
            op.execute(f'CREATE INDEX "{table}_{field}_idx" ON {q}."{table}" (tenant_id,"{field}")')
    # No runtime or PUBLIC grants in the foundation migration; DB-03 adds roles/RLS.
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {q} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {q} FROM PUBLIC")


def downgrade():
    raise RuntimeError("Destructive downgrade is not exposed; use a reviewed recovery migration.")
