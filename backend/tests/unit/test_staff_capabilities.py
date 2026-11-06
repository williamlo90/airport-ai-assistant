"""Selected scope policy parity and signed cursor isolation; no database."""
from uuid import uuid4
import pytest
from airport_assistant.modules.cases.capabilities import allowed, AssigneeCursor
from airport_assistant.modules.drafts.models import DraftError


@pytest.mark.parametrize('mode',[None,'waiting_staff','human'])
@pytest.mark.parametrize('supervisor',[False,True])
def test_unassigned_claim_and_assignment_follow_existing_chat_rules(mode,supervisor):
    mid=uuid4();result=allowed(dict(status='new',assigned_membership_id=None),dict(supervisor=supervisor,membership_id=mid),dict(mode=mode) if mode else None,None)
    assert ('claim' in result['allowed_actions'])==(not supervisor and mode is None)
    assert ('assign' in result['allowed_actions'])==supervisor
    assert result['self_claim_membership_id']==(mid if not supervisor and mode is None else None)
    assert result['allowed_statuses']==[] and result['allowed_close_reasons']


def test_other_assigned_cs_has_status_actions_but_no_assignment_and_invalid_assignee_only_closes():
    case=dict(status='in_progress',assigned_membership_id=uuid4());scope=dict(supervisor=False,membership_id=uuid4())
    result=allowed(case,scope,None,case['assigned_membership_id'])
    assert result['allowed_actions']==['update_status','close']
    assert result['allowed_statuses']==['awaiting_customer','awaiting_external','in_progress']
    result=allowed(case,scope,None,None)
    assert result['allowed_actions']==['close'] and not result['allowed_statuses']


@pytest.mark.parametrize('mode',[None,'waiting_staff','human'])
def test_supervisor_can_unassign_assigned_new_only_outside_human_chat(mode):
    result=allowed(dict(status='new',assigned_membership_id=uuid4()),dict(supervisor=True,membership_id=uuid4()),dict(mode=mode) if mode else None,uuid4())
    assert ('unassign' in result['allowed_actions'])==(mode!='human')


def test_closed_snapshot_offers_no_mutations_or_self_claim():
    result=allowed(dict(status='closed',assigned_membership_id=None),dict(supervisor=True,membership_id=uuid4()),None,None)
    assert result==dict(allowed_actions=[],allowed_statuses=[],allowed_close_reasons=[],self_claim_membership_id=None)


def test_cursor_binds_every_scope_field_and_uuid_position(monkeypatch):
    monkeypatch.setattr('airport_assistant.modules.cases.capabilities.time.time',lambda:1000)
    codec=AssigneeCursor(b'x'*32);scope=['tenant','session','actor','case',1,'member',1,'org','queue',25];position=uuid4()
    token=codec.issue(scope,position);assert len(token)<2048 and codec.read(token,scope)==position
    for i in range(len(scope)):
        other=scope.copy();other[i]='changed'
        with pytest.raises(DraftError):codec.read(token,other)
    with pytest.raises(DraftError):codec.read(token+'a',scope)
    for invalid in ('','x'*2049,'not/cursor','☃'):
        with pytest.raises(DraftError):codec.read(invalid,scope)
    monkeypatch.setattr('airport_assistant.modules.cases.capabilities.time.time',lambda:1900)
    with pytest.raises(DraftError):codec.read(token,scope)
