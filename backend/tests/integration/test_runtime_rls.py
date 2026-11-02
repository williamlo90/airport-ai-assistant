"""Live tenant/identity RLS under an actual restricted login, not SET ROLE."""

import asyncio
from pathlib import Path
import os
import secrets
from uuid import uuid4
from urllib.parse import unquote,urlsplit

import pytest
from sqlalchemy import text

from airport_assistant.composition import database_loop_factory
from airport_assistant.database_config import RuntimeDatabaseSettings,MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase,DatabaseError
from airport_assistant.infrastructure.database.migrations import migrate,create_migration_engine
from airport_assistant.infrastructure.database.provisioning import prepare_roles,enable_login,runtime_url
from airport_assistant.modules.identity.context import DatabaseContext

pytestmark=pytest.mark.skipif(os.environ.get("RUN_NEON_RLS_TESTS")!="1",reason="Set RUN_NEON_RLS_TESTS=1 for real restricted-role RLS tests.")


def insert(c,schema,table,**values):
    columns=",".join('"'+key+'"' for key in values)
    params=",".join(':'+key for key in values)
    return c.execute(text(f'INSERT INTO "{schema}"."{table}" ({columns}) VALUES ({params}) RETURNING id'),values).scalar_one()


@pytest.fixture(scope="module")
def target():
    file=Path(os.environ.get("DB_TEST_ENV_FILE",".env"))
    runtime=load_database_settings(RuntimeDatabaseSettings,file)
    direct=load_database_settings(MigrationDatabaseSettings,file)
    suffix=uuid4().hex
    schema="db03_test_"+suffix
    role="db03_runtime_"+suffix
    owner="db03_owner_"+suffix
    password=secrets.token_urlsafe(32)
    engine=create_migration_engine(direct.url.get_secret_value())
    try:
        with engine.begin() as c:
            prepare_roles(c,role,owner)
        assert migrate(direct.url.get_secret_value(),schema=schema,runtime_role=role,policy_role=owner,target_revision="0002_runtime_rls")=="0002_runtime_rls"
        with engine.begin() as c:
            enable_login(c,role,password)
            a=insert(c,schema,"tenants",slug="a",display_name="A",timezone="UTC",supported_languages=["en"])
            b=insert(c,schema,"tenants",slug="b",display_name="B",timezone="UTC",supported_languages=["id"])
            insert(c,schema,"tenant_hosts",tenant_id=a,hostname="a.example.test")
            insert(c,schema,"tenant_hosts",tenant_id=b,hostname="b.example.test")
            org=insert(c,schema,"organizations",tenant_id=a,name="A1")
            org2=insert(c,schema,"organizations",tenant_id=a,name="A2")
            orgb=insert(c,schema,"organizations",tenant_id=b,name="B1")
            pa=insert(c,schema,"principals",tenant_id=a,display_name="Customer A")
            pa2=insert(c,schema,"principals",tenant_id=a,display_name="Customer A2")
            pb=insert(c,schema,"principals",tenant_id=b,display_name="Customer B")
            admin=insert(c,schema,"principals",tenant_id=a,display_name="Admin")
            cs=insert(c,schema,"principals",tenant_id=a,display_name="CS")
            managed=insert(c,schema,"principals",tenant_id=a,display_name="Managed staff")
            admin_m=insert(c,schema,"staff_memberships",tenant_id=a,principal_id=admin,organization_id=org)
            cs_m=insert(c,schema,"staff_memberships",tenant_id=a,principal_id=cs,organization_id=org2)
            managed_m=insert(c,schema,"staff_memberships",tenant_id=a,principal_id=managed,organization_id=org)
            qa=insert(c,schema,"service_queues",tenant_id=a,organization_id=org,service_type="parking",name="A Parking")
            qa2=insert(c,schema,"service_queues",tenant_id=a,organization_id=org2,service_type="lounge",name="A Lounge")
            insert(c,schema,"service_queues",tenant_id=b,organization_id=orgb,service_type="general",name="B General")
            insert(c,schema,"membership_queues",tenant_id=a,membership_id=admin_m,queue_id=qa,organization_id=org)
            insert(c,schema,"membership_queues",tenant_id=a,membership_id=cs_m,queue_id=qa2,organization_id=org2)
            insert(c,schema,"membership_roles",tenant_id=a,membership_id=admin_m,role="admin")
            insert(c,schema,"membership_roles",tenant_id=a,membership_id=cs_m,role="cs")
            for tenant,principal,name in ((a,pa,"a"),(a,pa2,"a2"),(b,pb,"b")):
                insert(c,schema,"contacts",tenant_id=tenant,principal_id=principal,email_address=name+"@example.test",email_lookup_key=name+"@example.test")
        yield dict(schema=schema,engine=engine,url=runtime_url(runtime.url.get_secret_value(),role,password),privileged_url=direct.url.get_secret_value(),
                   role=role,owner=owner,a=a,b=b,pa=pa,pa2=pa2,pb=pb,admin=admin,cs=cs,org=org,org2=org2,
                   admin_m=admin_m,cs_m=cs_m,managed_m=managed_m,qa=qa,qa2=qa2)
    finally:
        assert schema.startswith("db03_test_") and len(schema)==42
        with engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            for name in (role,owner):
                assert name.endswith(suffix)
                c.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        engine.dispose()


