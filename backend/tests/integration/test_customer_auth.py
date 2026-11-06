"""Real restricted DB login + HTTPS ASGI auth; email delivery is controlled only."""

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
from airport_assistant.modules.identity.access import AccessService
from airport_assistant.modules.identity.auth import AuthService,AuthPolicy
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.system.health import ProcessHealthService

pytestmark=pytest.mark.skipif(os.environ.get("RUN_NEON_AUTH_TESTS")!="1",reason="Set RUN_NEON_AUTH_TESTS=1 for customer auth DB/API integration.")


def insert(c,schema,table,**values):
    columns=",".join('"'+key+'"' for key in values);params=",".join(':'+key for key in values)
    return c.execute(text(f'INSERT INTO "{schema}"."{table}" ({columns}) VALUES ({params}) RETURNING id'),values).scalar_one()


@pytest.fixture(scope="module")
def target():
    file=Path(os.environ.get("DB_TEST_ENV_FILE",".env"))
    runtime=load_database_settings(RuntimeDatabaseSettings,file);direct=load_database_settings(MigrationDatabaseSettings,file)
    suffix=uuid4().hex;schema="auth01_test_"+suffix
    role="auth01_runtime_"+suffix;policy="auth01_policy_"+suffix;owner="auth01_owner_"+suffix
    password=secrets.token_urlsafe(32);key=secrets.token_urlsafe(48)
    engine=create_migration_engine(direct.url.get_secret_value())
    try:
        with engine.begin() as c:prepare_roles(c,role,policy);prepare_auth_owner(c,owner)
        assert migrate(direct.url.get_secret_value(),schema=schema,runtime_role=role,policy_role=policy,auth_role=owner,target_revision="0003_customer_auth")=="0003_customer_auth"
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
        assert schema=="auth01_test_"+suffix
        with engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            for name in (role,policy,owner):
                assert name.endswith(suffix)
                c.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        engine.dispose()


class ControlledSender:
    def __init__(self):self.messages={};self.failure=None
    async def send(self,message):
        self.messages[str(message.challenge_id)]=message
        if self.failure=="timeout":await asyncio.Event().wait()
        if self.failure=="raise":raise RuntimeError("provider-secret-sentinel "+message.code)


class Harness:
    def __init__(self,target,app,db,service,sender):
        self.target=target;self.app=app;self.db=db;self.service=service;self.sender=sender
        self.peer="test-peer-"+uuid4().hex
    def client(self,host="a.example.test"):
        return AsyncClient(transport=ASGITransport(app=self.app,client=(self.peer,123)),base_url="https://"+host)
    def headers(self,client,csrf=True):
        value={"Origin":str(client.base_url).rstrip("/")}
        if csrf:value["X-CSRF-Token"]=client.cookies.get(CSRF_COOKIE)
        return value
    async def boot(self,client):
        result=await client.post("/api/v1/auth/sessions",headers=self.headers(client,False))
        assert result.status_code==200
        return client.cookies.get(SESSION_COOKIE)
    async def issue(self,client,email=None):
        email=email or "user"+uuid4().hex+"@example.com"
        return await client.post("/api/v1/auth/challenges",headers=self.headers(client),json=dict(contact=email,purpose="customer_login"))
    async def verify(self,client,id,code=None,headers=None):
        code=code if code is not None else self.sender.messages[id].code
        return await client.post(f"/api/v1/auth/challenges/{id}/verify",headers=headers or self.headers(client),json=dict(code=code))


@asynccontextmanager
async def harness(target,*,sender_enabled=True,**limits):
    db=RuntimeDatabase(target["url"],pool_max=4)
    settings=AuthSettings(SESSION_SECRET=target["key"],rate_window=target["window"],**limits)
    policy=AuthPolicy(**settings.model_dump(exclude={"session_secret"}))
    sender=ControlledSender()
    service=AuthService(PostgresAuthRepository(db,target["schema"]),target["key"].encode(),policy,sender if sender_enabled else None)
    application=create_app(Settings(env="test",database_enabled=True,auth_enabled=True),
        database_settings=RuntimeDatabaseSettings(DATABASE_URL=target["url"]),auth_settings=settings,
        services=ApplicationServices(ProcessHealthService(),service,access=AccessService(service)))
    try:
        await db.open()
        yield Harness(target,application,db,service,sender)
    finally:await db.close()


