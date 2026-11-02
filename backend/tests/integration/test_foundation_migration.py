"""Real PostgreSQL constraints in a generated, isolated schema; opt-in only."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from airport_assistant.database_config import MigrationDatabaseSettings, load_database_settings
from airport_assistant.infrastructure.database import migrations

pytestmark = pytest.mark.skipif(os.environ.get("RUN_NEON_MIGRATION_TESTS") != "1",
                               reason="Set RUN_NEON_MIGRATION_TESTS=1 for isolated Neon migration tests.")


@pytest.fixture(scope="module")
def target():
    settings = load_database_settings(MigrationDatabaseSettings, Path(os.environ.get("DB_TEST_ENV_FILE", ".env")))
    schema = "db02_test_" + uuid4().hex
    url = settings.url.get_secret_value()
    engine = migrations.create_migration_engine(url, connect_timeout=settings.connect_timeout)
    try:
        assert migrations.migrate(url, schema=schema, target_revision="0001_foundation") == "0001_foundation"
        yield schema, engine, url
    finally:
        # Exact generated schema only; never delete the application schema.
        assert schema.startswith("db02_test_") and len(schema) == 42
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def insert(connection, schema, table, **values):
    names = ",".join('"' + name + '"' for name in values)
    params = ",".join(":" + name for name in values)
    return connection.execute(text(f'INSERT INTO "{schema}"."{table}" ({names}) VALUES ({params}) RETURNING id'), values).scalar_one()


@pytest.fixture
def data(target):
    schema, engine, _ = target
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            a = insert(connection, schema, "tenants", slug="airport-a", display_name="Airport A", timezone="Asia/Jakarta", supported_languages=["id", "en"])
            b = insert(connection, schema, "tenants", slug="airport-b", display_name="Airport B", timezone="Asia/Singapore", supported_languages=["en"])
            orga = insert(connection, schema, "organizations", tenant_id=a, name="Org A")
            orga2 = insert(connection, schema, "organizations", tenant_id=a, name="Org A2")
            orgb = insert(connection, schema, "organizations", tenant_id=b, name="Org B")
            pa = insert(connection, schema, "principals", tenant_id=a)
            pb = insert(connection, schema, "principals", tenant_id=b)
            qa = insert(connection, schema, "service_queues", tenant_id=a, organization_id=orga, service_type="parking", name="Parking A")
            qa2 = insert(connection, schema, "service_queues", tenant_id=a, organization_id=orga2, service_type="lounge", name="Lounge A2")
            qb = insert(connection, schema, "service_queues", tenant_id=b, organization_id=orgb, service_type="general", name="General B")
            ma = insert(connection, schema, "staff_memberships", tenant_id=a, principal_id=pa, organization_id=orga)
            mb = insert(connection, schema, "staff_memberships", tenant_id=b, principal_id=pb, organization_id=orgb)
            yield connection, schema, dict(a=a,b=b,orga=orga,orga2=orga2,orgb=orgb,pa=pa,pb=pb,qa=qa,qa2=qa2,qb=qb,ma=ma,mb=mb)
        finally:
            transaction.rollback()


def reject(connection, expected, operation):
    with pytest.raises(IntegrityError) as error:
        with connection.begin_nested():
            operation()
    assert error.value.orig.sqlstate == expected


def test_empty_upgrade_and_repeat_are_versioned(target):
    schema, engine, url = target
    assert migrations.migrate(url, schema=schema, target_revision="0001_foundation") == "0001_foundation"
    assert migrations.migrate(url, schema=schema, action="current") == "0001_foundation"
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema=:schema"), {"schema": schema}).scalars().all()
        assert set(rows) == {"tenants", "tenant_hosts", "organizations", "service_queues", "principals", "contacts", "staff_memberships", "membership_roles", "membership_queues", "schema_migrations"}


@pytest.mark.parametrize("table,values", [
    ("service_queues", dict(tenant_id="a",organization_id="orgb",service_type="general",name="Invalid")),
    ("contacts", dict(tenant_id="a",principal_id="pb",email_address="x@example.test",email_lookup_key="x@example.test")),
    ("staff_memberships", dict(tenant_id="a",principal_id="pb",organization_id="orga")),
    ("staff_memberships", dict(tenant_id="a",principal_id="pa",organization_id="orgb")),
    ("membership_roles", dict(tenant_id="a",membership_id="mb",role="cs")),
    ("membership_queues", dict(tenant_id="a",membership_id="ma",queue_id="qb",organization_id="orga")),
    ("membership_queues", dict(tenant_id="a",membership_id="mb",queue_id="qa",organization_id="orga")),
    ("membership_queues", dict(tenant_id="a",membership_id="ma",queue_id="qa2",organization_id="orga")),
    ("membership_queues", dict(tenant_id="a",membership_id="ma",queue_id="qa2",organization_id="orga2")),
])
def test_cross_tenant_and_organization_links_rejected(data, table, values):
    connection, schema, ids = data
    values = {key: ids.get(value, value) for key,value in values.items()}
    reject(connection, "23503", lambda: insert(connection,schema,table,**values))


@pytest.mark.parametrize("table,values", [
    ("tenant_hosts", dict(tenant_id="a",hostname="Upper.EXAMPLE")),
    ("organizations", dict(tenant_id="a",name=" " * 201 + "X")),
    ("tenant_hosts", dict(tenant_id="a",hostname="example.test:443")),
    ("tenant_hosts", dict(tenant_id="a",hostname="-invalid.test")),
    ("service_queues", dict(tenant_id="a",organization_id="orga",name="Invalid",service_type="airport")),
    ("membership_roles", dict(tenant_id="a",membership_id="ma",role="owner")),
    ("staff_memberships", dict(tenant_id="a",principal_id="pa",organization_id="orga2",version=0)),
    ("contacts", dict(tenant_id="a",email_address="x@example.test",email_lookup_key="x@example.test",verified_at="2026-11-02T00:00:00Z")),
])
def test_field_checks(data, table, values):
    connection, schema, ids = data
    values = {key: ids.get(value, value) for key,value in values.items()}
    reject(connection, "23514", lambda: insert(connection,schema,table,**values))


@pytest.mark.parametrize("languages", [[], ["fr"], ["id", None], [["id", "en"]]])
def test_language_allowlist(data,languages):
    connection,schema,_=data
    reject(connection,"23514",lambda: insert(connection,schema,"tenants",slug="invalid",display_name="Invalid",timezone="UTC",supported_languages=languages))


def test_timezone_is_validated(data):
    connection,schema,_=data
    reject(connection,"23514",lambda: insert(connection,schema,"tenants",slug="invalid",display_name="Invalid",timezone="Invented/Timezone",supported_languages=["en"]))


def test_global_hostname_unique_and_tenant_email_scope(data):
    connection,schema,ids=data
    insert(connection,schema,"tenant_hosts",tenant_id=ids["a"],hostname="airport.example.test")
    reject(connection,"23505",lambda: insert(connection,schema,"tenant_hosts",tenant_id=ids["b"],hostname="airport.example.test"))
    for tenant in (ids["a"],ids["b"]):
        insert(connection,schema,"contacts",tenant_id=tenant,email_address="User+one@example.test",email_lookup_key="User+one@example.test")
    reject(connection,"23505",lambda: insert(connection,schema,"contacts",tenant_id=ids["a"],email_address="User+one@example.test",email_lookup_key="User+one@example.test"))
    insert(connection,schema,"contacts",tenant_id=ids["a"],email_address="User+two@example.test",email_lookup_key="User+two@example.test")


def test_source_subject_partial_uniqueness_and_composite_primary_key(data):
    connection,schema,ids=data
    common=uuid4()
    for tenant in (ids["a"],ids["b"]):
        insert(connection,schema,"principals",tenant_id=tenant,id=common,source_subject_ref="source-1")
    reject(connection,"23505",lambda: insert(connection,schema,"principals",tenant_id=ids["a"],id=common))
    reject(connection,"23505",lambda: insert(connection,schema,"principals",tenant_id=ids["a"],source_subject_ref="source-1"))


def test_roles_queues_and_restrict_parent_updates(data):
    connection,schema,ids=data
    for role in ("admin","cs","supervisor"):
        insert(connection,schema,"membership_roles",tenant_id=ids["a"],membership_id=ids["ma"],role=role)
    reject(connection,"23505",lambda: insert(connection,schema,"membership_roles",tenant_id=ids["a"],membership_id=ids["ma"],role="cs"))
    insert(connection,schema,"membership_queues",tenant_id=ids["a"],membership_id=ids["ma"],queue_id=ids["qa"],organization_id=ids["orga"])
    reject(connection,"23505",lambda: insert(connection,schema,"membership_queues",tenant_id=ids["a"],membership_id=ids["ma"],queue_id=ids["qa"],organization_id=ids["orga"]))
    reject(connection,"23503",lambda: connection.execute(text(f'UPDATE "{schema}".service_queues SET organization_id=:org WHERE tenant_id=:tenant AND id=:queue'),dict(org=ids["orga2"],tenant=ids["a"],queue=ids["qa"])))
    reject(connection,"23001",lambda: connection.execute(text(f'DELETE FROM "{schema}".principals WHERE tenant_id=:tenant AND id=:principal'),dict(tenant=ids["a"],principal=ids["pa"])))


def test_timestamp_default_and_update_trigger(data):
    connection,schema,ids=data
    row=connection.execute(text(f'UPDATE "{schema}".principals SET display_name=\'Updated\', updated_at=\'1900-01-01\' WHERE tenant_id=:tenant AND id=:id RETURNING created_at,updated_at'),dict(tenant=ids["a"],id=ids["pa"])).one()
    assert row.created_at.tzinfo is not None
    assert row.updated_at.year > 1900


def test_failed_revision_leaves_no_partial_schema(target,monkeypatch):
    _,engine,url=target
    schema="db02_test_"+uuid4().hex
    def fail(config,revision):
        config.attributes["connection"].exec_driver_sql(f'CREATE TABLE "{schema}".partial_failure (id int)')
        raise RuntimeError("controlled revision failure")
    monkeypatch.setattr(migrations.command,"upgrade",fail)
    with pytest.raises(migrations.MigrationError):
        migrations.migrate(url,schema=schema)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_namespace WHERE nspname=:schema"),dict(schema=schema)).scalar_one()==0
