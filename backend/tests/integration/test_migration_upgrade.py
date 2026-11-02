"""Populated upgrades and future-table recipes, never applied to app."""

import asyncio
from pathlib import Path
import os
import secrets
import shutil
from uuid import uuid4

import pytest
from sqlalchemy import text

from airport_assistant.composition import database_loop_factory
from airport_assistant.database_config import RuntimeDatabaseSettings,MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase,DatabaseError
from airport_assistant.infrastructure.database import migrations
from airport_assistant.infrastructure.database.provisioning import prepare_roles,enable_login,runtime_url
from airport_assistant.modules.identity.context import DatabaseContext

pytestmark=pytest.mark.skipif(os.environ.get("RUN_NEON_UPGRADE_TESTS")!="1",reason="Set RUN_NEON_UPGRADE_TESTS=1 for populated upgrade tests.")

TABLES=("tenants","tenant_hosts","organizations","service_queues","principals","contacts","staff_memberships","membership_roles","membership_queues")


def insert(c,schema,table,**values):
    columns=",".join('"'+key+'"' for key in values)
    params=",".join(':'+key for key in values)
    return c.execute(text(f'INSERT INTO "{schema}"."{table}" ({columns}) VALUES ({params}) RETURNING id'),values).scalar_one()


def snapshot(engine,schema):
    with engine.connect() as c:
        return {table:c.execute(text(f'SELECT to_jsonb(t) FROM "{schema}"."{table}" t ORDER BY id')).scalars().all() for table in TABLES}


@pytest.fixture
def target():
    file=Path(os.environ.get("DB_TEST_ENV_FILE",".env"))
    runtime=load_database_settings(RuntimeDatabaseSettings,file)
    direct=load_database_settings(MigrationDatabaseSettings,file)
    suffix=uuid4().hex;schema="db04_test_"+suffix
    role="db04_runtime_"+suffix;owner="db04_owner_"+suffix
    password=secrets.token_urlsafe(32)
    url=direct.url.get_secret_value();engine=migrations.create_migration_engine(url)
    try:
        with engine.begin() as c:prepare_roles(c,role,owner)
        assert migrations.migrate(url,schema=schema,target_revision="0001_foundation")=="0001_foundation"
        contexts=[]
        with engine.begin() as c:
            enable_login(c,role,password)
            for label in ("a","b"):
                tenant=insert(c,schema,"tenants",slug=label,display_name=label,timezone="UTC",supported_languages=["en"])
                insert(c,schema,"tenant_hosts",tenant_id=tenant,hostname=label+".example.test")
                org=insert(c,schema,"organizations",tenant_id=tenant,name=label)
                queue=insert(c,schema,"service_queues",tenant_id=tenant,organization_id=org,service_type="parking",name=label)
                principal=insert(c,schema,"principals",tenant_id=tenant,display_name="Existing "+label)
                insert(c,schema,"contacts",tenant_id=tenant,principal_id=principal,email_address=label+"@example.test",email_lookup_key=label+"@example.test")
                member=insert(c,schema,"staff_memberships",tenant_id=tenant,principal_id=principal,organization_id=org)
                for staff_role in ("admin","cs"):
                    insert(c,schema,"membership_roles",tenant_id=tenant,membership_id=member,role=staff_role)
                insert(c,schema,"membership_queues",tenant_id=tenant,membership_id=member,queue_id=queue,organization_id=org)
                contexts.append(DatabaseContext(tenant,principal,"customer"))
        before=snapshot(engine,schema)
        yield dict(schema=schema,role=role,owner=owner,url=url,engine=engine,
                   runtime_url=runtime_url(runtime.url.get_secret_value(),role,password),contexts=contexts,before=before)
    finally:
        assert schema.startswith("db04_test_") and len(schema)==42
        with engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            for name in (role,owner):
                assert name.endswith(suffix)
                c.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        engine.dispose()


def upgrade(t):
    return migrations.migrate(t["url"],schema=t["schema"],runtime_role=t["role"],policy_role=t["owner"])


def runtime(t,scenario):
    async def run():
        db=RuntimeDatabase(t["runtime_url"],pool_max=1)
        try:
            await db.open();await scenario(db)
        finally:await db.close()
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(run())


def future_directory(tmp_path,monkeypatch,fixture=None,body=None):
    directory=tmp_path/"migrations"
    shutil.copytree(migrations.migration_directory(),directory)
    if fixture:
        shutil.copyfile(Path(__file__).parents[1]/"fixtures/migrations"/fixture,directory/"versions"/fixture)
    else:
        source='''from alembic import context,op
revision="0003_blocked"
down_revision="0002_runtime_rls"
branch_labels=None
depends_on=None
def upgrade():
    attrs=context.config.attributes
    quote=op.get_bind().dialect.identifier_preparer.quote_identifier
    q=quote(attrs["schema"])
    role=quote(attrs["runtime_role"])
'''
        source+='\n'.join('    '+line for line in body.splitlines())+'\n'
        (directory/"versions/0003_blocked.py").write_text(source,encoding="utf-8")
    monkeypatch.setattr(migrations,"migration_directory",lambda:directory)