def run(coroutine):
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:runner.run(coroutine)


def row(target,query,**params):
    with target["engine"].connect() as c:return c.execute(text(query.format(s='"'+target["schema"]+'"')),params).first()


def test_complete_flow_hashes_rotation_revocation_and_no_secret_output(target,caplog):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                old=await h.boot(client);old_csrf=client.cookies.get(CSRF_COOKIE)
                accepted=await h.issue(client)
                assert accepted.status_code==202
                id=accepted.json()["challenge_id"];message=h.sender.messages[id]
                assert message.code not in accepted.text and set(accepted.json())=={"challenge_id","status"}
                stored=row(target,"SELECT hashed_code,attempts FROM {s}.verification_challenges WHERE id=:id",id=id)
                assert stored.hashed_code!=message.code and len(stored.hashed_code)==64 and stored.attempts==0
                verified=await h.verify(client,id)
                assert verified.status_code==200 and verified.json()["session_kind"]=="customer"
                new=client.cookies.get(SESSION_COOKIE)
                assert new!=old and client.cookies.get(CSRF_COOKIE)!=old_csrf
                assert set(verified.json())=={"session_kind","principal_id"}
                assert (await client.get("/api/v1/me")).status_code==200
                assert (await h.verify(client,id)).status_code==401
                record=row(target,"SELECT revoked_at,replaced_by_session_id,token_hash FROM {s}.sessions WHERE token_hash=:hash",hash=h.service.digest("session",target["a"],old))
                assert record.revoked_at and record.replaced_by_session_id and record.token_hash!=old
                async with h.client() as replay:
                    assert (await replay.get("/api/v1/me",headers={"Cookie":SESSION_COOKIE+"="+old})).status_code==401
                logout=await client.post("/api/v1/auth/logout",headers=h.headers(client))
                assert logout.status_code==204 and logout.content==b""
                assert (await client.get("/api/v1/me")).status_code==401
                assert old not in caplog.text and new not in caplog.text and message.code not in caplog.text
    run(scenario())


@pytest.mark.parametrize("wrong",["browser","tenant"])
def test_wrong_browser_or_tenant_cannot_consume_challenge(target,wrong):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as original,h.client("b.example.test" if wrong=="tenant" else "a.example.test") as other:
                await h.boot(original);await h.boot(other)
                accepted=await h.issue(original);id=accepted.json()["challenge_id"]
                result=await h.verify(other,id)
                assert result.status_code in (401,422)
                assert row(target,"SELECT attempts,consumed_at FROM {s}.verification_challenges WHERE id=:id",id=id)==(0,None)
                assert (await h.verify(original,id)).status_code==200
    run(scenario())


@pytest.mark.parametrize("state",["expired","exhausted","superseded","unknown"])
def test_invalid_codes_and_challenge_states_are_generic_and_bounded(target,state):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client);accepted=await h.issue(client);id=accepted.json()["challenge_id"]
                if state=="expired":
                    with target["engine"].begin() as c:c.execute(text(f'UPDATE "{target["schema"]}".verification_challenges SET expires_at=clock_timestamp()-interval \'1 second\' WHERE id=:id'),dict(id=id))
                elif state=="exhausted":
                    bad="00000000" if h.sender.messages[id].code!="00000000" else "11111111"
                    for _ in range(5):assert (await h.verify(client,id,bad)).status_code==422
                    assert row(target,"SELECT attempts FROM {s}.verification_challenges WHERE id=:id",id=id)[0]==5
                elif state=="superseded":assert (await h.issue(client)).status_code==202
                elif state=="unknown":id=str(uuid4())
                result=await h.verify(client,id,h.sender.messages[id].code if id in h.sender.messages else "12345678")
                assert result.status_code==422 and result.json()["error"]["code"]=="VERIFICATION_FAILED"
                assert (await client.get("/api/v1/me")).json()["session_kind"]=="anonymous"
    run(scenario())