def run(target,scenario,pool_max=1):
    async def execute():
        db=RuntimeDatabase(target["url"],pool_max=pool_max,acquire_timeout=5,statement_timeout_ms=3000)
        try:
            await db.open()
            await scenario(db)
        finally:
            await db.close()
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(execute())


def ctx(t,who="pa",kind="customer",tenant="a"):
    return DatabaseContext(t[tenant],t[who],kind)


async def scalar(c,query,params=None):
    return (await (await c.execute(query,params)).fetchone())[0]


def test_real_login_flags_and_all_tables_force_rls(target):
    t=target
    async def scenario(db):
        async with db.transaction() as c:
            assert await scalar(c,"SELECT current_user=session_user")
            assert await scalar(c,"SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolinherit) FROM pg_roles WHERE rolname=current_user")
    run(t,scenario)
    with t["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity"),dict(schema=t["schema"])).scalar_one()==9
        assert c.execute(text("SELECT bool_and(proconfig @> ARRAY['search_path=pg_catalog']) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:schema AND p.prosecdef"),dict(schema=t["schema"])).scalar_one()


def test_missing_context_and_bootstrap_projection(target):
    t=target;s=t["schema"]
    async def scenario(db):
        async with db.transaction() as c:
            for table in ("tenants","tenant_hosts","organizations","service_queues","principals","contacts","staff_memberships","membership_roles","membership_queues"):
                assert await scalar(c,f'SELECT count(*) FROM "{s}"."{table}"')==0
            assert await scalar(c,f'SELECT "{s}".lookup_tenant_host(%s)',("a.example.test",))==t["a"]
            assert await scalar(c,f'SELECT "{s}".lookup_tenant_host(%s)',("missing.example.test",)) is None
            await c.execute("SELECT set_config('app.tenant_id','malformed',true)")
            assert await scalar(c,f'SELECT count(*) FROM "{s}".contacts')==0
    run(t,scenario)


def test_tenant_metadata_and_staff_queue_reads_are_scoped(target):
    t=target;s=t["schema"]
    async def scenario(db):
        async with db.transaction(ctx(t)) as c:
            for table in ("tenant_hosts","organizations","service_queues"):
                rows=await (await c.execute(f'SELECT tenant_id FROM "{s}"."{table}"')).fetchall()
                assert rows and all(row[0]==t["a"] for row in rows)
        async with db.transaction(ctx(t,"cs",kind="staff")) as c:
            assert await scalar(c,f'SELECT queue_id FROM "{s}".membership_queues')==t["qa2"]
            assert await scalar(c,f'SELECT role FROM "{s}".membership_roles')=="cs"
            assert await scalar(c,f'SELECT count(*) FROM "{s}".contacts')==0
    run(t,scenario)


def test_privileged_migration_login_is_rejected_as_runtime(target):
    async def scenario():
        db=RuntimeDatabase(target["privileged_url"])
        try:
            with pytest.raises(DatabaseError):await db.open()
        finally:await db.close()
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(scenario())


