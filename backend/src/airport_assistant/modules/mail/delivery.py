"""Delivery evidence and bounded read-only reconciliation; never resend mail."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID
from airport_assistant.modules.jobs.service import Outcome
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError


STATES = frozenset({'accepted', 'delivered', 'bounced', 'failed', 'delivery_unknown'})
TERMINAL = frozenset({'delivered', 'bounced', 'failed'})
REASONS = frozenset({'NO_RECEIPT', 'LOOKUP_UNAVAILABLE', 'PENDING_DELIVERY', 'DELIVERY_FAILED', 'CONFLICTING_EVIDENCE'})


@dataclass(frozen=True)
class DeliveryEvidence:
    """Internal port; callbacks must authenticate their origin before constructing it.

    Polls have no provider event timestamp: created_at is not delivery time.
    Tracking opened/clicked is deliberately not a read receipt in this contract.
    """
    provider_message_id: str
    state: str
    rfc_message_id: str | None = field(default=None, repr=False)
    event_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.provider_message_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.provider_message_id) or self.state not in STATES - {'delivery_unknown'}:
            raise ValueError('Invalid provider evidence.')
        if self.rfc_message_id is not None and not re.fullmatch(r'<[^<>\s\x00-\x1f\x7f]{1,996}>', self.rfc_message_id):
            raise ValueError('Invalid provider RFC message ID.')
        if self.event_at is not None and (not isinstance(self.event_at, datetime) or self.event_at.tzinfo is None or self.event_at.utcoffset() is None):
            raise ValueError('Timezone-aware provider event time required.')

    def record(self):
        data = dict(provider_message_id=self.provider_message_id,state=self.state,rfc_message_id=self.rfc_message_id,
                    event_at=self.event_at.astimezone(timezone.utc).isoformat() if self.event_at else None)
        data['key'] = hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return data


def delivery_transition(current, incoming, *, current_at=None, event_at=None):
    """Older callbacks/lower states never undo confirmed delivery evidence."""
    if current not in STATES | {'sending'} or incoming not in STATES:
        raise ValueError('Invalid delivery state.')
    if current_at is not None and event_at is not None and event_at < current_at:
        return current, False
    if current in TERMINAL:
        return current, incoming in TERMINAL and incoming != current
    if current == 'accepted' and incoming == 'delivery_unknown':
        return current, False
    return incoming, False


class ReconcileMail:
    def __init__(self, repository, transport, *, account_refs):
        self._repository,self._transport,self._accounts = repository,transport,frozenset(account_refs)

    async def __call__(self, lease):
        if lease.kind != 'email_reconcile':return Outcome('blocked','BLOCKED')
        snapshot = await self._repository.delivery_snapshot(lease)
        if not snapshot or snapshot['tenant_id'] != str(lease.tenant_id) or snapshot['case_id'] != str(lease.case_id) or snapshot['provider_account_ref'] not in self._accounts:
            return Outcome('blocked','BLOCKED')
        if snapshot['source_busy']:
            return Outcome('retry','RETRYABLE')
        if snapshot['delivery_status'] in TERMINAL:
            # No GET is needed for an already terminal result.
            return Outcome('succeeded','OK')
        reason=None;evidence=None
        if not snapshot['provider_message_id']:
            reason='NO_RECEIPT'
        else:
            try:evidence = await self._transport.lookup(snapshot)
            except Exception:evidence=None
            if type(evidence) is not DeliveryEvidence or evidence.provider_message_id != snapshot['provider_message_id']:
                evidence=None
                if snapshot['attempts'] < snapshot['max_attempts']:return Outcome('retry','RETRYABLE')
                reason='LOOKUP_UNAVAILABLE'
            elif evidence.state=='accepted' and snapshot['attempts']>=snapshot['max_attempts']:
                reason='PENDING_DELIVERY'
        effective_state=snapshot['delivery_status']
        if evidence is not None:
            current_at=snapshot.get('provider_status_at')
            if isinstance(current_at,str):current_at=datetime.fromisoformat(current_at)
            effective_state,_=delivery_transition(effective_state,evidence.state,current_at=current_at,event_at=evidence.event_at)
        if not await self._repository.observe(lease,snapshot,evidence,reason):
            return Outcome('unknown','UNKNOWN')
        if evidence is not None and effective_state=='accepted' and reason is None:
            return Outcome('retry','RETRYABLE')
        return Outcome('succeeded','OK')


class DeliveryStatus:
    def __init__(self,repository,*,private_history=False):
        from .history import StaffMailHistory
        self._history=StaffMailHistory(repository,private_history=private_history)

    async def list(self,identity,case_id):
        return await self._history.read(identity,case_id,'deliveries')