@pytest.mark.parametrize("limit",["contact","ip","session"])
def test_issue_rate_limits_persist_and_forwarded_header_does_not_bypass(target,limit):
    async def scenario():
        async with harness(target,**{limit+"_limit":1}) as h:
            email="rate"+uuid4().hex+"@example.com"
            async with h.client() as first,h.client() as second:
                await h.boot(first);assert (await h.issue(first,email)).status_code==202
                chosen=first
                if limit=="contact":await h.boot(second);chosen=second
                chosen.headers["X-Forwarded-For"]="203.0.113.201"
                limited=await h.issue(chosen,email if limit=="contact" else None)
                assert limited.status_code==429 and limited.headers["Retry-After"]==str(target["window"])
                assert (await h.issue(chosen,email if limit=="contact" else None)).status_code==429
                assert len(h.sender.messages)==1
    run(scenario())


def test_concurrent_contact_limit_allows_one_delivery(target):
    async def scenario():
        async with harness(target,contact_limit=1) as h:
            email="race"+uuid4().hex+"@example.com"
            async with h.client() as a,h.client() as b:
                await h.boot(a);await h.boot(b)
                results=await asyncio.gather(h.issue(a,email),h.issue(b,email))
                assert sorted(r.status_code for r in results)==[202,429] and len(h.sender.messages)==1
    run(scenario())


def test_concurrent_verify_consumes_once(target):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                old=await h.boot(client);result=await h.issue(client);id=result.json()["challenge_id"]
                headers=h.headers(client)|{"Cookie":SESSION_COOKIE+"="+old+"; "+CSRF_COOKIE+"="+client.cookies.get(CSRF_COOKIE)}
                results=await asyncio.gather(h.verify(client,id,headers=headers),h.verify(client,id,headers=headers))
                assert sorted(r.status_code for r in results)==[200,401]
                assert len([r for r in results if r.headers.get_list("set-cookie")])==1
                assert row(target,"SELECT consumed_at,attempts FROM {s}.verification_challenges WHERE id=:id",id=id).attempts==1
    run(scenario())


def test_same_contact_parallel_verifications_create_one_principal(target):
    async def scenario():
        async with harness(target) as h:
            email="parallel"+uuid4().hex+"@example.com"
            before=row(target,"SELECT count(*) FROM {s}.principals WHERE tenant_id=:tenant",tenant=target["a"])[0]
            async with h.client() as a,h.client() as b:
                await h.boot(a);await h.boot(b)
                ia=(await h.issue(a,email)).json()["challenge_id"];ib=(await h.issue(b,email)).json()["challenge_id"]
                results=await asyncio.gather(h.verify(a,ia),h.verify(b,ib))
                assert all(r.status_code==200 for r in results)
                assert results[0].json()["principal_id"]==results[1].json()["principal_id"]
                assert row(target,"SELECT count(*) FROM {s}.principals WHERE tenant_id=:tenant",tenant=target["a"])[0]==before+1
    run(scenario())


@pytest.mark.parametrize("active",[True,False])
def test_existing_contact_binding_is_not_rebound_or_reactivated(target,active):
    email="existing"+uuid4().hex+"@example.com"
    with target["engine"].begin() as c:
        principal=insert(c,target["schema"],"principals",tenant_id=target["a"],active=active)
        insert(c,target["schema"],"contacts",tenant_id=target["a"],principal_id=principal,email_address=email,email_lookup_key=email)
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client);accepted=await h.issue(client,email)
                assert accepted.status_code==202
                result=await h.verify(client,accepted.json()["challenge_id"])
                assert result.status_code==(200 if active else 422)
                bound=row(target,"SELECT principal_id,verified_at FROM {s}.contacts WHERE tenant_id=:tenant AND email_lookup_key=:email",tenant=target["a"],email=email)
                assert bound.principal_id==principal and bool(bound.verified_at)==active
    run(scenario())


