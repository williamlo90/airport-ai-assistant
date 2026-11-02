"""Shared guards with real Neon identities and test-only resource projections.

Cases/conversations/drafts have no product tables yet. These fixture routes prove
only AUTH-04 identity/authorization wiring, not future case state/source gates.
"""

from dataclasses import replace
import os
from uuid import UUID, uuid4

from fastapi import Depends
import pytest

from test_staff_auth import target, harness, seed, run, change, PASSWORD
from airport_assistant.api.auth import SESSION_COOKIE, CSRF_COOKIE
from airport_assistant.api.dependencies import get_identity, get_mutation_identity
from airport_assistant.api.schemas import RequestModel
from airport_assistant.modules.identity.access import AccessIdentity, AccessService, OwnedObject, StaffObject, ResourceKey
from airport_assistant.modules.identity.auth import AuthError

pytestmark=pytest.mark.skipif(os.environ.get("RUN_NEON_AUTH_TESTS")!="1",reason="Set RUN_NEON_AUTH_TESTS=1 for shared access real identity tests.")


class Objects:
    def __init__(self):self.values={};self.lookups=[];self.bad_projection=None
    async def find(self,key):
        self.lookups.append(key)
        return self.bad_projection or self.values.get(key)
    def add(self,value):
        self.values[ResourceKey(value.tenant_id,value.kind,value.id)]=value
        return value


class Action(RequestModel):
    action:str


def mount(h):
    objects=Objects()
    async def lookup(identity,kind,id,action):
        requested=ResourceKey(identity.session.tenant_id,kind,id)
        found=await objects.find(requested)
        value=AccessService.require_object(identity,requested,found,action)
        return {"id":str(value.id),"kind":value.kind}
    @h.app.get("/api/v1/_auth04/{kind}/{id}")
    async def read(kind:str,id:UUID,action:str,identity:AccessIdentity=Depends(get_identity)):
        return await lookup(identity,kind,id,action)
    @h.app.post("/api/v1/_auth04/{kind}/{id}")
    async def act(kind:str,id:UUID,body:Action,identity:AccessIdentity=Depends(get_mutation_identity)):
        return await lookup(identity,kind,id,body.action)
    return objects


async def read(client,obj,action):
    return await client.get(f"/api/v1/_auth04/{obj.kind}/{obj.id}",params={"action":action})


def test_anonymous_exact_session_cross_tenant_and_missing_have_same_concealed_response(target):
    async def scenario():
        async with harness(target) as h:
            objects=mount(h)
            async with h.client() as owner,h.client() as other,h.client("b.example.test") as tenantb,h.client() as unauthenticated:
                await h.boot(owner);await h.boot(other);await h.boot(tenantb)
                s=await h.auth.current(target["a"],owner.cookies.get(SESSION_COOKIE))
                obj=objects.add(OwnedObject(target["a"],uuid4(),"conversation",owner_session_id=s.id,ownership_only=True))
                assert (await read(owner,obj,"read_conversation")).status_code==200
                before=len(objects.lookups)
                assert (await read(unauthenticated,obj,"read_conversation")).status_code==401
                assert len(objects.lookups)==before
                responses=[await read(other,obj,"read_conversation"),await read(tenantb,obj,"read_conversation"),
                           await read(owner,replace(obj,id=uuid4()),"read_conversation"),await read(other,obj,"unknown")]
                for r in responses:
                    assert r.status_code==404 and r.json()["error"]["code"]=="RESOURCE_NOT_FOUND"
                    assert str(obj.id) not in r.text and str(target["a"]) not in r.text
                def public_error(r):return {k:v for k,v in r.json()["error"].items() if k!="request_id"}
                assert all(public_error(r)==public_error(responses[0]) for r in responses)
                forbidden=await read(owner,obj,"manage_memberships")
                assert forbidden.status_code==403 and forbidden.json()["error"]["code"]=="ACTION_FORBIDDEN"
    run(scenario())


def test_customer_ownership_and_old_anonymous_session_after_verification(target):
    class Delivery:
        message=None
        async def send(self,message):self.message=message
    sender=Delivery()
    async def scenario():
        async with harness(target,sender=sender) as h:
            objects=mount(h)
            async with h.client() as client,h.client() as oldbrowser:
                old=await h.boot(client)
                anonymous=await h.auth.current(target["a"],old)
                oldobj=objects.add(OwnedObject(target["a"],uuid4(),"draft",owner_session_id=anonymous.id,ownership_only=True))
                accepted=await client.post("/api/v1/auth/challenges",headers=h.headers(client),json=dict(contact="owner"+uuid4().hex+"@example.com",purpose="customer_login"))
                verified=await client.post("/api/v1/auth/challenges/"+accepted.json()["challenge_id"]+"/verify",headers=h.headers(client),json=dict(code=sender.message.code))
                assert verified.status_code==200 and verified.json()["session_kind"]=="customer"
                principal=UUID(verified.json()["principal_id"])
                owned=objects.add(OwnedObject(target["a"],uuid4(),"conversation",owner_principal_id=principal,ownership_only=True))
                assert (await read(client,owned,"read_conversation")).status_code==200
                not_owned=objects.add(replace(owned,id=uuid4(),owner_principal_id=uuid4()))
                assert (await read(client,not_owned,"read_conversation")).status_code==404
                # AUTH-02 adoption has not happened. Old anonymous ownership is not inferred.
                assert (await read(client,oldobj,"read_draft")).status_code==404
                response=await oldbrowser.get(f"/api/v1/_auth04/conversation/{owned.id}",params={"action":"read_conversation"},headers={"Cookie":SESSION_COOKIE+"="+old})
                assert response.status_code==401
                assert (await client.get("/api/v1/staff/me")).status_code==401
    run(scenario())


