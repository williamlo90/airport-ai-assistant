"""A historical confirmation survives legitimate case classification growth."""
from dataclasses import replace
from uuid import uuid4
import pytest
from airport_assistant.modules.identity.auth import AuthError

from test_private_case_confirmation import setup, confirm


@pytest.mark.parametrize('action',['create_case','request_handoff'])
def test_committed_confirmation_retry_checks_expanded_current_grants_without_rewriting_consent(action):
    identity, state, counts, tx, access, service = setup(action=action)
    key = str(uuid4())
    original = state['draft']
    receipt = confirm(service, identity, state, key)
    addition = dict(source='reference', resource_type='booking', reference='LATER-BOOKING',
                    required_source_scopes=['booking:read'])
    expanded = list(original.resource_security_labels) + [addition]
    # Model a later authorized case update and the mandatory parent/child union.
    # The approved original preview/hash remains immutable.
    state['case']['resource_security_labels'] = expanded
    state['parent']['resource_security_labels'] = expanded
    state['parent']['version'] += 1
    state['conversation'] = replace(state['conversation'],
        resource_security_labels=tuple(expanded), version=3)
    result = confirm(service, identity, state, key)
    assert result == receipt and counts['writes'] == 1
    assert confirm(service,identity,state)==receipt and counts['writes']==1
    labels = access.allows.await_args.kwargs['labels'].to_records()
    assert addition in labels
    assert state['draft'].payload_hash == original.payload_hash
    assert state['draft'].source_evidence == original.source_evidence


@pytest.mark.parametrize('action',['create_case','request_handoff'])
def test_only_the_later_required_grant_can_block_historical_confirmation_replay(action):
    identity,state,counts,tx,access,service=setup(action=action)
    key=str(uuid4());original=state['draft'];confirm(service,identity,state,key)
    addition=dict(source='reference',resource_type='booking',reference='LATER-BOOKING',required_source_scopes=['booking:read'])
    expanded=list(original.resource_security_labels)+[addition]
    state['case']['resource_security_labels']=expanded
    state['parent']['resource_security_labels']=expanded;state['parent']['version']+=1
    state['conversation']=replace(state['conversation'],resource_security_labels=tuple(expanded),version=3)
    async def current(**kwargs):
        # Every original grant remains valid; just the new resource was revoked.
        return not any(label.reference=='LATER-BOOKING' for label in kwargs['labels'].labels)
    access.allows.side_effect=current
    with pytest.raises(AuthError):confirm(service,identity,state,key)
    assert counts['writes']==1 and state['draft'].payload_hash==original.payload_hash