def test_customer_verification_of_staff_contact_never_creates_staff_session(target):
    email="staff"+uuid4().hex+"@example.com";s=target["schema"]
    with target["engine"].begin() as c:
        principal=insert(c,s,"principals",tenant_id=target["a"])
        insert(c,s,"contacts",tenant_id=target["a"],principal_id=principal,email_address=email,email_lookup_key=email)
        org=insert(c,s,"organizations",tenant_id=target["a"],name="Staff unit")
        member=insert(c,s,"staff_memberships",tenant_id=target["a"],principal_id=principal,organization_id=org)
        insert(c,s,"membership_roles",tenant_id=target["a"],membership_id=member,role="admin")
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client);id=(await h.issue(client,email)).json()["challenge_id"]
                result=await h.verify(client,id)
                assert result.status_code==200 and result.json()==dict(session_kind="customer",principal_id=str(principal))
                async with h.db.transaction(DatabaseContext(target["a"],principal,"customer")) as c:
                    assert not (await (await c.execute(f'SELECT "{s}".has_staff_role(%s,ARRAY[\'admin\'])',(org,))).fetchone())[0]
    run(scenario())


@pytest.mark.parametrize("failure",["raise","timeout"])
def test_delivery_outcome_preserves_uncertain_code_without_exposing_it(target,failure,caplog):
    async def scenario():
        async with harness(target,delivery_timeout=0.1) as h:
            h.sender.failure=failure
            async with h.client() as client:
                await h.boot(client);result=await h.issue(client)
                assert result.status_code==(202 if failure=="timeout" else 503) and len(h.sender.messages)==1
                id=next(iter(h.sender.messages));message=h.sender.messages[id]
                assert message.code not in result.text+caplog.text
                consumed=row(target,"SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id",id=id)[0]
                assert (consumed is None)==(failure=="timeout")
                assert (await h.verify(client,id)).status_code==(200 if failure=="timeout" else 422)
    run(scenario())


def test_unconfigured_delivery_is_unavailable_and_runtime_cannot_read_auth_tables(target):
    async def scenario():
        async with harness(target,sender_enabled=False) as h:
            async with h.client() as client:
                await h.boot(client)
                assert (await h.issue(client)).status_code==503
                assert not h.sender.messages
            for table in ("sessions","verification_challenges","auth_rate_limits"):
                with pytest.raises(DatabaseError) as error:
                    async with h.db.transaction() as c:await c.execute(f'SELECT * FROM "{target["schema"]}"."{table}"')
                assert error.value.sqlstate=="42501"
    run(scenario())


def test_live_csrf_purpose_and_extra_fields_are_rejected_without_consumption(target):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                await h.boot(client)
                base=dict(contact="strict"+uuid4().hex+"@example.com",purpose="customer_login")
                for body in (base|{"purpose":"staff_login"},base|{"tenant_id":str(target["b"])}):
                    result=await client.post("/api/v1/auth/challenges",headers=h.headers(client),json=body)
                    assert result.status_code==422
                assert (await client.post("/api/v1/auth/sessions",headers=h.headers(client,False),json={"session_kind":"staff"})).status_code==422
                accepted=await h.issue(client,base["contact"]);id=accepted.json()["challenge_id"]
                result=await h.verify(client,id,headers=h.headers(client)|{"X-CSRF-Token":"X"*43})
                assert result.status_code==403
                assert row(target,"SELECT attempts FROM {s}.verification_challenges WHERE id=:id",id=id)[0]==0
                assert (await h.verify(client,id)).status_code==200
    run(scenario())


def test_boot_rate_is_persistent_and_valid_session_is_preserved(target):
    async def scenario():
        async with harness(target,boot_limit=1) as h:
            async with h.client() as a,h.client() as b:
                token=await h.boot(a)
                assert await h.boot(a)==token
                result=await b.post("/api/v1/auth/sessions",headers=h.headers(b,False))
                assert result.status_code==429
    run(scenario())


@pytest.mark.parametrize("scope",["ip","session"])
def test_verification_rate_limits_commit_on_failed_attempt(target,scope):
    async def scenario():
        async with harness(target,**{"verify_"+scope+"_limit":1}) as h:
            async with h.client() as client:
                await h.boot(client);id=(await h.issue(client)).json()["challenge_id"]
                bad="00000000" if h.sender.messages[id].code!="00000000" else "11111111"
                assert (await h.verify(client,id,bad)).status_code==422
                assert (await h.verify(client,id)).status_code==429
                assert row(target,"SELECT attempts,consumed_at FROM {s}.verification_challenges WHERE id=:id",id=id)==(1,None)
    run(scenario())