def test_cross_tenant_and_same_tenant_customer_isolation(target):
    t=target;s=t["schema"]
    async def scenario(db):
        for context,expected in ((ctx(t),t["pa"]),(ctx(t,"pb",tenant="b"),t["pb"])):
            async with db.transaction(context) as c:
                rows=await (await c.execute(f'SELECT principal_id FROM "{s}".contacts')).fetchall()
                assert rows==[(expected,)]
                assert await scalar(c,f'SELECT count(*) FROM "{s}".contacts WHERE principal_id=%s',(t["pa2"],))==0
    run(t,scenario)


def test_self_update_cannot_modify_other_customer(target):
    t=target;s=t["schema"]
    class Rollback(Exception):pass
    async def scenario(db):
        with pytest.raises(Rollback):
            async with db.transaction(ctx(t)) as c:
                assert (await c.execute(f'UPDATE "{s}".principals SET display_name=%s WHERE id=%s',("Changed",t["pa"]))).rowcount==1
                assert (await c.execute(f'UPDATE "{s}".principals SET display_name=%s WHERE id=%s',("Changed",t["pa2"]))).rowcount==0
                raise Rollback()
    run(t,scenario)


@pytest.mark.parametrize("operation",["source_ref","role_insert","membership_insert","truncate","create_table","create_schema","create_temp","alter_table","set_owner","row_security_off"])
def test_customer_cannot_escalate_or_ddl(target,operation):
    t=target;s=t["schema"]
    async def scenario(db):
        with pytest.raises(DatabaseError) as error:
            async with db.transaction(ctx(t)) as c:
                if operation=="source_ref":await c.execute(f'UPDATE "{s}".principals SET source_subject_ref=\'spoofed\' WHERE id=%s',(t["pa"],))
                elif operation=="role_insert":await c.execute(f'INSERT INTO "{s}".membership_roles(tenant_id,membership_id,role) VALUES(%s,%s,\'admin\')',(t["a"],t["cs_m"]))
                elif operation=="membership_insert":await c.execute(f'INSERT INTO "{s}".staff_memberships(tenant_id,principal_id,organization_id) VALUES(%s,%s,%s)',(t["a"],t["pa"],t["org"]))
                elif operation=="truncate":await c.execute(f'TRUNCATE "{s}".contacts')
                elif operation=="create_table":await c.execute(f'CREATE TABLE "{s}".forbidden(id int)')
                elif operation=="create_schema":await c.execute('CREATE SCHEMA forbidden_db03')
                elif operation=="create_temp":await c.execute('CREATE TEMP TABLE forbidden_db03(id int)')
                elif operation=="alter_table":await c.execute(f'ALTER TABLE "{s}".contacts DISABLE ROW LEVEL SECURITY')
                elif operation=="set_owner":await c.execute(f'SET ROLE "{t["owner"]}"')
                else:
                    await c.execute("SET LOCAL row_security=off")
                    await c.execute(f'SELECT * FROM "{s}".contacts')
        assert error.value.sqlstate=="42501"
    run(t,scenario)


def test_fake_staff_and_wrong_org_cannot_assign_roles(target):
    t=target;s=t["schema"]
    async def scenario(db):
        for context,membership in ((ctx(t,kind="staff"),t["cs_m"]),(ctx(t,"admin",kind="staff"),t["cs_m"]),(ctx(t,"cs",kind="staff"),t["cs_m"])):
            with pytest.raises(DatabaseError) as error:
                async with db.transaction(context) as c:
                    await c.execute(f'INSERT INTO "{s}".membership_roles(tenant_id,membership_id,role) VALUES(%s,%s,\'admin\')',(t["a"],membership))
            assert error.value.sqlstate=="42501"
    run(t,scenario)


