"""Offline send-boundary races. No provider, database, credential, or worker."""
import asyncio
import copy
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from airport_assistant.infrastructure.database.mail import PostgresMail
from airport_assistant.modules.jobs.service import Lease
from airport_assistant.modules.mail.private_outbound import (
    BeginNotCommitted, GROUPS, PrivateMailContext, envelope_hash,
)
from airport_assistant.modules.mail.service import OutboundMail, SendResult
from airport_assistant.modules.source.labels import PrivateSourceAccess
from airport_assistant.modules.source.models import SourceFailure


BOOKING = dict(source='reference', resource_type='booking', reference='B123',
               required_source_scopes=['booking:read'])
PAYMENT = dict(source='reference', resource_type='transaction', reference='P123',
               required_source_scopes=['payment_details:read'])


class Store:
    """Deterministic repository double with persisted reservation and I/O hooks."""
    def __init__(self, kind='email_send'):
        self.lease = Lease(uuid4(), uuid4(), uuid4(), uuid4(), kind, 1, 1, uuid4(), uuid4())
        lease = self.lease
        self.now = 0.
        self.until = 30.
        self.hooks = {}
        self.calls = {}
        self.in_tx = False
        self.completed = []
        self.aborts = 0
        self.abort_result = True
        self.complete_result = True
        self.begin_error = None
        self.begin_delay = 0.
        self.begin_return = 'normal'
        self.data = dict(
            classification_version=2, tenant_id=str(lease.tenant_id),
            case_id=str(lease.case_id), job_id=str(lease.id), draft_id=str(lease.draft_id),
            worker_id=str(lease.worker_id), lease_token=str(lease.token),
            job_kind=lease.kind, lease_generation=lease.generation, lease_valid=True,
            case_version=1, source_message_version=1, app_message_id=str(uuid4()),
            public_reference='CASE-' + uuid4().hex, language='en',
            contact_id=str(uuid4()), principal_id=str(uuid4()),
            recipient='customer@example.com', sender='support@airport.example',
            reply_to='reply@airport.example', account_id=str(uuid4()),
            provider_account_ref='account-a', tenant_active=True, account_active=True,
            principal_active=True, verified=True, participant_active=True,
            delivery_status=None, delivery_version=None, email_id=None,
            envelope_hash=None, reconcile_job_id=None, reconcile_email_id=None,
            labels=[copy.deepcopy(BOOKING)], body='Human-approved private response.' if kind == 'email_send' else None,
        )
        self.data['followup_contact_id'] = self.data['contact_id']
        self.data['owner_principal_id'] = self.data['principal_id']
        self.data.update({name: [] for name in GROUPS})
        self.data['case_labels'] = copy.deepcopy(self.data['labels'])
        if kind == 'email_send':
            self.data['message_hash'] = hashlib.sha256(self.data['body'].encode()).hexdigest()
        self.access = SimpleNamespace(allows=AsyncMock(side_effect=self.allow))
        self.transport = SimpleNamespace(send=AsyncMock(side_effect=self.send))
        self.service = OutboundMail(self, self.transport, account_ref='account-a',
                                    private_access=self.access, private_outbound=True)
        self.service._private_outbound.clock = lambda: self.now

    def hook(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        callback = self.hooks.get((name, self.calls[name]))
        if callback:
            callback(self)

    def projection(self):
        raw = copy.deepcopy(self.data)
        epoch = datetime(2026, 11, 5, tzinfo=timezone.utc)
        raw.update(server_now=(epoch + timedelta(seconds=self.now)).isoformat(),
                   lease_until=(epoch + timedelta(seconds=self.until)).isoformat())
        return raw

    async def allow(self, **kwargs):
        assert not self.in_tx
        assert kwargs['tenant_id'] == self.lease.tenant_id
        assert kwargs['principal_id'] == self.service_principal
        self.hook('grant')
        return True

    @property
    def service_principal(self):
        from uuid import UUID
        return UUID(self.data['principal_id'])

    async def private_snapshot(self, lease):
        assert lease == self.lease
        self.hook('snapshot')
        return self.projection()

    async def private_begin(self, lease, expected, envelope, *, before_commit):
        self.hook('begin')
        if self.begin_error:
            raise self.begin_error
        if self.begin_return != 'normal':
            return self.begin_return
        self.now += self.begin_delay
        self.in_tx = True
        try:
            try:
                before_commit()
            except Exception as error:
                raise BeginNotCommitted() from error
            self.data.update(delivery_status='sending', delivery_version=1,
                             email_id=str(envelope.id), envelope_hash=envelope_hash(envelope),
                             email_labels=copy.deepcopy(self.data['labels']),
                             reconcile_labels=copy.deepcopy(self.data['labels']),
                             reconcile_job_id=str(uuid4()), reconcile_email_id=str(envelope.id))
        finally:
            self.in_tx = False
        self.hook('after_begin')
        return self.projection()

    async def private_send_snapshot(self, lease, email_id, *, before_commit=None):
        assert str(email_id) == self.data['email_id']
        self.hook('send_snapshot')
        raw = self.projection()
        if before_commit:
            before_commit()
        return raw

    async def abort_unsent(self, lease, email_id, digest):
        assert self.transport.send.await_count == 0
        assert str(email_id) == self.data['email_id']
        assert digest == self.data['envelope_hash']
        self.aborts += 1
        return self.abort_result

    async def send(self, envelope):
        assert not self.in_tx
        self.hook('send')
        return SendResult('accepted', 'receipt-123')

    async def private_complete(self, lease, email_id, digest, result):
        assert lease == self.lease
        assert str(email_id) == self.data['email_id']
        assert digest == self.data['envelope_hash']
        self.completed.append(result)
        return self.complete_result

    def run(self):
        return asyncio.run(self.service(self.lease))


@pytest.mark.parametrize('kind', ['case_created', 'email_send'])
def test_authorized_send_preserves_approved_content_and_checks_recipient_twice(kind):
    f = Store(kind)
    assert f.run().state == 'succeeded'
    assert f.access.allows.await_count == 2
    assert f.transport.send.await_count == 1 and f.aborts == 0
    envelope = f.transport.send.call_args.args[0]
    assert envelope.id == uuid5(f.lease.id, 'case-created-email')
    assert envelope.recipient == 'customer@example.com'
    assert envelope.text == (f.data['body'] if kind == 'email_send' else f.service._envelope(f.lease, f.data).text)
    assert f.data['recipient'] not in repr(envelope)


@pytest.mark.parametrize('group', GROUPS)
@pytest.mark.parametrize('bad', [None, {}, '[]'])
def test_missing_or_malformed_parent_classification_never_sends(group, bad):
    f = Store(); f.data[group] = bad
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()
    assert 'begin' not in f.calls


@pytest.mark.parametrize('key,value', [
    ('classification_version', True), ('classification_version', 1),
    ('tenant_id', str(uuid4())), ('case_id', str(uuid4())), ('job_id', str(uuid4())),
    ('draft_id', str(uuid4())), ('worker_id', str(uuid4())), ('lease_token', str(uuid4())),
    ('lease_generation', True), ('lease_generation', 2), ('job_kind', 'other'),
    ('lease_valid', False), ('case_version', 0), ('source_message_version', True),
    ('app_message_id', 'invalid'), ('account_id', 'invalid'), ('labels', []),
    ('body', 'changed without approval'), ('message_hash', 'invalid'),
    ('delivery_status', 'invalid'), ('delivery_version', 1),
    ('tenant_active', False), ('account_active', False), ('principal_active', False),
    ('verified', False), ('participant_active', False), ('contact_id', None),
    ('followup_contact_id', str(uuid4())), ('owner_principal_id', str(uuid4())),
    ('provider_account_ref', 'unconfigured'), ('recipient', 'bad\r\nBcc:x@example.com'),
])
def test_untrusted_or_ineligible_snapshot_is_blocked_before_reservation(key, value):
    f = Store(); f.data[key] = value
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()
    assert 'begin' not in f.calls


@pytest.mark.parametrize('failure', [False, None, TimeoutError('source unavailable')])
@pytest.mark.parametrize('phase', ['before_begin', 'after_begin'])
def test_denied_or_unavailable_grants_never_disclose(failure, phase):
    f = Store()
    def answers():
        if phase == 'after_begin':
            yield True
        yield failure
    f.access.allows.side_effect = list(answers())
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()
    assert f.aborts == (1 if phase == 'after_begin' else 0)


def test_booking_grant_does_not_imply_payment_grant():
    f = Store()
    f.data['message_labels'] = [copy.deepcopy(PAYMENT)]
    f.data['labels'] = [copy.deepcopy(BOOKING), copy.deepcopy(PAYMENT)]
    grants = SimpleNamespace(has_grants=AsyncMock(side_effect=[True, False]))
    f.service._private = PrivateSourceAccess(grants)
    assert f.run().state == 'blocked'
    assert grants.has_grants.await_count == 2
    assert grants.has_grants.call_args.kwargs['required_scopes'] == frozenset({'payment_details:read'})
    f.transport.send.assert_not_awaited()


@pytest.mark.parametrize('key,value', [
    ('recipient', 'other@example.com'), ('contact_id', str(uuid4())),
    ('account_id', str(uuid4())), ('participant_active', False),
    ('source_message_version', 2), ('case_version', 2), ('language', 'id'),
    ('body', 'changed'), ('message_labels', [PAYMENT]), ('lease_token', str(uuid4())),
])
@pytest.mark.parametrize('boundary', ['after_initial_grants', 'after_begin', 'after_final_grants'])
def test_context_changes_at_each_read_boundary_prevent_send(key, value, boundary):
    f = Store()
    stage = {'after_initial_grants': ('snapshot', 2), 'after_begin': ('send_snapshot', 1),
             'after_final_grants': ('send_snapshot', 2)}[boundary]
    f.hooks[stage] = lambda store: store.data.update({key: copy.deepcopy(value)})
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()
    assert f.aborts == (0 if boundary == 'after_initial_grants' else 1)


@pytest.mark.parametrize('stage', [('snapshot', 2), ('send_snapshot', 2)])
def test_last_read_crossing_source_freshness_deadline_never_sends(stage):
    f = Store(); f.hooks[stage] = lambda store: setattr(store, 'now', store.now + 2.)
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()


def test_slow_begin_rolls_back_before_reservation():
    f = Store(); f.begin_delay = 2.
    assert f.run().state == 'blocked'
    assert f.data['delivery_status'] is None and f.aborts == 0
    f.transport.send.assert_not_awaited()


def test_heartbeat_extension_is_not_a_content_change():
    f = Store()
    f.hooks[('snapshot', 2)] = lambda store: setattr(store, 'until', 60.)
    f.hooks[('send_snapshot', 2)] = lambda store: setattr(store, 'until', 90.)
    assert f.run().state == 'succeeded'


@pytest.mark.parametrize('boundary', ['before_begin', 'before_send'])
def test_same_fence_renewal_crossing_old_expiry_keeps_original_grants_age(boundary):
    f = Store(); f.until = .1
    def renew(store):
        store.until = 30.
        store.now += .2
    if boundary == 'before_begin':
        f.hooks[('snapshot', 2)] = renew
    else:
        f.hooks[('snapshot', 2)] = lambda store: setattr(store, 'until', .3)
        f.hooks[('send_snapshot', 2)] = renew
        f.hooks[('after_begin', 1)] = lambda store: setattr(store, 'now', .2)
    assert f.run().state == 'succeeded'
    assert f.access.allows.await_count == 2


def test_lease_renewal_does_not_refresh_source_grant_deadline():
    f = Store(); f.until = .1
    def renew_but_slow(store):
        store.until = 30.
        store.now = 2.
    f.hooks[('snapshot', 2)] = renew_but_slow
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()


def test_lease_expires_during_final_read():
    f = Store(); f.until = 1.
    f.hooks[('send_snapshot', 2)] = lambda store: setattr(store, 'now', 1.)
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()


@pytest.mark.parametrize('state', ['sending', 'delivery_unknown', 'failed', 'bounced', 'accepted', 'delivered'])
def test_existing_attempt_is_never_reposted(state):
    f = Store()
    envelope = f.service._envelope(f.lease, f.data)
    f.data.update(delivery_status=state, delivery_version=1, email_id=str(envelope.id),
                  envelope_hash=envelope_hash(envelope), reconcile_job_id=str(uuid4()),
                  reconcile_email_id=str(envelope.id))
    assert f.run().state == ('succeeded' if state in {'accepted', 'delivered'} else 'unknown')
    f.transport.send.assert_not_awaited()
    assert 'begin' not in f.calls


@pytest.mark.parametrize('result', [None, True, 'invalid', {'delivery_status': 'sending'}])
def test_malformed_begin_result_never_sends(result):
    f = Store(); f.begin_return = result
    assert f.run().state == 'unknown'
    f.transport.send.assert_not_awaited()
    assert f.aborts == 0


def test_explicit_reservation_rejection_is_blocked_without_provider_call():
    f = Store(); f.begin_return = False
    assert f.run().state == 'blocked'
    f.transport.send.assert_not_awaited()
    assert f.aborts == 0


@pytest.mark.parametrize('key,value', [
    ('email_id', str(uuid4())), ('reconcile_email_id', str(uuid4())),
    ('reconcile_job_id', 'invalid'), ('delivery_version', 2),
    ('delivery_status', 'accepted'), ('envelope_hash', 'a' * 64),
    ('email_labels', []), ('reconcile_labels', []),
    ('source_message_version', 2), ('recipient', 'changed@example.com'),
])
def test_corrupted_committed_reservation_never_calls_transport_or_claims_unsent(key, value):
    f = Store()
    f.hooks[('after_begin', 1)] = lambda store: store.data.update({key: copy.deepcopy(value)})
    assert f.run().state == 'unknown'
    f.transport.send.assert_not_awaited()
    assert f.aborts == 0


def test_slow_committed_begin_requires_new_grants_before_send():
    f = Store()
    f.hooks[('after_begin', 1)] = lambda store: setattr(store, 'now', 3.)
    assert f.run().state == 'succeeded'
    assert f.access.allows.await_count == 2
    assert f.transport.send.await_count == 1


def test_ambiguous_begin_commit_never_sends_or_claims_unsent():
    f = Store(); f.begin_error = TimeoutError('commit unknown')
    assert f.run().state == 'unknown'
    f.transport.send.assert_not_awaited()
    assert f.aborts == 0


def test_cannot_confirm_abort_keeps_unknown_without_send():
    f = Store(); f.abort_result = False
    f.access.allows.side_effect = [True, False]
    assert f.run().state == 'unknown'
    assert f.aborts == 1
    f.transport.send.assert_not_awaited()


@pytest.mark.parametrize('result', [SendResult('failed'), SendResult('delivery_unknown'),
                                  TimeoutError('possibly accepted'), {'state': 'accepted'}])
def test_transport_failure_or_unknown_never_aborts_or_retries(result):
    f = Store(); f.transport.send.side_effect = result if isinstance(result, Exception) else None
    if not isinstance(result, Exception):
        f.transport.send.return_value = result
    outcome = f.run()
    assert outcome.state == ('dead' if result == SendResult('failed') else 'unknown')
    assert f.transport.send.await_count == 1 and f.aborts == 0
    assert len(f.completed) == 1


def test_recording_performed_outcome_does_not_require_new_customer_grant():
    f = Store()
    f.hooks[('send', 1)] = lambda store: setattr(store.access.allows, 'side_effect', AssertionError('No new disclosure'))
    assert f.run().state == 'succeeded'
    assert f.access.allows.await_count == 2


def test_complete_failure_keeps_unknown_and_never_reposts():
    f = Store(); f.complete_result = False
    assert f.run().state == 'unknown'
    assert f.run().state == 'unknown'
    assert f.transport.send.await_count == 1 and f.aborts == 0


@pytest.mark.parametrize('boundary', ['before_send', 'during_send'])
def test_cancellation_is_propagated_without_unsent_claim_or_resend(boundary):
    f = Store()
    if boundary == 'during_send':
        f.transport.send.side_effect = asyncio.CancelledError()
    else:
        def cancel(store):
            raise asyncio.CancelledError()
        f.hooks[('send_snapshot', 2)] = cancel
    with pytest.raises(asyncio.CancelledError):
        f.run()
    assert f.aborts == 0
    assert f.run().state == 'unknown'
    assert f.transport.send.await_count == (1 if boundary == 'during_send' else 0)


def test_default_off_rejects_private_child_even_when_parent_looks_public():
    f = Store(); f.data['labels'] = []; f.data['case_labels'] = []
    f.data['message_labels'] = [copy.deepcopy(BOOKING)]
    repo = SimpleNamespace(snapshot=AsyncMock(return_value=f.data), begin=AsyncMock())
    service = OutboundMail(repo, f.transport, account_ref='account-a', private_access=f.access)
    assert asyncio.run(service(f.lease)).state == 'blocked'
    repo.begin.assert_not_awaited(); f.transport.send.assert_not_awaited()


def test_private_context_repr_does_not_include_recipient_body_or_labels():
    f = Store()
    context = PrivateMailContext.capture(f.lease, f.projection(), f.now)
    assert f.data['body'] not in repr(context)
    assert f.data['recipient'] not in repr(context)
    assert 'B123' not in repr(context)


class Database:
    def __init__(self):
        self.commits = self.rollbacks = 0
        self.in_tx = False
        self.after_fetch = lambda: None
        self.query = None

    @asynccontextmanager
    async def transaction(self):
        self.in_tx = True
        try:
            yield self
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
        finally:
            self.in_tx = False

    async def execute(self, query, params):
        self.query = (query.as_string(), params)
        return self

    async def fetchone(self):
        self.after_fetch()
        return ({'result': 'persisted projection'},)


def test_postgres_begin_deadline_after_sql_rolls_back_and_returns_known_unsent():
    f = Store(); db = Database(); repo = PostgresMail(db)
    envelope = f.service._envelope(f.lease, f.data)
    db.after_fetch = lambda: setattr(f, 'now', 3.)
    def check():
        assert db.in_tx
        if f.now >= 2:
            raise SourceFailure(503)
    with pytest.raises(BeginNotCommitted):
        asyncio.run(repo.private_begin(f.lease, f.projection(), envelope, before_commit=check))
    assert db.rollbacks == 1 and db.commits == 0
    assert 'mail_private_begin' in db.query[0]


def test_postgres_private_ports_never_fall_back_to_legacy_functions():
    async def run():
        f = Store(); db = Database(); repo = PostgresMail(db)
        await repo.private_snapshot(f.lease)
        assert 'mail_private_snapshot' in db.query[0]
        await repo.private_send_snapshot(f.lease, uuid4(), before_commit=lambda: None)
        assert 'mail_private_send_snapshot' in db.query[0]
        await repo.abort_unsent(f.lease, uuid4(), 'a' * 64)
        assert 'mail_private_abort_unsent' in db.query[0]
        await repo.private_complete(f.lease, uuid4(), 'a' * 64, SendResult('accepted', 'receipt-1'))
        assert 'mail_private_complete' in db.query[0]
        assert db.query[1][-2:] == ('accepted', 'receipt-1')
    asyncio.run(run())


def test_postgres_async_commit_checker_is_rejected_without_commit():
    f = Store(); db = Database(); repo = PostgresMail(db)
    async def invalid():
        raise AssertionError('Must not be awaited')
    with pytest.raises(TypeError):
        asyncio.run(repo.private_send_snapshot(f.lease, uuid4(), before_commit=invalid))
    assert db.rollbacks == 1 and db.commits == 0