def test_expired_session_and_inactive_tenant_are_rechecked_in_database(target):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                token=await h.boot(client);csrf=client.cookies.get(CSRF_COOKIE)
                id=(await h.issue(client)).json()["challenge_id"]
                with target["engine"].begin() as c:
                    c.execute(text(f'UPDATE "{target["schema"]}".tenants SET active=false WHERE id=:id'),dict(id=target["a"]))
                try:
                    repo=PostgresAuthRepository(h.db,target["schema"])
                    status,session=await repo.verify(target["a"],UUID(id),h.service.digest("session",target["a"],token),h.service.digest("csrf",target["a"],csrf),h.service.digest("otp",target["a"],id+":"+h.sender.messages[id].code),uuid4(),"a"*64,"b"*64,3600,h.service.digest("ip",target["a"],h.peer),target["window"],100,10)
                    assert status=="invalid_session" and session is None
                finally:
                    with target["engine"].begin() as c:c.execute(text(f'UPDATE "{target["schema"]}".tenants SET active=true WHERE id=:id'),dict(id=target["a"]))
                with target["engine"].begin() as c:c.execute(text(f'UPDATE "{target["schema"]}".sessions SET expires_at=clock_timestamp()-interval \'1 second\' WHERE token_hash=:hash'),dict(hash=h.service.digest("session",target["a"],token)))
                assert (await client.get("/api/v1/me")).status_code==401
                assert (await h.verify(client,id)).status_code==401
    run(scenario())


def test_customer_session_survives_new_service_instance(target):
    async def scenario():
        async with harness(target) as first:
            async with first.client() as client:
                token=await first.boot(client)
                id=(await first.issue(client)).json()["challenge_id"]
                assert (await first.verify(client,id)).status_code==200
                token=client.cookies.get(SESSION_COOKIE)
        async with harness(target) as second:
            async with second.client() as client:
                result=await client.get("/api/v1/me",headers={"Cookie":SESSION_COOKIE+"="+token})
                assert result.status_code==200 and result.json()["session_kind"]=="customer"
    run(scenario())


@pytest.mark.parametrize("missing",["code_hash","csrf_hash"])
def test_sql_verifier_null_inputs_fail_closed(target,missing):
    async def scenario():
        async with harness(target) as h:
            async with h.client() as client:
                token=await h.boot(client);csrf=client.cookies.get(CSRF_COOKIE)
                id=(await h.issue(client)).json()["challenge_id"]
                csrf_hash=h.service.digest("csrf",target["a"],csrf)
                code_hash=h.service.digest("otp",target["a"],id+":"+h.sender.messages[id].code)
                status,session=await PostgresAuthRepository(h.db,target["schema"]).verify(target["a"],UUID(id),h.service.digest("session",target["a"],token),None if missing=="csrf_hash" else csrf_hash,None if missing=="code_hash" else code_hash,uuid4(),"a"*64,"b"*64,3600,h.service.digest("ip",target["a"],h.peer),target["window"],100,15)
                assert session is None and status==("csrf_failed" if missing=="csrf_hash" else "invalid")
                assert row(target,"SELECT consumed_at FROM {s}.verification_challenges WHERE id=:id",id=id)[0] is None
                assert (await h.verify(client,id)).status_code==200
    run(scenario())


def test_auth_functions_are_private_fixed_path_and_owned_by_nologin_role(target):
    with target["engine"].connect() as c:
        functions=c.execute(text("""SELECT p.prosecdef,p.proconfig,p.proowner=r.oid
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_roles r ON r.rolname=:owner WHERE n.nspname=:schema AND p.proname LIKE 'auth_%'"""),dict(owner=target["owner"],schema=target["schema"])).all()
        assert len(functions)==7 and all(row[0] and row[1]==["search_path=pg_catalog"] and row[2] for row in functions)
        assert c.execute(text("SELECT NOT(rolcanlogin OR rolbypassrls OR rolinherit OR rolcreaterole OR rolcreatedb) FROM pg_roles WHERE rolname=:owner"),dict(owner=target["owner"])).scalar_one()
        assert c.execute(text("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
            LATERAL aclexplode(p.proacl) a WHERE n.nspname=:schema AND p.proname LIKE 'auth_%'
            AND a.grantee=0 AND a.privilege_type='EXECUTE'"""),dict(schema=target["schema"])).scalar_one()==0
