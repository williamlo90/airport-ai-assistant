"""Shared access matrix, exact object binding, and fresh identity boundaries."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from airport_assistant.modules.identity.access import AccessIdentity, AccessService, OwnedObject, StaffObject, ResourceKey
from airport_assistant.modules.identity.auth import Session, AuthError
from airport_assistant.modules.identity.membership import Membership


def session(kind="anonymous",tenant=None,principal=None):
    return Session(uuid4(),tenant or uuid4(),None if kind=="anonymous" else principal or uuid4(),kind,
                   datetime.now(timezone.utc)+timedelta(hours=1),"private-csrf-digest")


def key(target):return ResourceKey(target.tenant_id,target.kind,target.id)


def error(status,callback):
    with pytest.raises(AuthError) as exc:callback()
    assert exc.value.status==status


@pytest.mark.parametrize("kind,action",[("conversation","read_conversation"),("conversation","append_message"),("draft","read_draft"),("draft","confirm_draft"),("draft","cancel_draft")])
@pytest.mark.parametrize("actor",["anonymous","customer"])
def test_owned_object_exact_owner_and_kind_permissions(kind,action,actor):
    s=session(actor);identity=AccessIdentity(s)
    target=OwnedObject(s.tenant_id,uuid4(),kind,owner_session_id=s.id,ownership_only=True) if actor=="anonymous" else OwnedObject(s.tenant_id,uuid4(),kind,owner_principal_id=s.principal_id,ownership_only=True)
    assert AccessService.require_object(identity,key(target),target,action) is target
    other=replace(target,owner_session_id=uuid4()) if actor=="anonymous" else replace(target,owner_principal_id=uuid4())
    error(404,lambda:AccessService.require_object(identity,key(other),other,action))
    error(403,lambda:AccessService.require_object(identity,key(target),target,"unknown_action"))
    error(403,lambda:AccessService.require_object(identity,key(target),target,"read_case"))


@pytest.mark.parametrize("mismatch",["id","tenant","kind","absent","unsupported"])
def test_mismatched_repository_projection_and_missing_objects_are_concealed(mismatch):
    s=session("customer");identity=AccessIdentity(s)
    target=OwnedObject(s.tenant_id,uuid4(),"conversation",owner_principal_id=s.principal_id,ownership_only=True)
    requested=key(target)
    if mismatch=="id":requested=replace(requested,id=uuid4())
    if mismatch=="tenant":requested=replace(requested,tenant_id=uuid4())
    if mismatch=="kind":requested=replace(requested,kind="draft")
    if mismatch=="unsupported":requested=replace(requested,kind="booking")
    error(404,lambda:AccessService.require_object(identity,requested,None if mismatch=="absent" else target,"unknown_action"))


def test_anonymous_provenance_does_not_grant_new_customer_or_staff_ownership():
    anon=session();customer=session("customer",tenant=anon.tenant_id)
    target=OwnedObject(anon.tenant_id,uuid4(),"conversation",owner_session_id=anon.id,ownership_only=True)
    error(404,lambda:AccessService.require_object(AccessIdentity(customer),key(target),target,"read_conversation"))
    staff=session("staff",tenant=anon.tenant_id,principal=customer.principal_id)
    member=Membership(uuid4(),uuid4(),1,{"admin","cs"},set())
    customer_owned=replace(target,owner_session_id=None,owner_principal_id=staff.principal_id)
    error(404,lambda:AccessService.require_object(AccessIdentity(staff,(member,)),key(customer_owned),customer_owned,"read_conversation"))


@pytest.mark.parametrize("roles,action,expected",[
    ({"admin"},"read_case",404),({"cs"},"read_case",200),({"cs"},"reply_case",200),
    ({"cs"},"assign_case",403),({"supervisor"},"assign_case",200),
    ({"admin","cs"},"read_case",200),({"supervisor"},"unknown",403),
])
def test_staff_case_visibility_and_actions(roles,action,expected):
    s=session("staff");org,queue=uuid4(),uuid4()
    m=Membership(uuid4(),org,1,roles,{queue})
    target=StaffObject(s.tenant_id,uuid4(),"case",org,queue)
    identity=AccessIdentity(s,(m,))
    if expected==200:assert AccessService.require_object(identity,key(target),target,action)==target
    else:error(expected,lambda:AccessService.require_object(identity,key(target),target,action))
    for hidden in [replace(target,tenant_id=uuid4()),replace(target,organization_id=uuid4()),replace(target,queue_id=uuid4())]:
        error(404,lambda:AccessService.require_object(identity,key(hidden),hidden,"unknown"))


def test_roles_from_different_organizations_do_not_combine():
    s=session("staff");orga,orgb,qa,qb=(uuid4() for _ in range(4))
    identity=AccessIdentity(s,(Membership(uuid4(),orga,1,{"admin"},{qa}),Membership(uuid4(),orgb,1,{"cs"},{qb})))
    target=StaffObject(s.tenant_id,uuid4(),"case",orga,qa)
    error(404,lambda:AccessService.require_object(identity,key(target),target,"read_case"))
    target=replace(target,organization_id=orgb,queue_id=qb)
    assert AccessService.require_object(identity,key(target),target,"read_case")==target
    error(404,lambda:AccessService.require_object(identity,key(replace(target,queue_id=qa)),replace(target,queue_id=qa),"read_case"))


def test_membership_self_management_and_admin_visibility_are_distinct():
    s=session("staff");org=uuid4();m=Membership(uuid4(),org,1,{"admin"},set())
    identity=AccessIdentity(s,(m,))
    target=StaffObject(s.tenant_id,m.id,"membership",org,target_principal_id=s.principal_id)
    assert AccessService.require_object(identity,key(target),target,"read_membership")==target
    error(403,lambda:AccessService.require_object(identity,key(target),target,"manage_memberships"))
    # Every membership of the same principal is excluded, not just current membership ID.
    other_self=replace(target,id=uuid4())
    error(403,lambda:AccessService.require_object(identity,key(other_self),other_self,"manage_memberships"))
    other=replace(target,id=uuid4(),target_principal_id=uuid4())
    assert AccessService.require_object(identity,key(other),other,"manage_memberships")==other
    error(404,lambda:AccessService.require_object(identity,key(replace(other,organization_id=uuid4())),replace(other,organization_id=uuid4()),"manage_memberships"))


@pytest.mark.parametrize("bad",["no_owner","both_owners","invalid_owner","unknown_kind","case_queue_missing","member_target_missing"])
def test_malformed_or_unsupported_resource_metadata_fails_closed(bad):
    with pytest.raises(ValueError):
        if bad=="no_owner":OwnedObject(uuid4(),uuid4(),"draft")
        elif bad=="both_owners":OwnedObject(uuid4(),uuid4(),"draft",uuid4(),uuid4())
        elif bad=="invalid_owner":OwnedObject(uuid4(),uuid4(),"draft",owner_principal_id="not-a-uuid")
        elif bad=="unknown_kind":OwnedObject(uuid4(),uuid4(),"case",owner_principal_id=uuid4())
        elif bad=="case_queue_missing":StaffObject(uuid4(),uuid4(),"case",uuid4())
        else:StaffObject(uuid4(),uuid4(),"membership",uuid4())


def test_actor_snapshot_and_membership_collections_are_immutable():
    s=session("staff");roles={"cs"};queues={uuid4()}
    m=Membership(uuid4(),uuid4(),1,roles,queues);members=[m]
    identity=AccessIdentity(s,members)
    roles.add("admin");queues.clear();members.clear()
    assert identity.memberships==(m,) and m.roles==frozenset({"cs"}) and len(m.queue_ids)==1
    assert identity.database_context.tenant_id==s.tenant_id and identity.database_context.actor_kind=="staff"
    with pytest.raises(FrozenInstanceError):identity.session=session()
    with pytest.raises(ValueError):AccessIdentity(session(),(m,))


def test_service_checks_identity_before_lookup_and_reloads_memberships_per_call():
    async def scenario():
        auth=Mock();auth.current=AsyncMock(return_value=None);auth.digest=Mock(return_value="digest")
        repo=Mock();repo.find=AsyncMock()
        reader=Mock();reader.memberships=AsyncMock()
        access=AccessService(auth,reader)
        tenant=uuid4();requested=ResourceKey(tenant,"case",uuid4())
        with pytest.raises(AuthError) as exc:await access.require_resource(tenant,"opaque",requested,repo,"read_case")
        assert exc.value.status==401;repo.find.assert_not_awaited();reader.memberships.assert_not_awaited()
        s=session("staff",tenant=tenant);m=Membership(uuid4(),uuid4(),1,{"cs"},{uuid4()})
        target=StaffObject(tenant,requested.id,"case",m.organization_id,next(iter(m.queue_ids)))
        auth.current.return_value=s;reader.memberships.return_value=(m,);repo.find.return_value=target
        assert await access.require_resource(tenant,"opaque",requested,repo,"read_case")==target
        reader.memberships.return_value=()
        with pytest.raises(AuthError) as exc:await access.require_resource(tenant,"opaque",requested,repo,"read_case")
        assert exc.value.status==401 and repo.find.await_count==1
        assert auth.current.await_count==3 and reader.memberships.await_count==2
    asyncio.run(scenario())


def test_mutation_requires_csrf_before_lookup_and_cross_tenant_key_is_not_queried():
    async def scenario():
        s=session();auth=Mock();auth.current=AsyncMock(return_value=s)
        repo=Mock();repo.find=AsyncMock();access=AccessService(auth)
        requested=ResourceKey(s.tenant_id,"conversation",uuid4())
        auth.validate_csrf.side_effect=AuthError(403,"CSRF_REQUIRED")
        with pytest.raises(AuthError) as exc:await access.require_resource(s.tenant_id,"opaque",requested,repo,"append_message",mutation=True)
        assert exc.value.status==403;repo.find.assert_not_awaited()
        auth.validate_csrf.side_effect=None
        for unsupported in [replace(requested,tenant_id=uuid4()),replace(requested,kind="blob"),replace(requested,kind="transaction")]:
            with pytest.raises(AuthError) as exc:await access.require_resource(s.tenant_id,"opaque",unsupported,repo,"read")
            assert exc.value.status==404
        repo.find.assert_not_awaited()
    asyncio.run(scenario())



def test_unknown_or_source_protected_owned_resource_is_not_opened_by_ownership_alone():
    s=session("customer");identity=AccessIdentity(s)
    protected=OwnedObject(s.tenant_id,uuid4(),"conversation",owner_principal_id=s.principal_id)
    error(404,lambda:AccessService.require_object(identity,key(protected),protected,"read_conversation"))
    general=replace(protected,ownership_only=True)
    assert AccessService.require_object(identity,key(general),general,"read_conversation")==general


def test_known_mutating_action_requires_csrf_even_without_explicit_mutation_flag():
    async def scenario():
        s=session();auth=Mock();auth.current=AsyncMock(return_value=s)
        auth.validate_csrf.side_effect=AuthError(403,"CSRF_REQUIRED")
        repo=Mock();repo.find=AsyncMock()
        with pytest.raises(AuthError) as exc:
            await AccessService(auth).require_resource(s.tenant_id,"opaque",ResourceKey(s.tenant_id,"draft",uuid4()),repo,"confirm_draft")
        assert exc.value.status==403
        auth.validate_csrf.assert_called_once_with(s,None);repo.find.assert_not_awaited()
    asyncio.run(scenario())