def test_populated_upgrade_preserves_all_rows_and_legacy_read_projection(target):
    t=target;s=t["schema"]
    assert upgrade(t)=="0002_runtime_rls"
    assert snapshot(t["engine"],s)==t["before"]
    assert upgrade(t)=="0002_runtime_rls"
    async def scenario(db):
        for context in t["contexts"]:
            async with db.transaction(context) as c:
                row=await (await c.execute(f'SELECT id,display_name FROM "{s}".principals')).fetchone()
                assert row[0]==context.principal_id and row[1].startswith("Existing ")
        async with db.transaction() as c:
            assert await (await c.execute(f'SELECT id,display_name FROM "{s}".principals')).fetchall()==[]
    runtime(t,scenario)


def test_expand_and_cyclic_fk_remain_tenant_isolated(target,tmp_path,monkeypatch):
    t=target;s=t["schema"];upgrade(t)
    future_directory(tmp_path,monkeypatch,fixture="0003_expand_cycle.py")
    assert upgrade(t)=="0003_expand_cycle"
    assert upgrade(t)=="0003_expand_cycle"
    async def scenario(db):
        a,b=t["contexts"];parent,child=uuid4(),uuid4()
        async with db.transaction(a) as c:
            row=await (await c.execute(f'SELECT id,display_name,migration_test_note FROM "{s}".principals')).fetchone()
            assert row[0]==a.principal_id and row[2] is None
            await c.execute(f'SET CONSTRAINTS "{s}".upgrade_parent_link_fk,"{s}".upgrade_child_link_fk DEFERRED')
            await c.execute(f'INSERT INTO "{s}".upgrade_parent(tenant_id,id,principal_id,child_id) VALUES(%s,%s,%s,%s)',(a.tenant_id,parent,a.principal_id,child))
            await c.execute(f'INSERT INTO "{s}".upgrade_child(tenant_id,id,principal_id,parent_id) VALUES(%s,%s,%s,%s)',(a.tenant_id,child,a.principal_id,parent))
        async with db.transaction(b) as c:
            assert (await (await c.execute(f'SELECT count(*) FROM "{s}".upgrade_parent')).fetchone())[0]==0
        with pytest.raises(DatabaseError) as error:
            async with db.transaction(b) as c:
                await c.execute(f'SET CONSTRAINTS "{s}".upgrade_parent_link_fk,"{s}".upgrade_child_link_fk DEFERRED')
                await c.execute(f'INSERT INTO "{s}".upgrade_child(tenant_id,id,principal_id,parent_id) VALUES(%s,%s,%s,%s)',(b.tenant_id,uuid4(),b.principal_id,parent))
        assert error.value.sqlstate=="23503"
        with pytest.raises(DatabaseError) as error:
            async with db.transaction(b) as c:
                await c.execute(f'INSERT INTO "{s}".upgrade_parent(tenant_id,id,principal_id,child_id) VALUES(%s,%s,%s,%s)',(a.tenant_id,uuid4(),a.principal_id,child))
        assert error.value.sqlstate=="42501"
    runtime(t,scenario)


@pytest.mark.parametrize("failure",["exception","no_rls","no_force","runtime_owner"])
def test_failed_or_unsafe_future_upgrade_rolls_back_rows_schema_and_version(target,tmp_path,monkeypatch,failure):
    t=target;s=t["schema"];upgrade(t)
    before=snapshot(t["engine"],s)
    body='op.execute(f"ALTER TABLE {q}.principals ADD COLUMN should_rollback text")\n'
    body+='op.execute(f"UPDATE {q}.principals SET display_name=\'must rollback\'")\n'
    body+=dict(
        exception='raise RuntimeError("controlled future revision failure")',
        no_rls='op.execute(f"CREATE TABLE {q}.unsafe_table(id uuid)")',
        no_force='op.execute(f"ALTER TABLE {q}.principals NO FORCE ROW LEVEL SECURITY")',
        runtime_owner='op.execute(f"GRANT CREATE ON SCHEMA {q} TO {role}")\ncurrent=quote(op.get_bind().exec_driver_sql("SELECT current_user").scalar_one())\nop.execute(f"GRANT {role} TO {current}")\nop.execute(f"ALTER TABLE {q}.principals OWNER TO {role}")',
    )[failure]
    future_directory(tmp_path,monkeypatch,body=body)
    checked=[]
    original=migrations.verify_row_security
    def observe(*args,**kwargs):
        checked.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(migrations,"verify_row_security",observe)
    with pytest.raises(migrations.MigrationError):upgrade(t)
    assert bool(checked)==(failure!="exception")
    assert migrations.migrate(t["url"],schema=s,action="current")=="0002_runtime_rls"
    assert snapshot(t["engine"],s)==before
    with t["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=:s AND column_name='should_rollback'"),dict(s=s)).scalar_one()==0
        assert not c.execute(text("SELECT has_schema_privilege(:role,:s,'CREATE')"),dict(role=t["role"],s=s)).scalar_one()
