"""Recipient decisions belong here, never in the transport or scheduler."""
from dataclasses import dataclass, field
import re
from typing import Protocol
from uuid import UUID, uuid5

from email_validator import validate_email, EmailNotValidError
from airport_assistant.modules.jobs.service import Lease, Outcome
from airport_assistant.modules.source.labels import SecurityLabels


def address(value):
    if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
        raise ValueError('Invalid email address.')
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError:
        raise ValueError('Invalid email address.') from None


@dataclass(frozen=True)
class Envelope:
    id: UUID
    app_message_id: UUID
    account_id: UUID
    sender: str = field(repr=False)
    recipient: str = field(repr=False)
    reply_to: str = field(repr=False)
    rfc_message_id: str
    delivery_key: str
    subject: str = field(repr=False)
    text: str = field(repr=False)
    provider_account_ref: str = ''
    reply_token_hash:str|None=field(default=None,repr=False)

    def __post_init__(self):
        for item in (self.sender, self.recipient, self.reply_to):
            if address(item) != item:
                raise ValueError('Normalized email required.')
        if not re.fullmatch(r'<[a-f0-9]{32}@[A-Za-z0-9.-]+>', self.rfc_message_id):
            raise ValueError('Invalid RFC message ID.')
        if not re.fullmatch(r'mail/[a-f0-9]{32}/[a-f0-9]{32}', self.delivery_key):
            raise ValueError('Invalid delivery key.')
        if not 1 <= len(self.subject) <= 200 or any(c in self.subject for c in '\r\n\x00'):
            raise ValueError('Invalid subject.')
        if not 1 <= len(self.text) <= 8000 or '\x00' in self.text:
            raise ValueError('Invalid text.')


@dataclass(frozen=True)
class SendResult:
    state: str
    provider_message_id: str | None = None

    def __post_init__(self):
        if self.state not in {'accepted', 'failed', 'delivery_unknown'}:
            raise ValueError('Invalid provider result.')
        if self.state == 'accepted':
            if not isinstance(self.provider_message_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.provider_message_id):
                raise ValueError('Invalid provider receipt.')
        elif self.provider_message_id is not None:
            raise ValueError('Receipt is only valid for accepted sends.')


class MailTransport(Protocol):
    async def send(self, envelope: Envelope) -> SendResult: ...


