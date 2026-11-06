"""Restricted staff authentication, live membership scope and provisioning tests."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import os
import secrets
from uuid import UUID,uuid4

from httpx import ASGITransport,AsyncClient
import pytest
from sqlalchemy import text

from airport_assistant.api.auth import SESSION_COOKIE,CSRF_COOKIE
from airport_assistant.auth_config import AuthSettings
from airport_assistant.composition import ApplicationServices,database_loop_factory
from airport_assistant.config import Settings
from airport_assistant.database_config import RuntimeDatabaseSettings,MigrationDatabaseSettings,load_database_settings
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository
from airport_assistant.infrastructure.database.connections import RuntimeDatabase,DatabaseError
from airport_assistant.infrastructure.database.migrations import create_migration_engine,migrate
from airport_assistant.infrastructure.database.provisioning import prepare_roles,prepare_auth_owner,enable_login,runtime_url
from airport_assistant.main import create_app
from airport_assistant.modules.identity.auth import AuthService,AuthPolicy
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.system.health import ProcessHealthService

pytestmark=pytest.mark.skipif(os.environ.get("RUN_NEON_AUTH_TESTS")!="1",reason="Set RUN_NEON_AUTH_TESTS=1 for staff auth DB/API integration.")


def insert(c,schema,table,**values):
    columns=",".join('"'+key+'"' for key in values);params=",".join(':'+key for key in values)
    return c.execute(text(f'INSERT INTO "{schema}"."{table}" ({columns}) VALUES ({params}) RETURNING id'),values).scalar_one()


@pytest.fixture(scope="module")
def target():
    file=Path(os.environ.get("DB_TEST_ENV_FILE",".env"))
    runtime=load_database_settings(RuntimeDatabaseSettings,file);direct=load_database_settings(MigrationDatabaseSettings,file)
    suffix=uuid4().hex;schema="auth03_test_"+suffix
    role="auth03_runtime_"+suffix;policy="auth03_policy_"+suffix;owner="auth03_owner_"+suffix
    password=secrets.token_urlsafe(32);key=secrets.token_urlsafe(48)
    engine=create_migration_engine(direct.url.get_secret_value())
    try:
        with engine.begin() as c:prepare_roles(c,role,policy);prepare_auth_owner(c,owner)
        assert migrate(direct.url.get_secret_value(),schema=schema,runtime_role=role,policy_role=policy,auth_role=owner,target_revision="0004_staff_auth")=="0004_staff_auth"
        with engine.begin() as c:
            enable_login(c,role,password)
            tenants=[]
            for label in ("a","b"):
                tenant=insert(c,schema,"tenants",slug=label,display_name=label,timezone="UTC",supported_languages=["id","en"])
                insert(c,schema,"tenant_hosts",tenant_id=tenant,hostname=label+".example.test")
                tenants.append(tenant)
            epoch=c.execute(text("SELECT extract(epoch FROM clock_timestamp())::bigint")).scalar_one()
            window=next(w for w in range(900,3601) if w-epoch%w>300)
        yield dict(schema=schema,role=role,owner=owner,policy=policy,engine=engine,
                   url=runtime_url(runtime.url.get_secret_value(),role,password),key=key,a=tenants[0],b=tenants[1],window=window)
    finally:
        assert schema=="auth03_test_"+suffix
        with engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            for name in (role,policy,owner):
                assert name.endswith(suffix)
                c.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        engine.dispose()


from datetime import datetime, timezone
from airport_assistant.infrastructure.database.staff_repository import PostgresStaffRepository
from airport_assistant.infrastructure.database.staff_provisioning import set_staff_credential, disable_staff_credential
from airport_assistant.infrastructure.security.passwords import hash_password, BoundedPasswordVerifier
from airport_assistant.modules.identity.staff import StaffService, StaffPolicy
from airport_assistant.modules.identity.access import AccessService
from airport_assistant.modules.identity.auth import AuthError

PASSWORD = "Staff test passphrase 2026!"


def run(coroutine):
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:
        return runner.run(coroutine)


def row(target, query, **params):
    with target["engine"].connect() as c:
        return c.execute(text(query.format(s='"'+target["schema"]+'"')), params).first()


def change(target, query, **params):
    with target["engine"].begin() as c:
        c.execute(text(query.format(s='"'+target["schema"]+'"')), params)


def seed(target, roles=("cs",), *, tenant=None, verified=True, credential=True):
    tenant=tenant or target["a"]
    email="staff"+uuid4().hex+"@example.com"
    with target["engine"].begin() as c:
        s=target["schema"]
        org=insert(c,s,"organizations",tenant_id=tenant,name="Test organization")
        queue=insert(c,s,"service_queues",tenant_id=tenant,organization_id=org,service_type="parking",name="Test queue")
        principal=insert(c,s,"principals",tenant_id=tenant)
        contact=insert(c,s,"contacts",tenant_id=tenant,principal_id=principal,email_address=email,email_lookup_key=email,
                       verified_at=datetime.now(timezone.utc) if verified else None)
        membership=insert(c,s,"staff_memberships",tenant_id=tenant,principal_id=principal,organization_id=org)
        for role in roles:insert(c,s,"membership_roles",tenant_id=tenant,membership_id=membership,role=role)
        insert(c,s,"membership_queues",tenant_id=tenant,membership_id=membership,organization_id=org,queue_id=queue)
        if credential:set_staff_credential(c,tenant,principal,contact,hash_password(PASSWORD),schema=s)
    return dict(tenant=tenant,email=email,principal=principal,contact=contact,org=org,queue=queue,membership=membership)


class Harness:
    def __init__(self,target,db,auth,staff,app):
        self.target,self.db,self.auth,self.staff,self.app=target,db,auth,staff,app
        self.peer="staff-peer-"+uuid4().hex
    def client(self,host="a.example.test"):
        return AsyncClient(transport=ASGITransport(app=self.app,client=(self.peer,123)),base_url="https://"+host)
    def headers(self,client):
        return {"Origin":str(client.base_url).rstrip("/"),"X-CSRF-Token":client.cookies.get(CSRF_COOKIE)}
    async def boot(self,client):
        r=await client.post("/api/v1/auth/sessions",headers={"Origin":str(client.base_url).rstrip("/")})
        assert r.status_code==200
        return client.cookies.get(SESSION_COOKIE)
    async def login(self,client,email,password=PASSWORD,**kwargs):
        return await client.post("/api/v1/staff/auth/login",headers=kwargs.pop("headers",self.headers(client)),
                                 json=dict(contact=email,password=password,**kwargs))


@asynccontextmanager
async def harness(target,policy=StaffPolicy(),passwords=None,sender=None):
    db=RuntimeDatabase(target["url"],pool_max=4)
    settings=AuthSettings(SESSION_SECRET=target["key"],rate_window=target["window"])
    auth=AuthService(PostgresAuthRepository(db,target["schema"]),target["key"].encode(),
                     AuthPolicy(**settings.model_dump(exclude={"session_secret"})),sender)
    repository=PostgresStaffRepository(db,target["schema"])
    access=AccessService(auth,repository)
    staff=StaffService(auth,repository,passwords or BoundedPasswordVerifier(),access,policy)
    app=create_app(Settings(env="test",database_enabled=True,auth_enabled=True),
        database_settings=RuntimeDatabaseSettings(DATABASE_URL=target["url"]),auth_settings=settings,
        services=ApplicationServices(ProcessHealthService(),auth,staff,access))
    try:
        await db.open()
        yield Harness(target,db,auth,staff,app)
    finally:await db.close()


def test_staff_login_rotation_me_bootstrap_and_logout(target,caplog):
    identity=seed(target,roles=("admin","cs"))
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                old=await h.boot(client)
                response=await h.login(client,identity["email"])
                assert response.status_code==200 and response.json()==dict(session_kind="staff",principal_id=str(identity["principal"]))
                new=client.cookies.get(SESSION_COOKIE)
                assert new!=old and PASSWORD not in response.text and "$argon2" not in response.text
                assert "HttpOnly" in response.headers.get_list("set-cookie")[0]
                assert (await client.get("/api/v1/me")).json()["session_kind"]=="staff"
                assert (await client.post("/api/v1/auth/sessions",headers=h.headers(client))).status_code==200
                me=await client.get("/api/v1/staff/me")
                assert me.status_code==200 and me.headers["cache-control"]=="no-store"
                assert set(me.json())=={"session_kind","principal_id","memberships"}
                assert me.json()["memberships"][0]["roles"]==["admin","cs"]
                assert me.json()["memberships"][0]["queue_ids"]==[str(identity["queue"])]
                async with h.client() as replay:
                    assert (await replay.get("/api/v1/staff/me",headers={"Cookie":SESSION_COOKIE+"="+old})).status_code==401
                assert (await client.post("/api/v1/auth/logout",headers=h.headers(client))).status_code==204
                assert (await client.get("/api/v1/staff/me")).status_code==401
                assert old not in caplog.text and new not in caplog.text and PASSWORD not in caplog.text
    run(scenario())


@pytest.mark.parametrize("kind",["unknown","wrong_password","no_credential","inactive_principal","inactive_membership","inactive_org","inactive_credential","no_roles","unverified","other_tenant"])
def test_invalid_login_is_generic_and_does_not_grant_staff(target,kind):
    identity=seed(target,credential=kind not in {"no_credential","unverified"},verified=kind!="unverified",tenant=target["b"] if kind=="other_tenant" else None)
    tables={"inactive_principal":("principals","principal"),"inactive_membership":("staff_memberships","membership"),"inactive_org":("organizations","org"),"inactive_credential":("staff_credentials","principal")}
    if kind in tables:
        table,key=tables[kind];col="principal_id" if table=="staff_credentials" else "id"
        change(target,f"UPDATE {{s}}.{table} SET active=false WHERE {col}=:id",id=identity[key])
    if kind=="no_roles":change(target,"DELETE FROM {s}.membership_roles WHERE membership_id=:id",id=identity["membership"])
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                old=await h.boot(client)
                response=await h.login(client,"unknown"+uuid4().hex+"@example.com" if kind=="unknown" else identity["email"],password="incorrect-password" if kind=="wrong_password" else PASSWORD)
                assert response.status_code==401 and response.json()["error"]["code"]=="STAFF_LOGIN_FAILED"
                assert response.headers["cache-control"]=="no-store" and not response.headers.get_list("set-cookie")
                assert client.cookies.get(SESSION_COOKIE)==old
                assert (await client.get("/api/v1/staff/me")).status_code==401
    run(scenario())


@pytest.mark.parametrize("mutation",["membership","roles","queues","organization","principal","credential","rotate"])
def test_live_revocation_and_rotation_affect_next_request(target,mutation):
    identity=seed(target)
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client);assert (await h.login(client,identity["email"])).status_code==200
                token=client.cookies.get(SESSION_COOKIE)
                await h.staff.require(target["a"],token,"read_case",identity["org"],identity["queue"])
                if mutation=="queues":change(target,"DELETE FROM {s}.membership_queues WHERE membership_id=:id",id=identity["membership"])
                elif mutation=="roles":change(target,"DELETE FROM {s}.membership_roles WHERE membership_id=:id",id=identity["membership"])
                elif mutation in {"rotate","credential"}:
                    with target["engine"].begin() as c:
                        if mutation=="rotate":set_staff_credential(c,target["a"],identity["principal"],identity["contact"],hash_password("Changed staff test password!"),schema=target["schema"])
                        else:disable_staff_credential(c,target["a"],identity["principal"],schema=target["schema"])
                else:
                    table,key={"membership":("staff_memberships","membership"),"organization":("organizations","org"),"principal":("principals","principal")}[mutation]
                    change(target,f"UPDATE {{s}}.{table} SET active=false WHERE id=:id",id=identity[key])
                with pytest.raises(AuthError):await h.staff.require(target["a"],token,"read_case",identity["org"],identity["queue"])
                response=await client.get("/api/v1/staff/me")
                assert response.status_code==(200 if mutation=="queues" else 401)
                if mutation=="queues":assert response.json()["memberships"][0]["queue_ids"]==[]
    run(scenario())


def test_scopes_do_not_flatten_roles_across_organizations_or_tenants(target):
    identity=seed(target,roles=("admin",))
    other=seed(target,roles=("cs",))
    # Add a CS membership in B organization to the same principal; retain own admin membership in A.
    change(target,"UPDATE {s}.staff_memberships SET principal_id=:principal WHERE id=:id",principal=identity["principal"],id=other["membership"])
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client);assert (await h.login(client,identity["email"])).status_code==200
                token=client.cookies.get(SESSION_COOKIE)
                await h.staff.require(target["a"],token,"manage_memberships",identity["org"])
                await h.staff.require(target["a"],token,"read_case",other["org"],other["queue"])
                for tenant,action,org,queue in [(target["a"],"read_case",identity["org"],identity["queue"]),(target["a"],"read_case",other["org"],identity["queue"]),(target["a"],"assign_case",other["org"],other["queue"]),(target["b"],"manage_memberships",identity["org"],None)]:
                    with pytest.raises(AuthError):await h.staff.require(tenant,token,action,org,queue)
    run(scenario())


@pytest.mark.parametrize("limit",["contact","ip","session"])
def test_persistent_limits_precede_kdf_and_commit_failures(target,limit):
    identity=seed(target)
    class Reject:
        calls=0
        async def verify(self,*args):self.calls+=1;return False
    passwords=Reject()
    values={"contact_limit":10,"ip_limit":100,"session_limit":10};values[limit+"_limit"]=1
    async def scenario():
        async with harness(target,StaffPolicy(**values),passwords=passwords) as h:
            async with h.client() as client:
                await h.boot(client)
                assert (await h.login(client,identity["email"])).status_code==401
                assert (await h.login(client,identity["email"])).status_code==429
                assert passwords.calls==1
                assert (await h.login(client,identity["email"])).status_code==429
                assert passwords.calls==1
    run(scenario())


@pytest.mark.parametrize("race",["rotate","disable","membership","tenant","contact"])
def test_finish_rechecks_identity_after_kdf(target,race):
    identity=seed(target)
    class MutatingVerifier:
        async def verify(self,*args):
            if race in {"rotate","disable"}:
                with target["engine"].begin() as c:
                    if race=="rotate":set_staff_credential(c,target["a"],identity["principal"],identity["contact"],hash_password("Changed test password during login!"),schema=target["schema"])
                    else:disable_staff_credential(c,target["a"],identity["principal"],schema=target["schema"])
            elif race=="membership":change(target,"UPDATE {s}.staff_memberships SET active=false WHERE id=:id",id=identity["membership"])
            elif race=="tenant":change(target,"UPDATE {s}.tenants SET active=false WHERE id=:id",id=target["a"])
            else:change(target,"UPDATE {s}.contacts SET verified_at=NULL WHERE id=:id",id=identity["contact"])
            return True
    async def scenario():
        try:
            async with harness(target,passwords=MutatingVerifier()) as h:
                async with h.client() as client:
                    await h.boot(client)
                    response=await h.login(client,identity["email"])
                    assert response.status_code==401 and not response.headers.get_list("set-cookie")
        finally:
            if race=="tenant":change(target,"UPDATE {s}.tenants SET active=true WHERE id=:id",id=target["a"])
    run(scenario())


def test_same_anonymous_session_has_one_concurrent_login_winner(target):
    identity=seed(target)
    class Barrier:
        def __init__(self):self.count=0;self.event=asyncio.Event()
        async def verify(self,*args):
            self.count+=1
            if self.count==2:self.event.set()
            await self.event.wait()
            return True
    async def scenario():
        async with harness(target,passwords=Barrier()) as h:
            async with h.client() as client:
                await h.boot(client)
                responses=await asyncio.gather(h.login(client,identity["email"]),h.login(client,identity["email"]))
                assert sorted(r.status_code for r in responses)==[200,401]
                assert not next(r for r in responses if r.status_code==401).headers.get_list("set-cookie")
    run(scenario())


@pytest.mark.parametrize("payload",["extra","csrf","origin","duplicate_cookie","duplicate_header","long_password"])
def test_staff_http_proof_and_input_limits(target,payload):
    identity=seed(target)
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client);headers=h.headers(client)
                if payload=="csrf":headers.pop("X-CSRF-Token")
                if payload=="origin":headers["Origin"]="https://evil.example.test"
                if payload=="duplicate_cookie":headers["Cookie"]=f"{SESSION_COOKIE}=a; {SESSION_COOKIE}=b"
                if payload=="duplicate_header":headers=list(headers.items())+[("X-CSRF-Token","duplicate")]
                response=await h.login(client,identity["email"],headers=headers,**({"roles":["admin"]} if payload=="extra" else {}),password="x"*129 if payload=="long_password" else PASSWORD)
                assert response.status_code==(422 if payload in {"extra","long_password"} else 403)
                assert PASSWORD not in response.text
    run(scenario())


def test_runtime_acl_and_sql_null_fail_closed(target):
    identity=seed(target)
    async def scenario():
        async with harness(target) as h:
            with pytest.raises(DatabaseError):
                async with h.db.transaction() as c:
                    await c.execute(f'SELECT password_hash FROM "{target["schema"]}".staff_credentials')
            async with h.client() as client:
                old=await h.boot(client)
                repo=PostgresStaffRepository(h.db,target["schema"])
                status,session=await repo.finish(target["a"],h.auth.digest("session",target["a"],old),None,identity["principal"],None,uuid4(),"a"*64,"b"*64,300)
                assert status=="csrf_failed" and session is None
                status,session=await repo.finish(target["a"],h.auth.digest("session",target["a"],old),h.auth.digest("csrf",target["a"],client.cookies.get(CSRF_COOKIE)),identity["principal"],None,uuid4(),"c"*64,"d"*64,300)
                assert status=="invalid" and session is None
    run(scenario())
    acl=row(target,"""SELECT bool_and(NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AND p.proconfig=ARRAY['search_path=pg_catalog'])
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:sname AND p.proname LIKE 'auth_staff_%'""",sname=target["schema"])
    assert acl[0]


def test_provisioner_refuses_missing_membership_or_unverified_contact(target):
    identity=seed(target,credential=False,verified=False)
    with pytest.raises(ValueError):
        with target["engine"].begin() as c:set_staff_credential(c,target["a"],identity["principal"],identity["contact"],hash_password(PASSWORD),schema=target["schema"])
    change(target,"UPDATE {s}.contacts SET verified_at=CURRENT_TIMESTAMP WHERE id=:id",id=identity["contact"])
    change(target,"UPDATE {s}.staff_memberships SET active=false WHERE id=:id",id=identity["membership"])
    with pytest.raises(ValueError):
        with target["engine"].begin() as c:set_staff_credential(c,target["a"],identity["principal"],identity["contact"],hash_password(PASSWORD),schema=target["schema"])



def test_customer_verification_of_staff_email_cannot_create_staff_session_on_latest_schema(target):
    identity=seed(target)
    class Delivery:
        message=None
        async def send(self,message):self.message=message
    sender=Delivery()
    async def scenario():
        async with harness(target,sender=sender) as h:
            async with h.client() as client:
                await h.boot(client)
                accepted=await client.post("/api/v1/auth/challenges",headers=h.headers(client),json=dict(contact=identity["email"],purpose="customer_login"))
                assert accepted.status_code==202
                result=await client.post("/api/v1/auth/challenges/"+accepted.json()["challenge_id"]+"/verify",headers=h.headers(client),json=dict(code=sender.message.code))
                assert result.status_code==200 and result.json()["session_kind"]=="customer"
                assert (await client.get("/api/v1/staff/me")).status_code==401
                assert (await h.login(client,identity["email"])).status_code==401
                assert (await client.post("/api/v1/auth/logout",headers=h.headers(client))).status_code==204
                await h.boot(client)
                assert (await h.login(client,identity["email"])).status_code==200
    run(scenario())



def test_auth_owner_can_lock_but_cannot_update_credential(target):
    from sqlalchemy.exc import DBAPIError
    identity=seed(target)
    with target["engine"].begin() as c:
        c.exec_driver_sql('SET LOCAL ROLE "'+target["owner"]+'"')
        value=c.execute(text('SELECT principal_id FROM "'+target["schema"]+'".staff_credentials WHERE principal_id=:id FOR SHARE'),dict(id=identity["principal"])).scalar_one()
        assert value==identity["principal"]
    with pytest.raises(DBAPIError):
        with target["engine"].begin() as c:
            c.exec_driver_sql('SET LOCAL ROLE "'+target["owner"]+'"')
            c.execute(text('UPDATE "'+target["schema"]+'".staff_credentials SET credential_version=gen_random_uuid() WHERE principal_id=:id'),dict(id=identity["principal"]))