def test_real_admin_can_manage_scoped_roles_and_revocation_is_rechecked(target):
    t=target;s=t["schema"]
    class Rollback(Exception):pass
    async def allowed(db):
        with pytest.raises(Rollback):
            async with db.transaction(ctx(t,"admin",kind="staff")) as c:
                await c.execute(f'INSERT INTO "{s}".membership_roles(tenant_id,membership_id,role) VALUES(%s,%s,\'cs\')',(t["a"],t["managed_m"]))
                assert not await scalar(c,f'SELECT "{s}".has_staff_role(%s,ARRAY[\'cs\'])',(t["org"],))
                raise Rollback()
    run(t,allowed)
    with t["engine"].begin() as c:c.execute(text(f'UPDATE "{s}".staff_memberships SET active=false WHERE id=:id'),dict(id=t["admin_m"]))
    try:
        async def denied(db):
            with pytest.raises(DatabaseError) as error:
                async with db.transaction(ctx(t,"admin",kind="staff")) as c:
                    await c.execute(f'INSERT INTO "{s}".membership_roles(tenant_id,membership_id,role) VALUES(%s,%s,\'cs\')',(t["a"],t["managed_m"]))
            assert error.value.sqlstate=="42501"
        run(t,denied)
    finally:
        with t["engine"].begin() as c:c.execute(text(f'UPDATE "{s}".staff_memberships SET active=true WHERE id=:id'),dict(id=t["admin_m"]))


@pytest.mark.parametrize("operation",["self_role_insert","self_queue_insert","self_membership_insert","self_role_update","self_queue_update","self_membership_update","self_role_delete","self_queue_delete","self_membership_delete","retarget_principal","retarget_membership","retarget_org"])
def test_admin_cannot_escalate_self_or_retarget_permission_rows(target,operation):
    t=target;s=t["schema"]
    async def scenario(db):
        if operation in {"self_role_update","self_queue_update","self_membership_update","self_role_delete","self_queue_delete","self_membership_delete"}:
            async with db.transaction(ctx(t,"admin",kind="staff")) as c:
                if operation=="self_role_update":result=await c.execute(f'UPDATE "{s}".membership_roles SET role=\'cs\' WHERE membership_id=%s',(t["admin_m"],))
                elif operation=="self_queue_update":result=await c.execute(f'UPDATE "{s}".membership_queues SET queue_id=queue_id WHERE membership_id=%s',(t["admin_m"],))
                elif operation=="self_membership_update":result=await c.execute(f'UPDATE "{s}".staff_memberships SET active=false WHERE id=%s',(t["admin_m"],))
                elif operation=="self_role_delete":result=await c.execute(f'DELETE FROM "{s}".membership_roles WHERE membership_id=%s',(t["admin_m"],))
                elif operation=="self_queue_delete":result=await c.execute(f'DELETE FROM "{s}".membership_queues WHERE membership_id=%s',(t["admin_m"],))
                else:result=await c.execute(f'DELETE FROM "{s}".staff_memberships WHERE id=%s',(t["admin_m"],))
                assert result.rowcount==0
                assert not await scalar(c,f'SELECT "{s}".has_staff_role(%s,ARRAY[\'cs\'])',(t["org"],))
        else:
            with pytest.raises(DatabaseError) as error:
                async with db.transaction(ctx(t,"admin",kind="staff")) as c:
                    if operation=="self_role_insert":await c.execute(f'INSERT INTO "{s}".membership_roles(tenant_id,membership_id,role) VALUES(%s,%s,\'cs\')',(t["a"],t["admin_m"]))
                    elif operation=="self_queue_insert":await c.execute(f'INSERT INTO "{s}".membership_queues(tenant_id,membership_id,queue_id,organization_id) VALUES(%s,%s,%s,%s)',(t["a"],t["admin_m"],t["qa"],t["org"]))
                    elif operation=="self_membership_insert":await c.execute(f'INSERT INTO "{s}".staff_memberships(tenant_id,principal_id,organization_id) VALUES(%s,%s,%s)',(t["a"],t["admin"],t["org"]))
                    elif operation=="retarget_principal":await c.execute(f'UPDATE "{s}".staff_memberships SET principal_id=%s WHERE id=%s',(t["admin"],t["managed_m"]))
                    elif operation=="retarget_org":await c.execute(f'UPDATE "{s}".staff_memberships SET organization_id=%s WHERE id=%s',(t["org2"],t["managed_m"]))
                    else:
                        await c.execute(f'INSERT INTO "{s}".membership_roles(tenant_id,membership_id,role) VALUES(%s,%s,\'cs\')',(t["a"],t["managed_m"]))
                        await c.execute(f'UPDATE "{s}".membership_roles SET membership_id=%s WHERE membership_id=%s',(t["admin_m"],t["managed_m"]))
            assert error.value.sqlstate=="42501"
    run(t,scenario)