@pytest.mark.parametrize("roles,action,expected",[("admin","read_case",404),("cs","read_case",200),("cs","assign_case",403),("supervisor","assign_case",200)])
def test_staff_scope_and_visible_action_http(target,roles,action,expected):
    identity=seed(target,roles=(roles,))
    async def scenario():
        async with harness(target) as h:
            objects=mount(h)
            obj=objects.add(StaffObject(target["a"],uuid4(),"case",identity["org"],identity["queue"]))
            async with h.client() as client:
                await h.boot(client);assert (await h.login(client,identity["email"])).status_code==200
                assert (await read(client,obj,action)).status_code==expected
                other=objects.add(replace(obj,id=uuid4(),queue_id=uuid4()))
                assert (await read(client,other,"unknown")).status_code==404
    run(scenario())


@pytest.mark.parametrize("change_kind,expected",[("queue",404),("role",404),("membership",401),("credential",401),("session",401),("expired",401)])
def test_each_new_request_rechecks_current_identity_and_scope(target,change_kind,expected):
    identity=seed(target)
    async def scenario():
        async with harness(target) as h:
            objects=mount(h)
            obj=objects.add(StaffObject(target["a"],uuid4(),"case",identity["org"],identity["queue"]))
            async with h.client() as client:
                await h.boot(client);assert (await h.login(client,identity["email"])).status_code==200
                token=client.cookies.get(SESSION_COOKIE)
                assert (await read(client,obj,"read_case")).status_code==200
                if change_kind=="queue":change(target,"DELETE FROM {s}.membership_queues WHERE membership_id=:id",id=identity["membership"])
                elif change_kind=="role":change(target,"UPDATE {s}.membership_roles SET role='admin' WHERE membership_id=:id",id=identity["membership"])
                elif change_kind=="membership":change(target,"UPDATE {s}.staff_memberships SET active=false WHERE id=:id",id=identity["membership"])
                elif change_kind=="credential":change(target,"UPDATE {s}.staff_credentials SET active=false WHERE principal_id=:id",id=identity["principal"])
                else:
                    mutation="revoked_at=clock_timestamp()" if change_kind=="session" else "expires_at=clock_timestamp()-interval '1 second'"
                    change(target,f"UPDATE {{s}}.sessions SET {mutation} WHERE token_hash=:hash",hash=h.auth.digest("session",target["a"],token))
                before=len(objects.lookups)
                assert (await read(client,obj,"read_case")).status_code==expected
                if expected==401:assert len(objects.lookups)==before
    run(scenario())


def test_admin_self_management_forbidden_and_other_org_hidden(target):
    identity=seed(target,roles=("admin",))
    async def scenario():
        async with harness(target) as h:
            objects=mount(h)
            own=objects.add(StaffObject(target["a"],identity["membership"],"membership",identity["org"],target_principal_id=identity["principal"]))
            other=objects.add(replace(own,id=uuid4(),target_principal_id=uuid4()))
            external=objects.add(replace(other,id=uuid4(),organization_id=uuid4()))
            async with h.client() as client:
                await h.boot(client);assert (await h.login(client,identity["email"])).status_code==200
                assert (await read(client,own,"read_membership")).status_code==200
                assert (await read(client,own,"manage_memberships")).status_code==403
                assert (await read(client,other,"manage_memberships")).status_code==200
                assert (await read(client,external,"manage_memberships")).status_code==404
    run(scenario())


@pytest.mark.parametrize("fault",["wrong_id","wrong_kind","wrong_tenant"])
def test_incorrect_repository_projection_never_authorizes_another_requested_object(target,fault):
    async def scenario():
        async with harness(target) as h:
            objects=mount(h)
            async with h.client() as client:
                old=await h.boot(client);s=await h.auth.current(target["a"],old)
                requested=OwnedObject(target["a"],uuid4(),"conversation",owner_session_id=s.id,ownership_only=True)
                altered={"wrong_id":dict(id=uuid4()),"wrong_kind":dict(kind="draft"),"wrong_tenant":dict(tenant_id=target["b"])}[fault]
                objects.bad_projection=replace(requested,**altered)
                assert (await read(client,requested,"read_conversation")).status_code==404
    run(scenario())


@pytest.mark.parametrize("failure",["csrf","origin","double_csrf","claims","valid"])
def test_shared_mutation_dependency_proof_and_rejected_identity_claims(target,failure):
    async def scenario():
        async with harness(target) as h:
            objects=mount(h)
            async with h.client() as client:
                token=await h.boot(client);s=await h.auth.current(target["a"],token)
                obj=objects.add(OwnedObject(target["a"],uuid4(),"draft",owner_session_id=s.id,ownership_only=True))
                headers=h.headers(client);body={"action":"confirm_draft"}
                if failure=="csrf":headers.pop("X-CSRF-Token")
                elif failure=="origin":headers["Origin"]="https://other.example.test"
                elif failure=="double_csrf":headers=list(headers.items())+[("X-CSRF-Token","duplicate")]
                elif failure=="claims":body.update(tenant_id=str(target["b"]),owner_session_id=str(s.id),roles=["admin"])
                response=await client.post(f"/api/v1/_auth04/draft/{obj.id}",headers=headers,json=body)
                assert response.status_code==({"claims":422,"valid":200}.get(failure,403))
                if failure in {"csrf","origin","double_csrf","claims"}:assert objects.lookups==[]
    run(scenario())


def test_production_routes_have_no_fixture_object_endpoints(target):
    async def scenario():
        async with harness(target) as h:
            paths=set(h.app.openapi()["paths"])
            assert not any("_auth04" in path for path in paths)
            assert paths >= {"/health/live","/api/v1/me","/api/v1/staff/me"}
    run(scenario())
