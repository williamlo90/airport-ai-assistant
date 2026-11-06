"""SRC-03B foundation: exercise emission after real business authorization awaits.

These are controlled service tests, not the positive SQL/private-case gate.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.identity.auth import AuthError, Session
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.source.labels import SecurityLabels, PrivateSourceAccess
from airport_assistant.modules.source.private_content import PrivateProjection, PrivateContentGate
from airport_assistant.modules.source.models import SourceFailure


def actor(kind='customer'):
    return AccessIdentity(Session(uuid4(), uuid4(), uuid4() if kind == 'customer' else None,
        kind, datetime.now(timezone.utc) + timedelta(hours=1), 'test-proof'))


def records():
    return [dict(source='reference', resource_type='transaction', reference='TX-A-001',
                 required_source_scopes=['payment_status:read', 'payment_details:read']),
            dict(source='reference', resource_type='booking', reference='PK-A-001',
                 required_source_scopes=['booking:read'])]


def projection(identity, id, *, labels=None, version=1, value='PRIVATE PAYMENT SNAPSHOT'):
    return PrivateProjection.capture(identity.session.tenant_id, id, value=value,
        basis={'version': version}, records=records() if labels is None else labels)


def test_positive_composite_checks_all_scopes_outside_transactions_and_reloads_acl():
    identity, id = actor(), uuid4()
    loaded = []
    in_transaction = False
    async def load():
        nonlocal in_transaction
        in_transaction = True
        try:
            loaded.append('current_acl')
            return projection(identity, id)
        finally:
            in_transaction = False
    async def allows(**kwargs):
        assert not in_transaction
        assert kwargs['tenant_id'] == identity.session.tenant_id
        assert kwargs['principal_id'] == identity.session.principal_id
        assert sum(len(x.required_source_scopes) for x in kwargs['labels'].labels) == 3
        return True
    gate = PrivateContentGate(SimpleNamespace(allows=allows))
    assert asyncio.run(gate.read(identity, load)) == 'PRIVATE PAYMENT SNAPSHOT'
    assert loaded == ['current_acl', 'current_acl']


@pytest.mark.parametrize('denied', ['booking', 'transaction'])
def test_booking_grant_never_substitutes_for_payment_grants(denied):
    identity, id = actor(), uuid4()
    async def has_grants(**kwargs):
        return kwargs['resource_type'] != denied
    grants = SimpleNamespace(has_grants=AsyncMock(side_effect=has_grants))
    load = AsyncMock(return_value=projection(identity, id))
    with pytest.raises(AuthError) as error:
        asyncio.run(PrivateContentGate(PrivateSourceAccess(grants)).read(identity, load))
    assert error.value.status == 404 and 'PRIVATE' not in str(error.value)
    assert load.await_count == 1  # No candidate was returned after denial.


@pytest.mark.parametrize('failure', [False, None, 1, 'true', SourceFailure(503), SourceFailure(502)])
def test_revocation_unavailability_or_malformed_source_never_emits_candidate(failure):
    identity, id = actor(), uuid4()
    access = SimpleNamespace(allows=AsyncMock())
    if isinstance(failure, Exception):
        access.allows.side_effect = failure
    else:
        access.allows.return_value = failure
    load = AsyncMock(return_value=projection(identity, id))
    with pytest.raises((AuthError, SourceFailure)):
        asyncio.run(PrivateContentGate(access).read(identity, load))
    assert load.await_count == 1


@pytest.mark.parametrize('changed', ['version', 'labels', 'resource', 'tenant', 'session'])
def test_changed_basis_or_revoked_acl_after_source_wait_is_not_emitted(changed):
    identity, id = actor(), uuid4()
    first = projection(identity, id)
    if changed == 'session':
        second = AuthError(401, 'AUTH_REQUIRED')
    elif changed == 'tenant':
        second = projection(actor(), id)
    else:
        second = projection(identity, uuid4() if changed == 'resource' else id,
            labels=records()[:1] if changed == 'labels' else None,
            version=2 if changed == 'version' else 1)
    load = AsyncMock(side_effect=[first, second])
    gate = PrivateContentGate(SimpleNamespace(allows=AsyncMock(return_value=True)))
    with pytest.raises((AuthError, DraftError)):
        asyncio.run(gate.read(identity, load))


def test_slow_database_wait_expires_check_instead_of_returning_stale_permission():
    identity, id = actor(), uuid4()
    ticks = iter([100, 102])
    gate = PrivateContentGate(SimpleNamespace(allows=AsyncMock(return_value=True)),
                              clock=lambda: next(ticks))
    with pytest.raises(SourceFailure) as error:
        asyncio.run(gate.read(identity, AsyncMock(return_value=projection(identity, id))))
    assert error.value.status == 503


@pytest.mark.parametrize('labels', [None, {}, '[]', [dict(source='reference')]])
def test_missing_or_malformed_stored_labels_do_not_declassify(labels):
    identity = actor()
    with pytest.raises(AuthError):
        PrivateProjection.capture(identity.session.tenant_id, uuid4(),
                                  value='PRIVATE', basis={'version': 1}, records=labels)


def test_staff_purpose_requires_operational_loader_not_customer_source_grants():
    customer = actor()
    staff = SimpleNamespace(session=SimpleNamespace(tenant_id=customer.session.tenant_id,
        principal_id=uuid4(), kind='staff'), memberships=(object(),))
    access = SimpleNamespace(allows=AsyncMock(side_effect=AssertionError('Not customer grants')))
    load = AsyncMock(return_value=projection(customer, uuid4()))
    assert asyncio.run(PrivateContentGate(access).read(staff, load, purpose='staff'))
    assert load.await_count == 2 and not access.allows.called
    with pytest.raises(AuthError):
        asyncio.run(PrivateContentGate(access).read(customer, load, purpose='staff'))


def test_no_configured_source_denies_private_but_owned_public_report_keeps_working():
    identity, id = actor(), uuid4()
    with pytest.raises(AuthError):
        asyncio.run(PrivateContentGate().read(identity, AsyncMock(return_value=projection(identity, id))))
    value = asyncio.run(PrivateContentGate().read(identity,
        AsyncMock(return_value=projection(identity, id, labels=[], value='User report'))))
    assert value == 'User report'
    assert 'PRIVATE' not in repr(projection(identity, id))


def test_case_service_never_returns_history_from_before_grant_wait_and_rechecks_staff_scope():
    identity, id = actor(), uuid4()
    state = {'version': 1, 'resource_security_labels': records()}
    in_transaction = False
    tx = SimpleNamespace(session_active=AsyncMock(return_value=True),
        find_case=AsyncMock(side_effect=lambda *a, **k: dict(state)),
        public_case=AsyncMock(side_effect=lambda c: {'version': c['version'], 'history': ['PRIVATE']}))
    class Repository:
        @asynccontextmanager
        async def transaction(self, context):
            nonlocal in_transaction
            in_transaction = True
            try:
                yield tx
            finally:
                in_transaction = False
    async def revoke_during_check(**kwargs):
        assert not in_transaction
        state['version'] += 1
        return True
    service = CaseLifecycle(Repository(), SimpleNamespace(allows=revoke_during_check))
    with pytest.raises(DraftError) as error:
        asyncio.run(service.get(identity, id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'
    assert tx.session_active.await_count == 4
