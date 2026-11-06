"""Private inbound proof/entitlement and atomic publication boundaries, offline."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.infrastructure.database.replies import PostgresReplies
from airport_assistant.modules.jobs.service import Lease
from airport_assistant.modules.mail.private_outbound import fingerprint
from airport_assistant.modules.mail.private_replies import GROUPS, PrivateReplies, digest_text, ReplyCandidate
from airport_assistant.modules.mail.replies import ReplyWorker, ChallengeWorker
from airport_assistant.modules.source.labels import PrivateSourceAccess
from test_private_outbound import Database, BOOKING, PAYMENT


SECRET = b'private-replies-offline-test-only-secret'
EPOCH = datetime(2026, 11, 5, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.lease = Lease(uuid4(), uuid4(), None, None, 'email_reply', 1, 1, uuid4(), uuid4())
        lease = self.lease
        self.now = 0.
        self.until = 30.
        self.in_tx = False
        self.reads = self.applies = self.commits = 0
        self.hooks = {}
        self.receipt_mutation = None
        self.after_commit = None
        self.data = dict(classification_version=2, tenant_id=str(lease.tenant_id), job_id=str(lease.id),
            worker_id=str(lease.worker_id), lease_token=str(lease.token), lease_generation=1,
            lease_valid=True, job_kind='email_reply', job_case_id=None, job_draft_id=None,
            case_id=str(uuid4()), email_id=str(uuid4()), referenced_email_id=str(uuid4()),
            account_id=str(uuid4()), principal_id=str(uuid4()), contact_id=str(uuid4()),
            tenant_active=True, account_active=True, principal_active=True, verified=True,
            participant_active=True, eligible=True, sender='customer@example.com',
            contact_address='customer@example.com', source_sender='customer@example.com',
            case_version=3, case_status='closed', case_language='en', assigned_membership_id=None,
            assignee_eligible=False, conversation_mode='ended', state='pending', source_reply_state='pending',
            case_close_reason='resolved', case_resolution='Original resolution', case_closed_at='2026-11-04T00:00:00+00:00',
            content_digest='a' * 64, source_content_digest='a' * 64,
            source_body='CONTINUE SAME ISSUE\nPlease help with this booking.',
            authentication={'dkim': 'pass', 'dmarc': 'pass'}, verification_challenge_id=None,
            labels=[copy.deepcopy(BOOKING)])
        d = self.data
        d.update(source_email_id=d['email_id'], thread_case_id=d['case_id'], thread_account_id=d['account_id'],
                 source_case_id=d['case_id'], referenced_case_id=d['case_id'], referenced_account_id=d['account_id'],
                 source_account_id=d['account_id'], followup_contact_id=d['contact_id'], owner_principal_id=d['principal_id'],
                 source_body_hash=digest_text(d['source_body']))
        d.update({key: [] for key in GROUPS}); d['case_labels'] = copy.deepcopy(d['labels'])
        self.access = SimpleNamespace(allows=AsyncMock(side_effect=self.allow))
        self.worker = ReplyWorker(self, SECRET, private_replies=True, private_access=self.access)
        self.worker._private_replies.clock = lambda: self.now

    def projection(self):
        return {**copy.deepcopy(self.data), 'server_now': (EPOCH + timedelta(seconds=self.now)).isoformat(),
                'lease_until': (EPOCH + timedelta(seconds=self.until)).isoformat()}

    async def private_candidate(self, lease):
        assert lease == self.lease
        self.reads += 1
        if self.reads in self.hooks:
            self.hooks[self.reads](self)
        return self.projection()

    async def allow(self, **kwargs):
        assert not self.in_tx
        assert str(kwargs['principal_id']) == self.data['principal_id']
        assert kwargs['tenant_id'] == self.lease.tenant_id
        return True

    async def private_apply(self, lease, expected, command, *, before_commit, result_check):
        assert expected == self.projection()
        self.applies += 1; self.command = command
        self.in_tx = True
        try:
            result = {key: expected[key] for key in ('tenant_id', 'job_id', 'email_id', 'source_email_id',
                'case_id', 'account_id', 'principal_id', 'contact_id', 'worker_id', 'lease_token', 'lease_generation')}
            published = command['action'] != 'review'
            result.update(classification_version=2, lease_valid=True, command_hash=fingerprint(command),
                state='verification_reply' if command['action'] == 'verify' else 'published' if published else 'review', case_status=command['case_status'],
                case_version=command['case_version'], assigned_membership_id=command['assigned_membership_id'],
                history_event=command['history_event'], message_body_hash=command['body_hash'],
                proof_kind=command['proof_kind'], app_message_id=str(uuid4()) if published else None,
                history_id=str(uuid4()) if command['history_event'] else None,
                challenge_attempts=command['challenge_attempts'], challenge_state=command['challenge_state'],
                source_reply_state=command['source_reply_state'], conversation_mode=command['conversation_mode'],
                case_close_reason=command['case_close_reason'], case_resolution=command['case_resolution'],
                case_closed_at=command['case_closed_at'],
                message_author_principal_id=expected['principal_id'] if published else None,
                message_source_email_id=expected['source_email_id'] if published else None,
                message_source_kind='email_reply' if published else None,
                message_channel='email' if published else None,
                message_language=expected['case_language'] if published else None,
                message_case_id=expected['case_id'] if published else None,
                message_content_digest=expected['source_content_digest'] if published else None,
                history_source_email_id=expected['source_email_id'] if command['history_event'] else None,
                history_case_id=expected['case_id'] if command['history_event'] else None,
                history_actor_principal_id=expected['principal_id'] if command['history_event'] else None,
                history_previous_version=expected['case_version'] if command['history_event'] else None,
                history_resulting_version=command['case_version'] if command['history_event'] else None,
                challenge_id=expected.get('verification_challenge_id') if command['challenge_attempt'] else None,
                challenge_control_email_id=expected['email_id'] if command['challenge_attempt'] else None,
                challenge_source_email_id=expected['source_email_id'] if command['challenge_attempt'] else None,
                review_reason=command['review_reason'],
                server_now=self.projection()['server_now'], lease_until=self.projection()['lease_until'])
            result.update(email_app_message_id=result['app_message_id'], reply_app_message_id=result['app_message_id'])
            for group in ('case_labels', 'email_labels', 'reply_labels', 'message_labels', 'history_labels'):
                result[group] = [] if group == 'message_labels' and not published or group == 'history_labels' and not command['history_event'] else command['labels']
            context = ReplyCandidate.capture(lease, expected, self.now)
            result['final_context'] = PrivateReplies.final_basis(context, command, result['app_message_id'])
            result['final_context'].update(server_now=result['server_now'], lease_until=result['lease_until'])
            if self.receipt_mutation:
                self.receipt_mutation(self, result)
            result_check(result)
            before_commit()
            self.commits += 1
            self.data = {key: copy.deepcopy(value) for key, value in result['final_context'].items() if key not in {'server_now', 'lease_until'}}
        finally:
            self.in_tx = False
        if self.after_commit:
            self.after_commit(self)
        return result

    def challenge(self):
        d = self.data
        d['verification_challenge_id'] = str(uuid4())
        d['source_email_id'] = str(uuid4())
        d['source_reply_state'] = 'challenged'
        d['authentication'] = {}
        d['challenge'] = dict(id=d['verification_challenge_id'], control_email_id=d['email_id'],
            case_id=d['case_id'],
            email_id=d['source_email_id'], contact_id=d['contact_id'], principal_id=d['principal_id'],
            account_id=d['account_id'], recipient=d['sender'], source_content_digest=d['source_content_digest'],
            pending_text_hash=d['source_body_hash'], state='pending', attempts=0,
            expires_at=(EPOCH + timedelta(seconds=300)).isoformat(), response_hash='b' * 64, code_hash='b' * 64)

    def run(self):
        return asyncio.run(self.worker(self.lease))


def test_authenticated_reply_reopens_only_the_same_case_with_grants():
    f = Store()
    assert f.run().state == 'succeeded'
    assert f.command['action'] == 'publish'
    assert f.command['history_event'] == 'reopened'
    assert f.data['case_status'] == 'new' and f.data['case_version'] == 4
    assert f.commits == 1 and f.access.allows.await_count == 1
    assert f.run().state == 'succeeded' and f.applies == 1


@pytest.mark.parametrize('body,event,status', [
    ('Thank you', None, 'closed'), ('Terima kasih', None, 'closed'),
    ('Could you explain?', None, 'closed'), ('NEW ISSUE\nAnother request', None, 'closed'),
    ('LANJUTKAN MASALAH YANG SAMA\nTolong bantu', 'reopened', 'new')])
def test_closed_continuation_policy_is_explicit(body, event, status):
    f = Store(); f.data.update(source_body=body, source_body_hash=digest_text(body))
    assert f.run().state == 'succeeded'
    assert f.command['history_event'] == event and f.command['case_status'] == status
    if body.startswith('NEW'):
        assert f.command['action'] == 'review'
    if body.startswith('Could'):
        assert f.command['action'] == 'publish' and f.command['review_reason'] == 'CLOSED_REPLY_AMBIGUOUS'
    assert f.command['case_version'] == (4 if event else 3)


@pytest.mark.parametrize('proof', [{}, {'dkim': 'pass'}, {'dkim': 'pass', 'dmarc': 'fail'}])
def test_missing_sender_proof_goes_to_quarantine_not_challenge_or_publish(proof):
    f = Store(); f.data['authentication'] = proof
    assert f.run().state == 'succeeded'
    assert f.command['action'] == 'review' and f.command['body'] is None
    assert f.data['case_status'] == 'closed'


@pytest.mark.parametrize('value', [False, None, TimeoutError('source unavailable')])
def test_sender_verification_never_substitutes_for_source_grants(value):
    f = Store(); f.access.allows.side_effect = value if isinstance(value, Exception) else None
    f.access.allows.return_value = value
    assert f.run().state == 'blocked'
    assert f.applies == f.commits == 0


def test_booking_right_does_not_include_payment_details():
    f = Store()
    f.data['case_labels'].append(copy.deepcopy(PAYMENT)); f.data['labels'].append(copy.deepcopy(PAYMENT))
    grants = SimpleNamespace(has_grants=AsyncMock(side_effect=[True, False]))
    f.worker._private_replies.source_access = PrivateSourceAccess(grants)
    assert f.run().state == 'blocked' and f.applies == 0
    assert grants.has_grants.await_count == 2


@pytest.mark.parametrize('group', GROUPS)
@pytest.mark.parametrize('bad', [None, {}, '[]'])
def test_malformed_whole_graph_classification_never_applies(group, bad):
    f = Store(); f.data[group] = bad
    assert f.run().state == 'blocked' and f.applies == 0


@pytest.mark.parametrize('key,value', [
    ('classification_version', True), ('tenant_id', str(uuid4())), ('job_id', str(uuid4())),
    ('worker_id', str(uuid4())), ('lease_token', str(uuid4())), ('lease_generation', True),
    ('job_kind', 'case_created'), ('job_case_id', str(uuid4())), ('job_draft_id', str(uuid4())),
    ('lease_valid', False), ('case_version', True), ('eligible', False), ('verified', False),
    ('participant_active', False), ('account_active', False), ('principal_active', False),
    ('thread_case_id', str(uuid4())), ('thread_account_id', str(uuid4())),
    ('source_case_id', str(uuid4())), ('referenced_case_id', str(uuid4())), ('referenced_account_id', str(uuid4())),
    ('source_account_id', str(uuid4())), ('followup_contact_id', str(uuid4())),
    ('owner_principal_id', str(uuid4())), ('source_sender', 'foreign@example.com'),
    ('sender', 'bad\r\nBcc:foreign@example.com'), ('source_body_hash', 'invalid'),
    ('content_digest', 'invalid'), ('source_email_id', 'invalid'), ('labels', [])])
def test_spoofed_root_sender_job_or_source_binding_is_rejected(key, value):
    f = Store(); f.data[key] = value
    assert f.run().state == 'blocked' and f.applies == 0


@pytest.mark.parametrize('key,value', [
    ('contact_address', 'other@example.com'), ('principal_id', str(uuid4())),
    ('source_content_digest', 'c' * 64), ('case_version', 4), ('eligible', False),
    ('participant_active', False), ('authentication', {}), ('reply_labels', [PAYMENT]),
    ('verification_challenge_id', str(uuid4()))])
def test_changes_after_grants_never_apply(key, value):
    f = Store(); f.hooks[2] = lambda store: store.data.update({key: copy.deepcopy(value)})
    assert f.run().state == 'blocked' and f.applies == 0


def test_candidate_is_read_only_until_authorized_apply():
    f = Store(); original = copy.deepcopy(f.data)
    f.access.allows.return_value = False; f.access.allows.side_effect = None
    assert f.run().state == 'blocked'
    assert f.data == original and f.applies == 0


def test_source_deadline_after_final_candidate_blocks_mutation():
    f = Store(); f.hooks[2] = lambda store: setattr(store, 'now', 2.)
    assert f.run().state == 'blocked' and f.applies == 0


def test_source_deadline_crossed_after_sql_result_rolls_back():
    f = Store(); f.receipt_mutation = lambda store, result: setattr(store, 'now', 2.)
    assert f.run().state == 'unknown' and f.commits == 0
    assert f.data['state'] == 'pending'


def test_slow_commit_returns_unknown_and_dedupes_on_next_operation():
    f = Store(); f.after_commit = lambda store: setattr(store, 'now', 3.)
    assert f.run().state == 'unknown' and f.commits == 1
    assert f.run().state == 'succeeded' and f.applies == 1


def test_lease_renewal_does_not_reset_source_freshness():
    f = Store(); f.until = .1
    def renew(store):
        store.until = 30.; store.now = .2
    f.hooks[2] = renew
    assert f.run().state == 'succeeded'


def test_valid_challenge_proves_original_pending_body_not_control_body():
    f = Store(); f.challenge()
    assert f.run().state == 'succeeded'
    assert f.command['action'] == 'verify'
    assert f.command['body'] == f.data['source_body']
    assert f.command['challenge_attempts'] == 1 and f.command['challenge_state'] == 'consumed'


@pytest.mark.parametrize('key,value', [
    ('email_id', str(uuid4())), ('control_email_id', str(uuid4())), ('contact_id', str(uuid4())),
    ('case_id', str(uuid4())),
    ('principal_id', str(uuid4())), ('account_id', str(uuid4())), ('recipient', 'other@example.com'),
    ('source_content_digest', 'c' * 64), ('pending_text_hash', 'c' * 64), ('state', 'consumed'),
    ('attempts', 5), ('attempts', True), ('expires_at', EPOCH.isoformat())])
def test_invalid_challenge_cannot_publish_or_reopen(key, value):
    f = Store(); f.challenge(); f.data['challenge'][key] = value
    assert f.run().state == 'succeeded'
    assert f.command['action'] == 'review' and not f.command['challenge_attempt']
    assert f.data['case_status'] == 'closed'


@pytest.mark.parametrize('attempts,state', [(0, 'pending'), (4, 'failed')])
def test_wrong_code_still_consumes_bounded_attempt_without_publishing(attempts, state):
    f = Store(); f.challenge(); f.data['challenge'].update(attempts=attempts, response_hash='c' * 64)
    assert f.run().state == 'succeeded'
    assert f.command['action'] == 'review' and f.command['challenge_attempt']
    assert f.command['challenge_attempts'] == attempts + 1 and f.command['challenge_state'] == state
    assert f.command['source_reply_state'] == ('review' if attempts == 4 else 'challenged')
    assert f.data['case_status'] == 'closed'


def test_challenge_expiry_during_sql_result_read_rolls_back():
    f = Store(); f.challenge()
    f.data['challenge']['expires_at'] = (EPOCH + timedelta(seconds=1)).isoformat()
    f.receipt_mutation = lambda store, result: setattr(store, 'now', 1.)
    assert f.run().state == 'unknown' and f.commits == 0


@pytest.mark.parametrize('state', ['published', 'review', 'pending', 'rejected'])
def test_verification_cannot_republish_an_original_reply_outside_challenged_state(state):
    f = Store(); f.challenge(); f.data['source_reply_state'] = state
    assert f.run().state == 'succeeded'
    assert f.command['action'] == 'review' and not f.command['challenge_attempt']
    assert f.command['source_reply_state'] == state
    assert f.data['case_status'] == 'closed'


@pytest.mark.parametrize('key,value', [
    ('tenant_id', str(uuid4())), ('case_id', str(uuid4())), ('email_id', str(uuid4())),
    ('source_email_id', str(uuid4())), ('principal_id', str(uuid4())), ('lease_token', str(uuid4())),
    ('lease_generation', True), ('lease_valid', False), ('command_hash', 'invalid'),
    ('case_version', 3), ('case_status', 'closed'), ('app_message_id', None),
    ('history_id', None), ('message_body_hash', 'invalid'), ('proof_kind', 'challenge'),
    ('message_labels', []), ('email_labels', []), ('history_labels', []), ('reply_labels', []),
])
def test_corrupted_actual_graph_receipt_rolls_back(key, value):
    f = Store(); f.receipt_mutation = lambda store, result: result.update({key: value})
    assert f.run().state == 'unknown' and f.commits == 0
    assert f.data['state'] == 'pending'


@pytest.mark.parametrize('key,value', [
    ('participant_active', False), ('verified', False), ('principal_active', False), ('account_active', False),
    ('sender', 'other@example.com'), ('contact_address', 'other@example.com'),
    ('contact_id', str(uuid4())), ('principal_id', str(uuid4())), ('account_id', str(uuid4())),
    ('thread_case_id', str(uuid4())), ('thread_account_id', str(uuid4())),
    ('referenced_email_id', str(uuid4())), ('source_content_digest', 'c' * 64),
    ('content_digest', 'c' * 64), ('source_body', 'changed after final authorization'),
    ('source_body_hash', 'c' * 64), ('authentication', {}), ('eligible', False),
    ('lease_token', str(uuid4())), ('lease_generation', 2), ('lease_valid', False),
    ('case_close_reason', 'resolved'), ('case_resolution', 'Old resolution'),
    ('conversation_mode', 'human'), ('job_labels', []), ('email_labels', []),
])
def test_actual_final_context_drift_cannot_hide_behind_matching_command_hash(key, value):
    f = Store()
    f.receipt_mutation = lambda store, result: result['final_context'].update({key: copy.deepcopy(value)})
    assert f.run().state == 'unknown' and f.commits == 0


@pytest.mark.parametrize('key', ['final_context', 'case_close_reason', 'history_source_email_id'])
def test_omitted_required_final_graph_or_nullable_field_fails_closed(key):
    f = Store(); f.receipt_mutation = lambda store, result: result.pop(key)
    assert f.run().state == 'unknown' and f.commits == 0


@pytest.mark.parametrize('key,value', [
    ('message_author_principal_id', str(uuid4())), ('message_source_email_id', str(uuid4())),
    ('message_source_kind', 'chat'), ('message_content_digest', 'c' * 64),
    ('message_channel', 'web'), ('message_language', 'id'), ('message_case_id', str(uuid4())),
    ('email_app_message_id', str(uuid4())), ('reply_app_message_id', str(uuid4())),
    ('history_actor_principal_id', str(uuid4())), ('history_source_email_id', str(uuid4())),
    ('history_case_id', str(uuid4())),
    ('history_previous_version', 1), ('history_resulting_version', 3)])
def test_cross_linked_actual_message_or_history_is_rolled_back(key, value):
    f = Store(); f.receipt_mutation = lambda store, result: result.update({key: value})
    assert f.run().state == 'unknown' and f.commits == 0


@pytest.mark.parametrize('key,value', [
    ('challenge_control_email_id', str(uuid4())), ('challenge_source_email_id', str(uuid4())),
    ('challenge_id', str(uuid4())), ('challenge_attempts', 0), ('challenge_state', 'pending'),
    ('source_reply_state', 'challenged')])
def test_verification_cannot_succeed_without_atomic_challenge_consumption_and_source_publication(key, value):
    f = Store(); f.challenge()
    f.receipt_mutation = lambda store, result: result.update({key: value})
    assert f.run().state == 'unknown' and f.commits == 0


def test_private_mode_never_calls_legacy_candidate_with_side_effects():
    repo = SimpleNamespace(private_candidate=AsyncMock(side_effect=RuntimeError('missing capability')),
                           snapshot=AsyncMock(), apply=AsyncMock())
    f = Store(); worker = ReplyWorker(repo, SECRET, private_replies=True, private_access=f.access)
    assert asyncio.run(worker(f.lease)).state == 'blocked'
    repo.snapshot.assert_not_awaited(); repo.apply.assert_not_awaited()


@pytest.mark.parametrize('worker_kind', ['reply', 'challenge'])
def test_default_off_denies_private_child_even_with_empty_parent(worker_kind):
    f = Store()
    snapshot = dict(labels=[], message_labels=[BOOKING], state='pending', send_state='queued')
    repo = SimpleNamespace(snapshot=AsyncMock(return_value=snapshot), challenge=AsyncMock(return_value=snapshot),
                           apply=AsyncMock(), reserve=AsyncMock())
    transport = SimpleNamespace(send=AsyncMock())
    worker = ReplyWorker(repo, SECRET) if worker_kind == 'reply' else ChallengeWorker(repo, transport, SECRET)
    assert asyncio.run(worker(f.lease)).state == 'blocked'
    repo.apply.assert_not_awaited(); repo.reserve.assert_not_awaited(); transport.send.assert_not_awaited()


def test_private_adapter_validates_result_inside_transaction_and_rolls_back():
    f = Store(); db = Database(); repo = PostgresReplies(db)
    def reject(result):
        assert db.in_tx
        raise ValueError('bad persisted graph')
    with pytest.raises(ValueError):
        asyncio.run(repo.private_apply(f.lease, f.projection(), {'action': 'review'},
            before_commit=lambda: None, result_check=reject))
    assert db.rollbacks == 1 and db.commits == 0
    assert 'email_private_reply_apply' in db.query[0]


def test_private_adapter_candidate_uses_new_read_only_port():
    f = Store(); db = Database()
    asyncio.run(PostgresReplies(db).private_candidate(f.lease))
    assert 'email_private_reply_candidate' in db.query[0]


def test_async_result_checker_is_not_awaited_or_committed():
    f = Store(); db = Database()
    async def forbidden(result):
        raise AssertionError('Must be synchronous')
    with pytest.raises(TypeError):
        asyncio.run(PostgresReplies(db).private_apply(f.lease, {}, {},
            before_commit=lambda: None, result_check=forbidden))
    assert db.rollbacks == 1 and db.commits == 0
