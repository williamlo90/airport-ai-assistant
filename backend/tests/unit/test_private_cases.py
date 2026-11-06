"""Case/history and related-case metadata inherit all required source scopes."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from test_private_content import actor, records


def setup(private=False, related=True, allowed=True):
    identity, id, target = actor(), uuid4(), uuid4()
    row = dict(id=id, version=1, resource_security_labels=records()[:1] if private else [],
               duplicate_of_case_id=target if related else None)
    target_row = dict(id=target, version=1, resource_security_labels=records()[1:])
    async def view(case):
        return dict(version=case['version'], history=['PRIVATE'],
            related_case_ref='CASE-'+target.hex if related else None)
    tx = SimpleNamespace(session_active=AsyncMock(return_value=True),
        find_case=AsyncMock(side_effect=lambda *a, **k: deepcopy(row)),
        related_source_context=AsyncMock(side_effect=lambda id: deepcopy(target_row)),
        public_case=AsyncMock(side_effect=view), staff_workspace=AsyncMock(return_value=True))
    active = False
    class Repository:
        @asynccontextmanager
        async def transaction(self, context):
            nonlocal active
            active = True
            try:
                yield tx
            finally:
                active = False
    async def grant(**kwargs):
        assert not active
        return allowed
    access = SimpleNamespace(allows=AsyncMock(side_effect=grant))
    return identity, id, row, target_row, tx, access, CaseLifecycle(Repository(), access)


def test_related_private_case_cannot_be_disclosed_with_only_primary_case_grants():
    identity, id, row, related, tx, access, service = setup(allowed=False)
    with pytest.raises(AuthError) as error:
        asyncio.run(service.get(identity, id))
    assert error.value.status == 404
    assert access.allows.await_args.kwargs['labels'].labels[0].reference == 'PK-A-001'


def test_positive_related_case_uses_union_and_exposes_no_internal_source_metadata():
    identity, id, row, related, tx, access, service = setup(private=True)
    value = asyncio.run(service.get(identity, id))
    assert len(access.allows.await_args.kwargs['labels'].labels) == 2
    assert set(value) == {'version', 'history', 'related_case_ref'}
    assert value['related_case_ref'] == 'CASE-'+related['id'].hex


def test_missing_related_context_cannot_release_a_reference_observed_earlier():
    identity, id, row, related, tx, access, service = setup()
    tx.related_source_context.side_effect = None
    tx.related_source_context.return_value = None
    with pytest.raises(AuthError):
        asyncio.run(service.get(identity, id))
    access.allows.assert_not_awaited()


def test_related_case_version_changed_during_source_await_invalidates_whole_view():
    identity, id, row, related, tx, access, service = setup()
    async def changed(**kwargs):
        related['version'] += 1
        return True
    access.allows.side_effect = changed
    with pytest.raises(DraftError) as error:
        asyncio.run(service.get(identity, id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'


def test_public_to_private_during_history_read_is_not_emitted_without_source_grants():
    identity, id, row, related, tx, access, service = setup(related=False, allowed=False)
    async def changed(case):
        row.update(version=2, resource_security_labels=records())
        return dict(version=case['version'], history=['PRIVATE'], related_case_ref=None)
    tx.public_case.side_effect = changed
    with pytest.raises(AuthError):
        asyncio.run(service.get(identity, id))
    access.allows.assert_awaited_once()


def test_private_basis_changed_during_second_history_read_is_not_emitted():
    identity, id, row, related, tx, access, service = setup(private=True, related=False)
    calls = 0
    async def changed(case):
        nonlocal calls
        calls += 1
        if calls == 2:
            row['version'] += 1
        return dict(version=case['version'], history=['PRIVATE'], related_case_ref=None)
    tx.public_case.side_effect = changed
    with pytest.raises(DraftError) as error:
        asyncio.run(service.get(identity, id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'


def test_selected_staff_membership_rechecked_after_history_wait():
    identity, id, row, related, tx, access, service = setup(related=False)
    staff = SimpleNamespace(session=replace(identity.session, kind='staff'),
        memberships=(object(),), database_context=identity.database_context)
    tx.staff_workspace.side_effect = [True, None]
    with pytest.raises(AuthError) as error:
        asyncio.run(service.get(staff, id, staff=True, membership=uuid4()))
    assert error.value.status == 404
    access.allows.assert_not_awaited()
