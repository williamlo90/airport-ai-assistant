"""A pending source-bearing draft is created only through the authorized unit."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.drafts.models import DraftError,require_integrity
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from test_private_draft_mutations import setup as draft_setup


def setup(allowed=True,parent=False):
    identity,state,counts,tx,access,service=draft_setup(allowed=allowed)
    original=state['draft'];state['draft']=None
    tx.locate_draft.side_effect=lambda id:state['conversation'].id if state['draft'] and state['draft'].id==id else None
    tx.find_draft.side_effect=lambda id:state['draft'] if state['draft'] and state['draft'].id==id else None
    async def create(draft):
        # Actual DraftSQL row decoder marks a visible case-bound row only after
        # the current RLS read; model this repository projection, not a bypass.
        if draft.case_id is not None:draft=replace(draft,case_access_verified=True)
        counts['writes']+=1;state['draft']=draft
        return draft
    tx.create_draft.side_effect=create
    target=None
    if parent:
        case_id=uuid4()
        state['parent']=dict(id=case_id,version=1,resource_security_labels=list(original.resource_security_labels))
        target=dict(id=case_id,status='in_progress',message_id=uuid4(),result_ref='CASE-'+case_id.hex,
            destination=original.destination)
        tx.handoff_case=AsyncMock(side_effect=lambda *a,**k:target)
    return identity,state,counts,tx,access,service,original,target


def command(identity,state,service,original,target=None,*,evidence=True,expected=None):
    return asyncio.run(service.create(identity,state['conversation'].id,
        'request_handoff' if target else 'create_case',original.payload,
        case_id=target['id'] if target else None,
        source_evidence=original.source_evidence if evidence else None,
        expected_conversation_version=expected,expected_generation_epoch=1 if expected is not None else None))


def test_positive_snapshot_creation_checks_grants_and_persists_exact_human_review():
    identity,state,counts,tx,access,service,original,target=setup()
    result=command(identity,state,service,original)
    assert result==state['draft'] and result.source_evidence==original.source_evidence
    assert result.resource_security_labels==original.resource_security_labels
    assert result.id!=original.id and result.payload_hash!=original.payload_hash
    assert result.source_evidence.display(result.language) in result.displayed_summary
    assert counts==dict(commits=2,rollbacks=0,writes=1)
    assert state['conversation'].resource_security_labels==()
    require_integrity(result)
    assert len(access.allows.await_args.kwargs['labels'].labels)==2


@pytest.mark.parametrize('allowed',[False,None,'true',SourceFailure(503)])
def test_denied_or_unavailable_new_snapshot_never_creates_a_draft(allowed):
    identity,state,counts,tx,access,service,original,target=setup(allowed)
    with pytest.raises((AuthError,SourceFailure)):command(identity,state,service,original)
    assert state['draft'] is None and counts['writes']==0
    tx.create_draft.assert_not_awaited()


def test_root_conversation_requirements_are_inherited_even_without_new_snapshot():
    identity,state,counts,tx,access,service,original,target=setup()
    state['conversation']=replace(state['conversation'],resource_security_labels=original.resource_security_labels)
    result=command(identity,state,service,original,evidence=False)
    assert result.resource_security_labels==original.resource_security_labels and result.source_evidence is None
    access.allows.assert_awaited_once()


def test_existing_private_case_handoff_requires_its_current_grants():
    identity,state,counts,tx,access,service,original,target=setup(parent=True)
    result=command(identity,state,service,original,target,evidence=False)
    assert result.case_id==target['id'] and result.resource_security_labels==original.resource_security_labels
    assert len(access.allows.await_args.kwargs['labels'].labels)==2


@pytest.mark.parametrize('change',['version','session','owner','parent'])
def test_changes_during_grant_wait_cannot_create_an_unreviewed_new_basis(change):
    identity,state,counts,tx,access,service,original,target=setup(parent=True)
    async def changed(**kwargs):
        if change=='version':state['conversation']=replace(state['conversation'],version=2)
        elif change=='session':state['active']=False
        elif change=='owner':state['conversation']=replace(state['conversation'],customer_principal_id=uuid4())
        else:state['parent']['version']+=1
        return True
    access.allows.side_effect=changed
    with pytest.raises((AuthError,DraftError)):command(identity,state,service,original,target,evidence=False)
    assert state['draft'] is None and counts['writes']==0


@pytest.mark.parametrize('change',['labels','evidence','hash'])
def test_postloader_detects_storage_that_dropped_the_approved_snapshot(change):
    identity,state,counts,tx,access,service,original,target=setup()
    async def corrupt(draft):
        counts['writes']+=1
        state['draft']=replace(draft,**{'labels':{'resource_security_labels':()},
            'evidence':{'source_evidence':None},'hash':{'payload_hash':'0'*64}}[change])
        return draft
    tx.create_draft.side_effect=corrupt
    with pytest.raises(DraftError):command(identity,state,service,original)
    assert state['draft'] is None and counts['rollbacks']==1


def test_legacy_in_transaction_entry_cannot_persist_private_target_without_a_gate():
    identity,state,counts,tx,access,service,original,target=setup(parent=True)
    async def run():
        async with service._repository.transaction(identity.database_context) as unit:
            return await service.create_in_transaction(unit,identity,state['conversation'].id,'request_handoff',
                original.payload,case_id=target['id'])
    with pytest.raises(AuthError):asyncio.run(run())
    tx.create_draft.assert_not_awaited()


def test_stale_expected_generation_cannot_create_a_source_draft():
    identity,state,counts,tx,access,service,original,target=setup()
    with pytest.raises(DraftError):command(identity,state,service,original,expected=2)
    assert counts['writes']==0


def test_existing_case_handoff_cannot_claim_to_update_its_snapshot_with_new_evidence():
    identity,state,counts,tx,access,service,original,target=setup(parent=True)
    with pytest.raises(DraftError) as error:command(identity,state,service,original,target,evidence=True)
    assert error.value.status==422 and counts['commits']==0 and counts['writes']==0


def test_public_report_creation_remains_one_transaction_with_no_source_calls():
    identity,state,counts,tx,access,service,original,target=setup()
    result=command(identity,state,service,original,evidence=False)
    assert result.source_evidence is None and result.resource_security_labels==()
    assert counts==dict(commits=1,rollbacks=0,writes=1)
    access.allows.assert_not_awaited()
