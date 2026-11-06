"""Optional feedback semantics, current source authority and bounded failure paths."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from airport_assistant.modules.cases.ratings import CustomerRatings,RatingInput
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure


class Fixture:
    def __init__(self,private=False,kind='customer'):
        self.identity=SimpleNamespace(database_context=None,session=SimpleNamespace(kind=kind,id=uuid4(),principal_id=uuid4(),tenant_id=uuid4()))
        self.id=uuid4();self.clock=[1000.];self.active=0;self.writes=0;self.row=None;self.valid=True;self.fail=False;self.delay=False
        self.parent=dict(version=5,eligible=True,resource_security_labels=[dict(source='reference',resource_type='booking',reference='B1',required_source_scopes=['booking:read'])] if private else [])
        self.source=SimpleNamespace(allows=AsyncMock(return_value=True))
        self.service=CustomerRatings(self,self.source);self.service.clock=lambda:self.clock[0]
    @asynccontextmanager
    async def transaction(self,context):
        saved=deepcopy((self.row,self.writes));self.active+=1
        try:yield self
        except BaseException:self.row,self.writes=saved;raise
        finally:self.active-=1
        if self.delay and self.writes:self.clock[0]+=3
    async def session_active(self):return self.valid
    async def ratings_ready(self):return True
    async def rating_target(self,kind,id,**kwargs):return deepcopy(self.parent) if id==self.id else None
    async def rating(self,*args,**kwargs):return deepcopy(self.row)
    async def save_rating(self,kind,target,id,version,command,labels):
        now=datetime.now(timezone.utc);self.writes+=1
        self.row=dict(id=id,score=command.score,comment=command.comment,version=version+1,
            created_at=self.row['created_at'] if self.row else now,updated_at=now,resource_security_labels=labels)
        if self.fail:raise RuntimeError('Controlled audit rollback')
    def run(self,awaitable):return asyncio.run(awaitable)
    def save(self,**values):return self.run(self.service.save(self.identity,'case',self.id,RatingInput(**values)))


def test_optional_rating_idempotent_desired_state_and_explicit_versioned_change():
    f=Fixture();before=deepcopy(f.parent)
    assert f.run(f.service.get(f.identity,'case',f.id))['rating'] is None and not f.writes
    first=f.save(expected_version=0,score=4,comment='Clear guidance')
    assert first['rating']['version']==1 and f.writes==1
    assert f.save(expected_version=0,score=4,comment='Clear guidance')==first and f.writes==1
    assert f.save(expected_version=1,score=4,comment='Clear guidance')==first and f.writes==1
    second=f.save(expected_version=1,score=2)
    assert second['rating']['version']==2 and second['rating']['comment'] is None and f.writes==2
    assert f.parent==before # No case status, version, assignment or workflow mutation.
    with pytest.raises(DraftError) as e:f.save(expected_version=0,score=5)
    assert e.value.code=='VERSION_CONFLICT' and f.writes==2


def test_ineligible_new_or_changed_feedback_denied_but_current_read_and_identical_retry_remain_safe():
    f=Fixture();f.parent['eligible']=False
    with pytest.raises(DraftError) as e:f.save(expected_version=0,score=4)
    assert e.value.code=='RATING_NOT_AVAILABLE' and not f.writes
    f.parent['eligible']=True;f.save(expected_version=0,score=4);f.parent['eligible']=False
    assert f.save(expected_version=0,score=4)['eligible'] is False and f.writes==1
    assert f.run(f.service.get(f.identity,'case',f.id))['rating']['score']==4
    with pytest.raises(DraftError):f.save(expected_version=1,score=1)
    assert f.writes==1


@pytest.mark.parametrize('values',[dict(score=True),dict(score=0),dict(score=6),dict(score='5'),dict(score=5,expected_version=True),
    dict(score=5,comment=' '),dict(score=5,comment='\0'),dict(score=5,comment='\uD800'),dict(score=5,comment='x'*2001),dict(score=5,tenant_id=str(uuid4()))])
def test_strict_input_rejects_coercion_claimed_scope_and_invalid_comments(values):
    with pytest.raises(ValidationError):RatingInput(**{'expected_version':0,**values})


def test_private_source_check_runs_outside_sql_and_denial_never_reads_or_writes_public_result():
    f=Fixture(private=True)
    async def authorize(**kwargs):assert f.active==0;return True
    f.source.allows.side_effect=authorize
    saved=f.save(expected_version=0,score=5);assert saved['rating']['score']==5
    f.source.allows.side_effect=None;f.source.allows.return_value=False
    for operation in (f.service.get(f.identity,'case',f.id),f.service.save(f.identity,'case',f.id,RatingInput(expected_version=1,score=2))):
        with pytest.raises(AuthError) as e:f.run(operation)
        assert e.value.status==404
    assert f.writes==1


def test_slow_source_deadline_cancel_and_malformed_parent_fail_closed():
    f=Fixture(private=True)
    async def slow(**kwargs):f.clock[0]+=3;return True
    f.source.allows.side_effect=slow
    with pytest.raises(SourceFailure):f.save(expected_version=0,score=3)
    assert not f.writes
    f.parent['resource_security_labels']=None
    with pytest.raises(AuthError):f.run(f.service.get(f.identity,'case',f.id))


def test_session_loss_and_audit_failure_roll_back_entire_feedback():
    f=Fixture();f.fail=True
    with pytest.raises(RuntimeError):f.save(expected_version=0,score=2)
    assert f.row is None and f.writes==0
    f.fail=False;original=f.save_rating
    async def lose(*args):await original(*args);f.valid=False
    f.save_rating=lose
    with pytest.raises(AuthError):f.save(expected_version=0,score=4)
    assert f.row is None and not f.writes


def test_late_private_commit_does_not_return_stale_result_but_fresh_get_recovers_actual_rating():
    f=Fixture(private=True);f.delay=True
    with pytest.raises(SourceFailure):f.save(expected_version=0,score=3)
    assert f.writes==1
    f.delay=False
    assert f.run(f.service.get(f.identity,'case',f.id))['rating']['score']==3


def test_anonymous_is_not_case_owner_and_staff_cannot_rate_any_customer_target():
    f=Fixture(kind='anonymous')
    with pytest.raises(AuthError):f.save(expected_version=0,score=4)
    assert f.run(f.service.save(f.identity,'conversation',f.id,RatingInput(expected_version=0,score=4)))['rating']['score']==4
    f.identity.session.kind='staff'
    with pytest.raises(AuthError):f.run(f.service.get(f.identity,'conversation',f.id))