class OutboundMail:
    """The current case-created notification contains no report or source data.

    Re-read the recipient and labels after external grant checks and conditionally
    reserve the exact projection before send. No DB lock spans provider calls.
    A persisted sending/uncertain attempt is never automatically sent again.
    """
    def __init__(self, repository, transport: MailTransport, *, account_ref: str, private_access=None,reply_secret=None,private_outbound=False):
        if type(private_outbound) is not bool:raise ValueError('Explicit private outbound switch required.')
        self._repository, self._transport = repository, transport
        self._account_refs = frozenset({account_ref} if isinstance(account_ref,str) else account_ref)
        self._private = private_access
        self._reply_secret=reply_secret
        self._private_outbound=None
        if private_outbound:
            from .private_outbound import PrivateOutbound
            self._private_outbound=PrivateOutbound(self)

    async def _allowed(self, lease, snapshot):
        if not snapshot or snapshot.get('tenant_id') != str(lease.tenant_id) or snapshot.get('case_id') != str(lease.case_id):
            return False
        if not all(snapshot.get(k) is True for k in ('tenant_active', 'account_active', 'principal_active', 'verified', 'participant_active')):
            return False
        if not snapshot.get('contact_id') or snapshot.get('contact_id') != snapshot.get('followup_contact_id'):
            return False
        if snapshot.get('principal_id') != snapshot.get('owner_principal_id') or snapshot.get('provider_account_ref') not in self._account_refs:
            return False
        try:
            for name in ('recipient', 'sender', 'reply_to'):
                if address(snapshot[name]) != snapshot[name]:
                    return False
            from airport_assistant.modules.source.private_content import inherited_records
            groups=[snapshot['labels']]
            extra=('case_labels','conversation_labels','draft_labels','message_labels','job_labels','email_labels','reconcile_labels')
            version=snapshot.get('classification_version',1)
            if type(version) is not int or version not in {1,2}:return False
            if version==2 and any(name not in snapshot for name in extra):return False
            groups.extend(snapshot[name] for name in extra if name in snapshot)
            records=inherited_records(lease.tenant_id,*groups)
            if records != []:
                labels = SecurityLabels.from_records(lease.tenant_id, records)
                if self._private is None or not await self._private.allows(tenant_id=lease.tenant_id, principal_id=UUID(snapshot['principal_id']), labels=labels):
                    return False
                # Persistence currently supports general cases only.
                return False
        except Exception:
            return False
        return True

    def _envelope(self, lease, snapshot):
        id = uuid5(lease.id, 'case-created-email')
        ref = snapshot['public_reference']
        if not re.fullmatch(r'CASE-[a-f0-9]{32}', ref) or snapshot['language'] not in {'en', 'id'}:
            raise ValueError('Invalid notification projection.')
        subject, text = ('Your support request was received', f'We received your request. Your case reference is {ref}. Our customer service team will follow up by email.')
        if snapshot['language'] == 'id':
            subject, text = ('Permintaan bantuan Anda sudah diterima', f'Permintaan Anda sudah diterima. Nomor kasus Anda adalah {ref}. Tim layanan pelanggan akan menindaklanjuti melalui email.')
        if lease.kind == 'email_send':
            subject, text = ('Reply to your support request' if snapshot['language'] == 'en' else 'Balasan permintaan bantuan Anda'), snapshot['body']
        reply_to=snapshot['reply_to'];token_hash=None
        if self._reply_secret is not None:
            from airport_assistant.modules.mail.replies import thread_token
            import hashlib
            token=thread_token(self._reply_secret,lease.tenant_id,id);local,domain=reply_to.rsplit('@',1)
            reply_to=local+'+r-'+token+'@'+domain;token_hash=hashlib.sha256(token.encode()).hexdigest()
            text+='\n\nIf this case is closed and the same issue continues, start your reply with CONTINUE SAME ISSUE.'
        return Envelope(id, UUID(snapshot['app_message_id']), UUID(snapshot['account_id']), snapshot['sender'], snapshot['recipient'], reply_to,
                        f'<{id.hex}@{snapshot["sender"].rsplit("@", 1)[1]}>', f'mail/{lease.tenant_id.hex}/{id.hex}', subject, text, snapshot['provider_account_ref'],token_hash)

    async def __call__(self, lease: Lease):
        if lease.kind not in {'case_created', 'email_send'}:
            return Outcome('blocked', 'BLOCKED')
        if self._private_outbound is not None:return await self._private_outbound(lease)
        snapshot = await self._repository.snapshot(lease)
        if not await self._allowed(lease, snapshot):
            return Outcome('blocked', 'BLOCKED')
        # Fresh object/contact/account state after the source authorization call.
        current = await self._repository.snapshot(lease)
        if current != snapshot:
            return Outcome('blocked', 'BLOCKED')
        if current.get('delivery_status') == 'accepted':
            return Outcome('succeeded', 'OK')
        if current.get('delivery_status') is not None:
            return Outcome('unknown', 'UNKNOWN')
        envelope = self._envelope(lease, current)
        if not await self._repository.begin(lease, current, envelope):
            return Outcome('blocked', 'BLOCKED')
        try:
            result = await self._transport.send(envelope)
            if type(result) is not SendResult:
                result = SendResult('delivery_unknown')
        except Exception:
            result = SendResult('delivery_unknown')
        if not await self._repository.complete(lease, envelope.id, result):
            return Outcome('unknown', 'UNKNOWN')
        return {'accepted': Outcome('succeeded', 'OK'), 'failed': Outcome('dead', 'PERMANENT'),
                'delivery_unknown': Outcome('unknown', 'UNKNOWN')}[result.state]
