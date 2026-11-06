"""Preview and recovery receipts never substitute for current source permission."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.drafts.models import seal, displayed_summary, DraftError
from airport_assistant.modules.drafts.service import DraftService
from airport_assistant.modules.cases.service import CaseService
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from test_draft_rules import setup
from test_case_rules import case_setup
from test_private_content import records


def draft_setup(allowed=True, private=True):
    old, conv, draft, tx, outcomes, base = setup()
    principal = uuid4()
    identity = replace(old, session=replace(old.session, kind='customer', principal_id=principal))
    conv = replace(conv, customer_principal_id=principal, anonymous_session_id=None,
                   resource_security_labels=tuple(records()[1:]) if private else ())
    draft = seal(replace(draft, actor_principal_id=principal, actor_session_id=None,
                         resource_security_labels=tuple(records()[:1]) if private else ()))
    tx.find.return_value, tx.find_draft.return_value = conv, draft
    tx.source_case_context = AsyncMock()
    tx.selected_attachment_context = AsyncMock(return_value=[])
    async def grant(**kwargs):
        assert outcomes[-1] == 'commit'  # No source network inside that DB unit.
        if isinstance(allowed, Exception):
            raise allowed
        return allowed
    access = SimpleNamespace(allows=AsyncMock(side_effect=grant))
    service = DraftService(base._repository, access)
    return identity, conv, draft, tx, access, service


@pytest.mark.parametrize('allowed', [False, SourceFailure(503)])
def test_private_preview_not_emitted_when_source_denied_or_unavailable(allowed):
    identity, conv, draft, tx, access, service = draft_setup(allowed)
    with pytest.raises((AuthError, SourceFailure)):
        asyncio.run(service.get(identity, draft.id))
    assert tx.find_draft.await_count == 1


def test_positive_preview_preserves_original_hash_and_requires_parent_union():
    identity, conv, draft, tx, access, service = draft_setup()
    assert asyncio.run(service.get(identity, draft.id)) == draft
    assert len(access.allows.await_args.kwargs['labels'].labels) == 2
    assert tx.find_draft.await_count == 2


def test_new_draft_hash_while_source_waits_is_not_accepted_as_prior_preview():
    identity, conv, draft, tx, access, service = draft_setup()
    async def changed(**kwargs):
        tx.find_draft.return_value = seal(replace(draft, version=2))
        return True
    access.allows.side_effect = changed
    with pytest.raises(DraftError) as error:
        asyncio.run(service.get(identity, draft.id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'


def test_verified_owner_still_cannot_read_previous_browser_preview():
    identity, conv, draft, tx, access, service = draft_setup()
    tx.find_draft.return_value = seal(replace(draft, created_session_id=uuid4()))
    with pytest.raises(AuthError):
        asyncio.run(service.get(identity, draft.id))
    access.allows.assert_not_awaited()


@pytest.mark.parametrize('allowed', [True, False])
def test_private_expired_read_does_not_write_an_expiry_marker_before_grants(allowed):
    identity, conv, draft, tx, access, service = draft_setup(allowed)
    tx.now.return_value = draft.expires_at
    if allowed:
        assert asyncio.run(service.get(identity, draft.id)).state == 'expired'
    else:
        with pytest.raises(AuthError):
            asyncio.run(service.get(identity, draft.id))
    tx.terminal.assert_not_awaited()


def test_selected_file_labels_are_required_even_if_parent_was_not_yet_classified():
    identity, conv, draft, tx, access, service = draft_setup(False, private=False)
    id = uuid4()
    payload = draft.payload.model_copy(update={'attachment_ids': (id,)})
    draft = seal(replace(draft, payload=payload,
        displayed_summary=displayed_summary(draft.action_type, payload, draft.destination, draft.language)))
    tx.find_draft.return_value = draft
    tx.selected_attachment_context.return_value = [dict(id=id, content_hash='a'*64,
                                                        resource_security_labels=records())]
    with pytest.raises(AuthError):
        asyncio.run(service.get(identity, draft.id))
    access.allows.assert_awaited_once()


def test_missing_selected_file_is_not_silently_omitted_from_preview():
    identity, conv, draft, tx, access, service = draft_setup()
    draft = seal(replace(draft, payload=draft.payload.model_copy(update={'attachment_ids': (uuid4(),)})))
    tx.find_draft.return_value = draft
    with pytest.raises(AuthError):
        asyncio.run(service.get(identity, draft.id))
    access.allows.assert_not_awaited()


def receipt_setup(allowed=True):
    identity, draft, tx, outcomes, base, result = case_setup()
    parent = dict(id=result.case_id, version=1, resource_security_labels=records())
    tx.receipt = AsyncMock(return_value=dict(draft_id=draft.id,state='committed',case_id=result.case_id,
        result_ref=result.result_ref,version=draft.version,expires_at=None))
    tx.source_case_context = AsyncMock(side_effect=lambda id: dict(parent))
    async def grant(**kwargs):
        assert outcomes[-1] == 'commit'
        if isinstance(allowed, Exception):
            raise allowed
        return allowed
    access = SimpleNamespace(allows=AsyncMock(side_effect=grant))
    return identity, draft, tx, parent, access, CaseService(base._repository, access), result


def test_committed_receipt_checks_current_case_grants_on_every_recovery():
    identity, draft, tx, parent, access, service, result = receipt_setup()
    assert asyncio.run(service.status(identity, draft.id)) == tx.receipt.return_value
    access.allows.side_effect = None
    access.allows.return_value = False
    with pytest.raises(AuthError):
        asyncio.run(service.status(identity, draft.id))
    assert access.allows.await_count == 2


@pytest.mark.parametrize('allowed', [False, SourceFailure(503)])
def test_recovery_receipt_cannot_disclose_case_ids_when_source_check_fails(allowed):
    identity, draft, tx, parent, access, service, result = receipt_setup(allowed)
    with pytest.raises((AuthError, SourceFailure)):
        asyncio.run(service.status(identity, draft.id))
    assert tx.receipt.await_count == 1


def test_receipt_case_basis_changed_during_source_wait_requires_new_read():
    identity, draft, tx, parent, access, service, result = receipt_setup()
    async def changed(**kwargs):
        parent['version'] += 1
        return True
    access.allows.side_effect = changed
    with pytest.raises(DraftError):
        asyncio.run(service.status(identity, draft.id))


def test_receipt_disappearing_after_grants_does_not_fall_back_to_a_preview():
    identity, draft, tx, parent, access, service, result = receipt_setup()
    tx.receipt.side_effect = [dict(tx.receipt.return_value), None]
    service._drafts.get = AsyncMock(side_effect=AssertionError('No fallback after lost receipt ACL'))
    with pytest.raises(AuthError) as error:
        asyncio.run(service.status(identity, draft.id))
    assert error.value.status == 404
    service._drafts.get.assert_not_awaited()


def test_pending_action_uses_its_session_bound_preview_instead_of_a_fake_receipt():
    identity, draft, tx, parent, access, service, result = receipt_setup()
    tx.receipt.return_value = None
    service._drafts.get = AsyncMock(return_value=draft)
    assert asyncio.run(service.status(identity, draft.id)) == draft
    access.allows.assert_not_awaited()
    service._drafts.get.assert_awaited_once_with(identity, draft.id)
