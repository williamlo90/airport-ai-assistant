"""Opt-in, worker-owned private reply publication; no browser impersonation.

Candidate is a read-only SQL capability. Apply must compare the exact candidate
and validate its actual persisted graph before commit. No legacy SQL fallback.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from airport_assistant.modules.jobs.service import Outcome
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.private_content import inherited_records
from .private_outbound import fingerprint, MailAuthorizationDenied
from .service import address


GROUPS = ('case_labels', 'conversation_labels', 'draft_labels', 'message_labels',
          'job_labels', 'email_labels', 'referenced_email_labels', 'challenge_labels',
          'reply_labels', 'history_labels')
TERMINAL = {'published', 'review', 'rejected', 'verification_reply', 'challenged'}
CASE_STATES = {'new', 'in_progress', 'awaiting_customer', 'awaiting_external', 'closed'}


def uuid_text(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise MailAuthorizationDenied()
    return value


def digest_text(value):
    return hashlib.sha256(value.encode()).hexdigest()


def utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MailAuthorizationDenied()
    return parsed


def lease_deadline(raw, started):
    remaining = (utc(raw['lease_until']) - utc(raw['server_now'])).total_seconds()
    if not 0 < remaining <= 300:
        raise MailAuthorizationDenied()
    return started + remaining


@dataclass(frozen=True)
class ReplyCandidate:
    wire: str = field(repr=False)
    basis: str
    deadline: float = field(repr=False)
    labels: SecurityLabels | None = field(repr=False)

    @property
    def data(self):
        return json.loads(self.wire)

    @classmethod
    def capture(cls, lease, raw, started):
        if type(raw) is not dict or type(raw.get('classification_version')) is not int or raw['classification_version'] != 2:
            raise MailAuthorizationDenied()
        bindings = dict(tenant_id=lease.tenant_id, job_id=lease.id,
                        worker_id=lease.worker_id, lease_token=lease.token)
        if any(not isinstance(value, UUID) or raw.get(key) != str(value) for key, value in bindings.items()):
            raise MailAuthorizationDenied()
        for name, expected in (('job_case_id', lease.case_id), ('job_draft_id', lease.draft_id)):
            if name not in raw or raw[name] != (str(expected) if expected is not None else None):
                raise MailAuthorizationDenied()
        if (lease.kind != 'email_reply' or raw.get('job_kind') != lease.kind
                or type(raw.get('lease_generation')) is not int or raw['lease_generation'] != lease.generation
                or raw.get('lease_valid') is not True):
            raise MailAuthorizationDenied()
        for key in ('case_id', 'email_id', 'source_email_id', 'referenced_email_id',
                    'account_id', 'principal_id', 'contact_id'):
            uuid_text(raw.get(key))
        if (raw.get('thread_case_id') != raw['case_id'] or raw.get('thread_account_id') != raw['account_id']
                or raw.get('source_case_id') != raw['case_id'] or raw.get('referenced_case_id') != raw['case_id']
                or raw.get('referenced_account_id') != raw['account_id']
                or raw.get('source_account_id') != raw['account_id']
                or raw.get('followup_contact_id') != raw['contact_id']
                or raw.get('owner_principal_id') != raw['principal_id']
                or (lease.case_id is not None and raw['case_id'] != str(lease.case_id))):
            raise MailAuthorizationDenied()
        for key in ('tenant_active', 'account_active', 'principal_active', 'verified', 'participant_active', 'eligible'):
            if raw.get(key) is not True:
                raise MailAuthorizationDenied()
        sender = address(raw.get('sender'))
        if sender != raw['sender'] or sender != raw.get('contact_address') or sender != raw.get('source_sender'):
            raise MailAuthorizationDenied()
        if type(raw.get('case_version')) is not int or raw['case_version'] < 1 or raw.get('case_status') not in CASE_STATES:
            raise MailAuthorizationDenied()
        if raw.get('case_language') not in {'en', 'id'}:
            raise MailAuthorizationDenied()
        if raw.get('assigned_membership_id') is not None:
            uuid_text(raw['assigned_membership_id'])
        if type(raw.get('assignee_eligible')) is not bool or raw.get('conversation_mode') not in {None, 'ai', 'waiting_staff', 'human', 'ended'}:
            raise MailAuthorizationDenied()
        if raw.get('state') not in TERMINAL | {'pending'}:
            raise MailAuthorizationDenied()
        if raw.get('source_reply_state') not in TERMINAL | {'pending'}:
            raise MailAuthorizationDenied()
        if raw['source_email_id'] == raw['email_id'] and raw['source_reply_state'] != raw['state']:
            raise MailAuthorizationDenied()
        if any(key not in raw for key in ('case_close_reason', 'case_resolution', 'case_closed_at')):
            raise MailAuthorizationDenied()
        for key in ('content_digest', 'source_content_digest'):
            if type(raw.get(key)) is not str or not re.fullmatch('[a-f0-9]{64}', raw[key]):
                raise MailAuthorizationDenied()
        body = raw.get('source_body')
        if type(body) is not str or len(body) > 65536 or raw.get('source_body_hash') != digest_text(body):
            raise MailAuthorizationDenied()
        records = inherited_records(lease.tenant_id, *(raw.get(key) for key in GROUPS))
        if raw.get('labels') != records or raw.get('case_labels') != records:
            raise MailAuthorizationDenied()
        wire = json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(wire.encode()) > 200000:
            raise MailAuthorizationDenied()
        basis = fingerprint({k: v for k, v in raw.items() if k not in {'server_now', 'lease_until'}})
        return cls(wire, basis, lease_deadline(raw, started), SecurityLabels.from_records(lease.tenant_id, records) if records else None)

    def current(self, clock):
        if clock() >= self.deadline:
            raise MailAuthorizationDenied()

    def proof(self, clock, *, allow_mismatch=False):
        """Stored transport/challenge proof is separate from booking grants."""
        raw = self.data
        challenge_id = raw.get('verification_challenge_id')
        if challenge_id is None:
            auth = raw.get('authentication')
            if raw['source_email_id'] == raw['email_id'] and type(auth) is dict and auth.get('dkim') == 'pass' and auth.get('dmarc') == 'pass':
                return 'authenticated_email'
            return None
        proof = raw.get('challenge')
        if (type(proof) is not dict or raw['source_email_id'] == raw['email_id']
                or raw['source_reply_state'] != 'challenged' or raw['state'] != 'pending'):
            return None
        expected = dict(id=challenge_id, control_email_id=raw['email_id'], email_id=raw['source_email_id'],
                        case_id=raw['case_id'],
                        principal_id=raw['principal_id'], contact_id=raw['contact_id'],
                        account_id=raw['account_id'], recipient=raw['sender'],
                        source_content_digest=raw['source_content_digest'], pending_text_hash=raw['source_body_hash'])
        if any(proof.get(key) != value for key, value in expected.items()):
            return None
        try:
            uuid_text(challenge_id)
            remaining = (utc(proof['expires_at']) - utc(raw['server_now'])).total_seconds()
            if (proof.get('state') != 'pending' or type(proof.get('attempts')) is not int
                    or not 0 <= proof['attempts'] < 5 or not 0 < remaining <= 300
                    or clock() >= self.deadline - (utc(raw['lease_until']) - utc(proof['expires_at'])).total_seconds()):
                return None
            response, expected_hash = proof.get('response_hash'), proof.get('code_hash')
            if (type(response) is not str or type(expected_hash) is not str
                    or not re.fullmatch('[a-f0-9]{64}', response) or not re.fullmatch('[a-f0-9]{64}', expected_hash)):
                return None
            if not hmac.compare_digest(response, expected_hash):
                return 'challenge_attempt' if allow_mismatch else None
        except (KeyError, ValueError, TypeError):
            return None
        return 'challenge'


def command_for(candidate, clock):
    raw = candidate.data
    body = raw['source_body']
    first = body.strip().split('\n', 1)[0].strip().upper()
    proof = candidate.proof(clock)
    review = (proof is None or not 1 <= len(body) <= 4000 or not body.strip() or '\0' in body
              or first.startswith('VERIFY ') or first in {'NEW ISSUE', 'MASALAH BARU'})
    reopening = raw['case_status'] == 'closed' and first in {'CONTINUE SAME ISSUE', 'LANJUTKAN MASALAH YANG SAMA'}
    if raw['case_status'] == 'closed' and not reopening and body.strip().lower() not in {'thanks', 'thank you', 'terima kasih'}:
        # Preserve the legacy policy: authorized ambiguous text can be stored
        # for staff review, but does not reopen a closed case.
        ambiguous = True
    else:
        ambiguous = False
    if reopening and raw['conversation_mode'] in {'human', 'waiting_staff'}:
        review = True
    action = 'review' if review else 'verify' if proof == 'challenge' else 'publish'
    challenge_proof = candidate.proof(clock, allow_mismatch=True)
    attempt = challenge_proof in {'challenge', 'challenge_attempt'}
    event = None if review or raw['case_status'] == 'closed' and not reopening else 'reopened' if reopening else 'email_received'
    assignee = raw['assigned_membership_id']
    if reopening and not review and not raw['assignee_eligible']:
        assignee = None
    status = ('in_progress' if assignee else 'new') if reopening and not review else raw['case_status']
    source_state = 'published' if not review else 'review' if raw['source_email_id'] == raw['email_id'] else raw['source_reply_state']
    if attempt and review and raw['challenge']['attempts'] == 4:
        source_state = 'review'
    return dict(action=action, body=None if review else body, body_hash=None if review else raw['source_body_hash'],
                proof_kind=None if review else proof, history_event=event, case_status=status,
                case_version=raw['case_version'] + (1 if event else 0), assigned_membership_id=assignee,
                source_reply_state=source_state, conversation_mode=raw['conversation_mode'],
                case_close_reason=None if reopening and not review else raw['case_close_reason'],
                case_resolution=None if reopening and not review else raw['case_resolution'],
                case_closed_at=None if reopening and not review else raw['case_closed_at'],
                review_reason='CLOSED_REPLY_AMBIGUOUS' if ambiguous and not review else None,
                labels=raw['labels'], input_basis_hash=candidate.basis, challenge_attempt=attempt,
                challenge_attempts=raw['challenge']['attempts'] + 1 if attempt else None,
                challenge_state=('consumed' if action == 'verify' else 'failed' if raw['challenge']['attempts'] == 4 else 'pending') if attempt else None)


class PrivateReplies:
    def __init__(self, repository, source_access, *, clock=time.monotonic):
        self.repository, self.source_access, self.clock = repository, source_access, clock

    async def candidate(self, lease):
        started = self.clock()
        return ReplyCandidate.capture(lease, await self.repository.private_candidate(lease), started)

    @staticmethod
    def final_basis(candidate, command, message_id):
        """Only the specified publication transition may change final context.

        In particular sender/contact/thread/content/proof inputs cannot change
        underneath an unchanged command hash. SQL must project actual rows.
        """
        raw = candidate.data
        raw.update(state='review' if command['action'] == 'review' else 'verification_reply' if command['action'] == 'verify' else 'published',
            source_reply_state=command['source_reply_state'], case_status=command['case_status'],
            case_version=command['case_version'], assigned_membership_id=command['assigned_membership_id'],
            case_close_reason=command['case_close_reason'], case_resolution=command['case_resolution'],
            case_closed_at=command['case_closed_at'], app_message_id=message_id)
        for group in ('email_labels', 'reply_labels', 'job_labels'):
            raw[group] = command['labels']
        if command['action'] != 'review':
            raw['message_labels'] = command['labels']
        if command['history_event']:
            raw['history_labels'] = command['labels']
        if command['challenge_attempt']:
            raw['challenge_labels'] = command['labels']
            raw['challenge'].update(state=command['challenge_state'], attempts=command['challenge_attempts'])
        return {key: value for key, value in raw.items() if key not in {'server_now', 'lease_until'}}

    def verify_result(self, lease, candidate, command, started, result):
        if result is False:
            return
        if type(result) is not dict:
            raise MailAuthorizationDenied()
        raw = candidate.data
        expected = {key: raw[key] for key in ('tenant_id', 'job_id', 'email_id', 'source_email_id',
                                             'case_id', 'account_id', 'principal_id', 'contact_id',
                                             'worker_id', 'lease_token', 'lease_generation')}
        expected.update(classification_version=2, lease_valid=True, command_hash=fingerprint(command),
                        state='review' if command['action'] == 'review' else 'verification_reply' if command['action'] == 'verify' else 'published',
                        case_status=command['case_status'], case_version=command['case_version'],
                        assigned_membership_id=command['assigned_membership_id'], history_event=command['history_event'],
                        message_body_hash=command['body_hash'], proof_kind=command['proof_kind'],
                        challenge_attempts=command['challenge_attempts'], challenge_state=command['challenge_state'],
                        source_reply_state=command['source_reply_state'], conversation_mode=command['conversation_mode'],
                        case_close_reason=command['case_close_reason'], case_resolution=command['case_resolution'],
                        case_closed_at=command['case_closed_at'],
                        message_author_principal_id=raw['principal_id'] if command['action'] != 'review' else None,
                        message_source_email_id=raw['source_email_id'] if command['action'] != 'review' else None,
                        message_source_kind='email_reply' if command['action'] != 'review' else None,
                        message_channel='email' if command['action'] != 'review' else None,
                        message_language=raw['case_language'] if command['action'] != 'review' else None,
                        message_case_id=raw['case_id'] if command['action'] != 'review' else None,
                        message_content_digest=raw['source_content_digest'] if command['action'] != 'review' else None,
                        history_source_email_id=raw['source_email_id'] if command['history_event'] else None,
                        history_case_id=raw['case_id'] if command['history_event'] else None,
                        history_actor_principal_id=raw['principal_id'] if command['history_event'] else None,
                        history_previous_version=raw['case_version'] if command['history_event'] else None,
                        history_resulting_version=command['case_version'] if command['history_event'] else None,
                        challenge_id=raw.get('verification_challenge_id') if command['challenge_attempt'] else None,
                        challenge_control_email_id=raw['email_id'] if command['challenge_attempt'] else None,
                        challenge_source_email_id=raw['source_email_id'] if command['challenge_attempt'] else None,
                        review_reason=command['review_reason'])
        if (any(key not in result or result[key] != value or type(result[key]) is not type(value) for key, value in expected.items())
                or self.clock() >= lease_deadline(result, started)):
            raise MailAuthorizationDenied()
        if command['action'] == 'review':
            if result.get('app_message_id') is not None:
                raise MailAuthorizationDenied()
        else:
            uuid_text(result.get('app_message_id'))
            if result.get('email_app_message_id') != result['app_message_id'] or result.get('reply_app_message_id') != result['app_message_id']:
                raise MailAuthorizationDenied()
        if command['history_event'] is None:
            if result.get('history_id') is not None:
                raise MailAuthorizationDenied()
        else:
            uuid_text(result.get('history_id'))
        for group in ('case_labels', 'email_labels', 'reply_labels', 'message_labels', 'history_labels'):
            labels = [] if group == 'message_labels' and command['action'] == 'review' or group == 'history_labels' and command['history_event'] is None else raw['labels']
            if result.get(group) != labels:
                raise MailAuthorizationDenied()
        final = ReplyCandidate.capture(lease, result.get('final_context'), started)
        final.current(self.clock)
        if fingerprint(self.final_basis(candidate, command, result.get('app_message_id'))) != final.basis:
            raise MailAuthorizationDenied()

    async def __call__(self, lease):
        if lease.kind != 'email_reply':
            return Outcome('blocked', 'BLOCKED')
        try:
            candidate = await self.candidate(lease)
            candidate.current(self.clock)
            if candidate.data['state'] in TERMINAL:
                return Outcome('succeeded', 'OK')
            if candidate.labels is not None:
                allowed = False if self.source_access is None else await self.source_access.allows(
                    tenant_id=lease.tenant_id, principal_id=UUID(candidate.data['principal_id']), labels=candidate.labels)
                if allowed is not True:
                    raise MailAuthorizationDenied()
            checked = self.clock()
            def fresh():
                if candidate.labels is not None and not 0 <= self.clock() - checked < 2:
                    raise MailAuthorizationDenied()
            current = await self.candidate(lease)
            if current.basis != candidate.basis:
                raise MailAuthorizationDenied()
            current.current(self.clock)
            fresh()
            command = command_for(current, self.clock)
        except Exception:
            return Outcome('blocked', 'BLOCKED')
        started = self.clock()
        def verify(result):
            self.verify_result(lease, current, command, started, result)
            # Check challenge TTL again after the LAST SQL result await.
            if command['challenge_attempt'] and current.proof(self.clock, allow_mismatch=True) not in {'challenge', 'challenge_attempt'}:
                raise MailAuthorizationDenied()
            fresh()
        try:
            result = await self.repository.private_apply(lease, current.data, command,
                before_commit=fresh, result_check=verify)
            verify(result)
        except Exception:
            return Outcome('unknown', 'UNKNOWN')
        return Outcome('succeeded', 'OK') if type(result) is dict else Outcome('blocked', 'BLOCKED')
