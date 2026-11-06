"""A valid customer owner still needs current source access on continue/replay."""
from copy import deepcopy
from uuid import uuid4

import pytest

from test_customer_information import Fixture as Base
from airport_assistant.modules.cases.lifecycle import CaseLifecycle,ContinueCase
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure


class Fixture(Base):
    def __init__(self,private=True):
        super().__init__(private)
        self.case.update(status='closed',duplicate_of_case_id=None)
        self.events=[];self.previous=None;self.participant=True;self.session=True;self.slow_exit=False
        self.mono=[0]
        self.lifecycle=CaseLifecycle(self,self.access)
        self.lifecycle._private_content.clock=lambda:self.mono[0]
        self.command=ContinueCase(expected_version=3,intent='same_issue',message='Still need help.',client_request_id=uuid4())
    async def session_active(self):return self.session
    async def continuation_ancestry(self,id):return deepcopy(self.case['resource_security_labels']) if self.participant else None
    async def reopen_receipt(self,id,key):return deepcopy(self.previous)
    async def public_case(self,case):
        if self.slow_exit and self.events:self.mono[0]=2.01
        return {**deepcopy(case),'history':deepcopy(self.events)}
    async def current_assignee(self,id):return None
    async def update_case(self,case,values):
        self.writes+=1;self.case.update(**values,version=case['version']+1)
        return deepcopy(self.case)
    async def history(self,before,after,event,*,command,digest):
        self.events.append(dict(event_type=event,resulting_version=after['version']))
        self.previous=dict(request_hash=digest)
    def continue_case(self):return self.run(self.lifecycle.continue_case(self.identity,self.cid,self.command))


def test_denied_private_continue_does_not_publish_or_return_case_body():
    f=Fixture();f.access.allows.return_value=False
    with pytest.raises(AuthError) as e:f.continue_case()
    assert e.value.status==404 and f.writes==0 and f.case['version']==3 and not f.events


def test_allowed_once_then_revoked_replay_cannot_read_private_case():
    f=Fixture();saved=f.continue_case()
    assert saved['status']=='new' and saved['version']==4 and f.writes==1
    assert f.continue_case()==saved and f.writes==1
    f.access.allows.return_value=False
    with pytest.raises(AuthError):f.continue_case()
    assert f.writes==1 and len(f.events)==1


@pytest.mark.parametrize('change',['session','participant','version','labels'])
def test_current_authority_and_parent_reloaded_after_grants_before_write(change):
    f=Fixture()
    async def revoke(**kwargs):
        assert f.active==0
        if change=='session':f.session=False
        elif change=='participant':f.participant=False
        elif change=='version':f.case['version']+=1
        else:f.case['resource_security_labels'][0]['reference']='B2'
        return True
    f.access.allows.side_effect=revoke
    with pytest.raises((AuthError,DraftError)):f.continue_case()
    assert f.writes==0 and not f.events


def test_pregrant_deadline_cannot_reanchor_after_delayed_allow():
    f=Fixture()
    async def slow(**kwargs):f.mono[0]=2.01;return True
    f.access.allows.side_effect=slow
    with pytest.raises(SourceFailure):f.continue_case()
    assert f.writes==0


def test_public_continue_and_historical_replay_remain_one_transaction_each():
    f=Fixture(private=False);saved=f.continue_case()
    assert f.continue_case()==saved and f.writes==1
    f.access.allows.assert_not_awaited()
