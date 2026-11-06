"""Organization, version, no-self and audit rollback boundaries for staff access."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from airport_assistant.modules.identity.administration import StaffAdministration,MembershipEdit
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError


class Fixture:
    def __init__(self):
        self.actor,self.org,self.target,self.queue=uuid4(),uuid4(),uuid4(),uuid4()
        self.identity=SimpleNamespace(database_context=None,session=SimpleNamespace(kind='staff',principal_id=uuid4()))
        self.workspace=dict(membership_id=str(self.actor),membership_version=1,organization=dict(id=str(self.org),name='Airport services'),
            queues=[dict(id=str(self.queue),name='Parking',active=True)])
        self.member=dict(membership_id=str(uuid4()),principal_id=str(self.target),organization_id=str(self.org),display_name='Test staff',
            principal_active=True,active=True,version=1,roles=['cs'],queue_ids=[str(self.queue)])
        self.audits=0;self.valid=True;self.ready=True;self.allowed=True;self.result=None;self.rows=None;self.scope_changed=False
        self.service=StaffAdministration(self)
    @asynccontextmanager
    async def transaction(self,context):
        old=deepcopy((self.member,self.audits))
        try:yield self
        except BaseException:self.member,self.audits=old;raise
    async def session_active(self):return self.valid
    async def administration_ready(self):return self.ready
    async def admin_workspace(self,id):
        value=deepcopy(self.workspace) if self.allowed and id==self.actor else None
        if self.scope_changed and self.audits:value['membership_version']+=1
        return value
    async def admin_member(self,actor,principal):return deepcopy(self.member) if principal==self.target and actor==self.actor else None
    async def admin_directory(self,*args):return deepcopy(self.rows if self.rows is not None else [self.member])
    async def admin_change(self,principal,command):
        if self.result:return dict(status=self.result)
        same=(self.member['active'],self.member['roles'],self.member['queue_ids'])==(command.active,sorted(command.roles),sorted(map(str,command.queue_ids)))
        if command.expected_version!=self.member['version'] and not(same and command.expected_version+1==self.member['version']):return dict(status='version_conflict')
        if not same:
            self.member.update(active=command.active,roles=sorted(command.roles),queue_ids=sorted(map(str,command.queue_ids)),version=self.member['version']+1);self.audits+=1
        return dict(status='saved',member=deepcopy(self.member))
    def command(self,**changes):return MembershipEdit(**(dict(actor_membership_id=self.actor,actor_version=1,expected_version=1,
        active=True,roles=['supervisor'],queue_ids=[self.queue])|changes))
    def run(self,value):return asyncio.run(value)


def test_current_org_directory_target_metadata_and_idempotent_versioned_update():
    f=Fixture();page=f.run(f.service.directory(f.identity,f.actor,limit=1))
    assert page['items']==[f.member] and not page['has_more'] and page['next_after'] is None
    result=f.run(f.service.update(f.identity,f.target,f.command()))
    assert result['member']['version']==2 and result['member']['roles']==['supervisor'] and f.audits==1
    assert f.run(f.service.update(f.identity,f.target,f.command()))==result and f.audits==1
    with pytest.raises(DraftError) as e:f.run(f.service.update(f.identity,f.target,f.command(roles=['admin'])))
    assert e.value.code=='VERSION_CONFLICT' and f.audits==1


def test_directory_has_real_lookahead_without_total_or_scope_union():
    f=Fixture();second={**f.member,'principal_id':str(uuid4())};f.rows=[f.member,second]
    result=f.run(f.service.directory(f.identity,f.actor,limit=1))
    assert result['items']==[f.member] and result['has_more'] and result['next_after']==f.member['principal_id']
    assert 'total' not in result


def test_self_principal_changes_denied_even_with_another_selected_membership():
    f=Fixture()
    with pytest.raises(AuthError) as e:f.run(f.service.update(f.identity,f.identity.session.principal_id,f.command()))
    assert e.value.status==403 and not f.audits


@pytest.mark.parametrize('status,error', [('actor_changed','MEMBERSHIP_SCOPE_CHANGED'),('version_conflict','VERSION_CONFLICT'),('invalid','VALIDATION_ERROR'),('unknown','STAFF_UNAVAILABLE')])
def test_sql_authority_errors_are_publicly_bounded_and_never_acknowledged(status,error):
    f=Fixture();f.result=status
    with pytest.raises(DraftError) as e:f.run(f.service.update(f.identity,f.target,f.command()))
    assert e.value.code==error and not f.audits


def test_current_scope_and_session_rechecked_before_commit_roll_back_the_change():
    f=Fixture();f.scope_changed=True
    with pytest.raises(DraftError):f.run(f.service.update(f.identity,f.target,f.command()))
    assert f.audits==0 and f.member['version']==1
    f.scope_changed=False;original=f.admin_change
    async def lost(*args):result=await original(*args);f.valid=False;return result
    f.admin_change=lost
    with pytest.raises(AuthError):f.run(f.service.update(f.identity,f.target,f.command()))
    assert f.audits==0 and f.member['version']==1


def test_unavailable_admission_nonadmin_and_foreign_target_do_not_return_data():
    f=Fixture();f.ready=False
    with pytest.raises(DraftError):f.run(f.service.workspace(f.identity,f.actor))
    f.ready=True;f.allowed=False
    with pytest.raises(AuthError):f.run(f.service.directory(f.identity,f.actor))
    f.allowed=True
    with pytest.raises(AuthError):f.run(f.service.get(f.identity,uuid4(),f.actor))
    f.identity.session.kind='customer'
    with pytest.raises(AuthError):f.run(f.service.workspace(f.identity,f.actor))


@pytest.mark.parametrize('changes',[dict(active='true'),dict(actor_version=True),dict(expected_version=0),dict(expected_version=2**60),
    dict(roles=['owner']),dict(roles=['admin','admin']),dict(queue_ids=[uuid4()]*2),dict(tenant_id=str(uuid4())),dict(password='not-an-admin-setting')])
def test_strict_edit_rejects_coercion_claimed_scope_credentials_and_duplicate_permissions(changes):
    f=Fixture()
    with pytest.raises(ValidationError):f.command(**changes)
