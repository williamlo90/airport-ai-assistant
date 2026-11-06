"""Mail history never exposes quarantine or bypasses current staff scope."""
import asyncio
import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.cases.lifecycle import CaseLifecycle
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.mail.delivery import DeliveryStatus
from airport_assistant.modules.mail.history import StaffMailHistory, GROUPS
from test_private_outbound import BOOKING, PAYMENT


class Store:
    def __init__(self, view='replies', private=True):
        self.id = uuid4(); self.tenant = uuid4()
        self.identity = SimpleNamespace(session=SimpleNamespace(kind='staff', tenant_id=self.tenant),
            memberships=(object(),), database_context=object())
        self.case = dict(id=self.id, version=1, resource_security_labels=[copy.deepcopy(BOOKING)] if private else [])
        self.rows = [dict(email_id=str(uuid4()), state='published', reason=None, app_message_id=str(uuid4()),
            created_at='2026-11-05T00:00:00+00:00', body='Authorized published response.',
            verification_state='verified', send_state=None, attempts=None,
            verification_response_hash='must-not-leak', body_html='must-not-leak')]
        if view == 'deliveries':
            self.rows = [dict(job_id=str(uuid4()), app_message_id=str(uuid4()), email_message_id=str(uuid4()),
                delivery_status='accepted', delivery_version=1, needs_review=False, review_reason=None,
                queued_at='2026-11-05T00:00:00+00:00', provider_status_at=None, last_checked_at=None,
                recipient='private-address@example.com')]
        self.view = view
        self.allowed = self.session = True
        self.counts = {}; self.hooks = {}; self.scope_version = 1
        self.context_overrides = {}
        self.personal_source = AsyncMock(side_effect=AssertionError('No personal staff source grant'))
        self.loader = StaffMailHistory(self, private_history=private)

    def hook(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1
        hook = self.hooks.get((name, self.counts[name]))
        if hook:
            hook(self)

    @asynccontextmanager
    async def transaction(self, context):
        assert context is self.identity.database_context
        yield self

    async def session_active(self):
        self.hook('session')
        return self.session

    async def find_case(self, id):
        assert id == self.id
        self.hook('case')
        return copy.deepcopy(self.case) if self.allowed else None

    async def email_replies(self, id):
        self.hook('rows')
        return copy.deepcopy(self.rows)

    async def deliveries(self, id):
        return await self.email_replies(id)

    async def mail_history_context(self, id, view):
        assert id == self.id and view == self.view
        self.hook('context')
        if not self.allowed:
            return None
        raw = dict(classification_version=2, tenant_id=str(self.tenant), case_id=str(self.id),
            view=view, case_version=self.case['version'], scope_version=self.scope_version,
            items=copy.deepcopy(self.rows), labels=copy.deepcopy(self.case['resource_security_labels']))
        raw.update({key: [] for key in GROUPS})
        raw['case_labels'] = copy.deepcopy(self.case['resource_security_labels'])
        raw.update(copy.deepcopy(self.context_overrides))
        return raw

    def run(self):
        return asyncio.run(self.loader.read(self.identity, self.id, self.view))


@pytest.mark.parametrize('view', ['replies', 'deliveries'])
def test_current_staff_scope_can_read_private_history_without_personal_grants(view):
    f = Store(view)
    result = f.run()
    assert result
    assert f.counts['context'] == 4
    f.personal_source.assert_not_awaited()
    assert 'must-not-leak' not in str(result)
    assert 'private-address@example.com' not in str(result)


@pytest.mark.parametrize('state', ['pending', 'review', 'rejected', 'challenged', 'verification_reply'])
def test_quarantined_or_unverified_body_is_not_returned(state):
    f = Store(); f.rows[0].update(state=state, body='Private quarantined body')
    result = f.run()
    assert result[0]['body'] is None
    assert 'Private quarantined body' not in str(result)
    assert 'verification_response_hash' not in result[0] and 'body_html' not in result[0]


def test_published_state_without_linked_app_message_does_not_expose_body():
    f = Store(); f.rows[0]['app_message_id'] = None
    assert f.run()[0]['body'] is None


@pytest.mark.parametrize('bad', [True, 'invalid', 1, {}])
def test_invalid_published_message_pointer_is_not_permission_to_read_body(bad):
    f = Store(); f.rows[0]['app_message_id'] = bad
    with pytest.raises(AuthError):
        f.run()


@pytest.mark.parametrize('boundary', [('context', 2), ('case', 2), ('session', 2), ('context', 4)])
def test_revoked_operational_scope_at_each_boundary_returns_no_history(boundary):
    f = Store(); f.hooks[boundary] = lambda store: setattr(store, 'allowed', False)
    with pytest.raises(AuthError):
        f.run()


@pytest.mark.parametrize('key,value', [
    ('tenant_id', str(uuid4())), ('case_id', str(uuid4())), ('classification_version', True),
    ('case_version', 2), ('scope_version', 0), ('view', 'customer'), ('labels', []), ('items', None)])
def test_wrong_projection_identity_or_classification_never_returns_rows(key, value):
    f = Store(); f.context_overrides[key] = value
    with pytest.raises(AuthError):
        f.run()


@pytest.mark.parametrize('group', GROUPS)
def test_missing_child_classification_fails_closed(group):
    f = Store(); f.context_overrides[group] = None
    with pytest.raises(AuthError):
        f.run()


@pytest.mark.parametrize('boundary', [2, 3, 4])
def test_scope_or_items_changed_during_materialization_are_not_emitted(boundary):
    f = Store()
    f.hooks[('context', boundary)] = lambda store: setattr(store, 'scope_version', 2)
    with pytest.raises((AuthError, DraftError)):
        f.run()


def test_changed_published_body_after_initial_projection_is_not_emitted():
    f = Store(); f.hooks[('context', 2)] = lambda store: store.rows[0].update(body='Changed')
    with pytest.raises(AuthError):
        f.run()


@pytest.mark.parametrize('view', ['replies', 'deliveries'])
def test_default_off_denies_private_parent_without_calling_private_rpc(view):
    f = Store(view)
    f.loader = StaffMailHistory(f)
    with pytest.raises(AuthError):
        f.run()
    assert 'context' not in f.counts


@pytest.mark.parametrize('view', ['replies', 'deliveries'])
def test_default_off_denies_private_child_under_public_looking_parent(view):
    f = Store(view, private=False); f.rows[0]['email_labels'] = [PAYMENT]
    with pytest.raises(AuthError):
        f.run()


def test_public_history_rechecks_case_after_query_and_session_await():
    f = Store(private=False)
    f.hooks[('rows', 1)] = lambda store: setattr(store, 'allowed', False)
    with pytest.raises(AuthError):
        f.run()


def test_public_to_private_during_query_is_denied():
    f = Store(private=False)
    f.hooks[('rows', 1)] = lambda store: store.case.update(resource_security_labels=[BOOKING])
    with pytest.raises(AuthError):
        f.run()


def test_public_case_acl_recheck_after_session_read_blocks_late_revocation():
    f = Store(private=False)
    f.hooks[('session', 2)] = lambda store: setattr(store, 'allowed', False)
    with pytest.raises(AuthError):
        f.run()


def test_lost_staff_session_returns_auth_required():
    f = Store(); f.hooks[('session', 2)] = lambda store: setattr(store, 'session', False)
    with pytest.raises(AuthError) as error:
        f.run()
    assert error.value.status == 401


@pytest.mark.parametrize('kind', ['customer', 'anonymous'])
def test_staff_projection_is_not_a_customer_history_endpoint(kind):
    f = Store(); f.identity.session.kind = kind
    with pytest.raises(AuthError):
        f.run()
    assert f.counts == {}


def test_current_service_entrypoints_use_guarded_projection_and_keep_shape():
    f = Store(private=False)
    result = asyncio.run(CaseLifecycle(f).email_replies(f.identity, f.id))
    assert result[0]['body'] == 'Authorized published response.'
    f = Store('deliveries', private=False)
    f.rows *= 101
    result = asyncio.run(DeliveryStatus(f).list(f.identity, f.id))
    assert len(result['items']) == 100 and result['has_more'] is True


def test_no_fallback_when_private_history_capability_is_missing():
    f = Store(); f.mail_history_context = AsyncMock(side_effect=RuntimeError('missing private SQL'))
    with pytest.raises(RuntimeError):
        f.run()
    assert 'rows' not in f.counts