@pytest.mark.parametrize("failure",["commit","rollback","sql_error","cancel","timeout"])
def test_context_cleared_after_every_exit_and_pool_switch(target,failure):
    t=target;s=t["schema"]
    class Rollback(Exception):pass
    async def work(db):
        async def first():
            async with db.transaction(ctx(t)) as c:
                assert await scalar(c,f'SELECT "{s}".current_tenant_id()')==t["a"]
                if failure=="rollback":raise Rollback()
                if failure=="sql_error":await c.execute("SELECT 1/0")
                if failure=="cancel":raise asyncio.CancelledError()
                if failure=="timeout":await c.execute("SELECT pg_sleep(2)")
        if failure=="timeout":
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.1):await first()
        elif failure=="cancel":
            with pytest.raises(asyncio.CancelledError):await first()
        elif failure=="rollback":
            with pytest.raises(Rollback):await first()
        elif failure=="sql_error":
            with pytest.raises(DatabaseError):await first()
        else:await first()
        async with db.transaction() as c:
            assert await scalar(c,f'SELECT "{s}".current_tenant_id()') is None
            assert await scalar(c,f'SELECT "{s}".current_principal_id()') is None
            assert await scalar(c,f'SELECT count(*) FROM "{s}".contacts')==0
        async with db.transaction(ctx(t,"pb",tenant="b")) as c:
            assert await scalar(c,f'SELECT principal_id FROM "{s}".contacts')==t["pb"]
    run(t,work)


def test_concurrent_tenants_through_single_connection_pool(target):
    t=target;s=t["schema"]
    async def scenario(db):
        async def read(context,expected):
            async with db.transaction(context) as c:
                assert await scalar(c,f'SELECT principal_id FROM "{s}".contacts')==expected
        await asyncio.gather(*(read(ctx(t),t["pa"]) if i%2==0 else read(ctx(t,"pb",tenant="b"),t["pb"]) for i in range(6)))
    run(t,scenario)


def test_configured_runtime_credential_enforces_isolation_in_scratch_schema():
    # In addition to generated-role tests, prove the actual deployed credential.
    file=Path(os.environ.get("DB_TEST_ENV_FILE",".env"))
    runtime=load_database_settings(RuntimeDatabaseSettings,file)
    direct=load_database_settings(MigrationDatabaseSettings,file)
    suffix=uuid4().hex
    schema="db03_config_"+suffix
    owner="db03_config_owner_"+suffix
    role=unquote(urlsplit(runtime.url.get_secret_value()).username)
    engine=create_migration_engine(direct.url.get_secret_value())
    try:
        with engine.begin() as c:prepare_roles(c,role,owner)
        migrate(direct.url.get_secret_value(),schema=schema,runtime_role=role,policy_role=owner,target_revision="0002_runtime_rls")
        ids=[]
        with engine.begin() as c:
            for label in ("a","b"):
                tenant=insert(c,schema,"tenants",slug=label,display_name=label,timezone="UTC",supported_languages=["en"])
                principal=insert(c,schema,"principals",tenant_id=tenant)
                insert(c,schema,"contacts",tenant_id=tenant,principal_id=principal,email_address=label+"@example.test",email_lookup_key=label+"@example.test")
                ids.append((tenant,principal))
        async def scenario(db):
            for tenant,principal in ids:
                async with db.transaction(DatabaseContext(tenant,principal,"customer")) as c:
                    assert await scalar(c,f'SELECT principal_id FROM "{schema}".contacts')==principal
                    assert await scalar(c,f'SELECT count(*) FROM "{schema}".contacts')==1
            async with db.transaction() as c:
                assert await scalar(c,f'SELECT count(*) FROM "{schema}".contacts')==0
        run(dict(url=runtime.url.get_secret_value()),scenario)
    finally:
        assert schema=="db03_config_"+suffix
        with engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            c.exec_driver_sql(f'DROP ROLE IF EXISTS "{owner}"')
        engine.dispose()
