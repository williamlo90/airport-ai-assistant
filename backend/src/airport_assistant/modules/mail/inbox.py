"""Authenticated inbox metadata and quarantined content, before case identity."""
import base64
from dataclasses import dataclass,field,replace
from datetime import datetime,timezone
import hashlib
import hmac
import json
import re
import time
from uuid import UUID
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.jobs.service import Outcome
from airport_assistant.modules.mail.delivery import DeliveryEvidence

EVENT_STATES={'email.sent':'accepted','email.delivered':'delivered','email.bounced':'bounced','email.failed':'failed',
              'email.suppressed':'failed','email.delivery_delayed':'accepted'}


def signing_key(value):
    if not isinstance(value,str) or not value.startswith('whsec_'):raise ValueError('Invalid webhook key.')
    try:key=base64.b64decode(value[6:],validate=True)
    except Exception:raise ValueError('Invalid webhook key.') from None
    if not 16<=len(key)<=64:raise ValueError('Invalid webhook key.')
    return key


def verify(raw,headers,secrets,*,now=None):
    """Svix signs the exact id.timestamp.body bytes, before JSON parsing."""
    try:
        id,stamp,signatures=(headers[k] for k in ('svix-id','svix-timestamp','svix-signature'))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id) or not re.fullmatch(r'[0-9]{1,12}',stamp):return False
        if abs((time.time() if now is None else now)-int(stamp))>300 or not 1<=len(signatures)<=1024:return False
        candidates=signatures.split()
        if not 1<=len(candidates)<=8:return False
        message=id.encode()+b'.'+stamp.encode()+b'.'+raw
        for secret in secrets:
            expected=hmac.new(signing_key(secret),message,hashlib.sha256).digest()
            for token in candidates:
                if not token.startswith('v1,'):continue
                try:signature=base64.b64decode(token[3:],validate=True)
                except Exception:continue
                if hmac.compare_digest(expected,signature):return True
    except Exception:return False
    return False


def normalize(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key.')
            result[key]=value
        return result
    payload=json.loads(raw,object_pairs_hook=unique)
    kind=payload['type']
    if not isinstance(kind,str) or not re.fullmatch(r'[a-z]+\.[a-z_]{1,60}',kind):raise ValueError('Invalid event type.')
    supported=kind=='email.received' or kind in EVENT_STATES
    id=payload.get('data',{}).get('email_id') if isinstance(payload.get('data'),dict) else None
    stamp=payload.get('created_at')
    event_at=None
    if supported:
        if not isinstance(id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id):raise ValueError('Invalid provider ID.')
        if not isinstance(stamp,str) or len(stamp)>64:raise ValueError('Invalid provider event time.')
        event_at=datetime.fromisoformat(stamp)
        if event_at.tzinfo is None or event_at.utcoffset() is None or event_at.timestamp()>time.time()+60:raise ValueError('Invalid provider event time.')
    return dict(event_type=kind,provider_message_id=id if supported else None,event_at=event_at.astimezone(timezone.utc).isoformat() if event_at else None,
                supported=supported,digest=hashlib.sha256(raw).hexdigest())


class WebhookIngress:
    def __init__(self,repository,webhooks):self._repository,self._webhooks=repository,webhooks

    async def receive(self,alias,raw,headers):
        config=self._webhooks.get(alias)
        if config is None or len(raw)>65536 or not verify(raw,headers,[v.get_secret_value() for v in config.secrets]):
            raise AuthError(401,'WEBHOOK_INVALID')
        try:event=normalize(raw)
        except Exception:raise AuthError(400,'WEBHOOK_INVALID') from None
        result=await self._repository.accept(alias,config.account_ref,headers['svix-id'],event)
        if result=='conflict':raise AuthError(409,'WEBHOOK_CONFLICT')
        if result!='accepted':raise AuthError(503,'WEBHOOK_UNAVAILABLE')


@dataclass(frozen=True,repr=False)
class ReceivedContent:
    sender:str
    recipient:str
    subject:str
    text:str|None
    html:str|None
    rfc_message_id:str|None
    in_reply_to:str|None
    references:str|None
    reply_to:str|None
    attachment_count:int
    authentication:dict|None=None
    delivered_to:str|None=None
    verification_challenge_id:str|None=None
    verification_response_hash:str|None=None

    def record(self):
        from dataclasses import asdict
        data=asdict(self)
        data['digest']=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return data


@dataclass(frozen=True)
class ReceiptContext:
    """GET corroborates the envelope, without asserting a delivery/read state."""
    provider_message_id:str
    rfc_message_id:str|None=field(default=None,repr=False)

    def __post_init__(self):
        if not isinstance(self.provider_message_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',self.provider_message_id):raise ValueError('Invalid provider receipt.')
        if self.rfc_message_id is not None and (not isinstance(self.rfc_message_id,str) or not re.fullmatch(r'<[^<>\s\x00-\x1f\x7f]{1,996}>',self.rfc_message_id)):raise ValueError('Invalid RFC ID.')


class InboxWorker:
    def __init__(self,repository,mail_repository,transport,*,accounts):
        self._repository,self._mail,self._transport,self._accounts=repository,mail_repository,transport,frozenset(accounts)

    async def __call__(self,lease):
        if lease.kind not in {'email_fetch','email_event'}:return Outcome('blocked','BLOCKED')
        snap=await self._repository.snapshot(lease)
        if not snap or snap['provider_account_ref'] not in self._accounts:return Outcome('blocked','BLOCKED')
        if snap['processing_state'] in {'processed','review','ignored'}:return Outcome('succeeded','OK')
        if lease.kind=='email_fetch':
            if snap['existing_message_id']:
                ok=await self._repository.complete(lease,None,None)
                return Outcome('succeeded','OK') if ok else Outcome('retry','RETRYABLE')
            try:content=await self._transport.receive(snap)
            except Exception:content=None
            if type(content) is not ReceivedContent:
                if snap['attempts']<snap['max_attempts']:return Outcome('retry','RETRYABLE')
                ok=await self._repository.complete(lease,None,'FETCH_UNAVAILABLE')
            else:ok=await self._repository.complete(lease,content.record(),None)
            return Outcome('succeeded','OK') if ok else Outcome('retry','RETRYABLE')
        binding=await self._repository.bind(lease)
        if binding is None:
            if snap['attempts']<snap['max_attempts']:return Outcome('retry','RETRYABLE')
            ok=await self._repository.complete(lease,None,'UNMATCHED_RECEIPT')
            return Outcome('succeeded','OK') if ok else Outcome('retry','RETRYABLE')
        bound=replace(lease,case_id=UUID(binding['case_id']),draft_id=UUID(binding['draft_id']))
        delivery=await self._mail.delivery_snapshot(bound)
        if delivery is None or delivery['source_busy']:return Outcome('retry','RETRYABLE')
        try:proof=await self._transport.context(delivery)
        except Exception:proof=None
        if type(proof) is not ReceiptContext or proof.provider_message_id!=snap['provider_message_id']:
            if snap['attempts']<snap['max_attempts']:return Outcome('retry','RETRYABLE')
            ok=await self._repository.complete(lease,None,'FETCH_UNAVAILABLE')
            return Outcome('succeeded','OK') if ok else Outcome('retry','RETRYABLE')
        evidence=DeliveryEvidence(proof.provider_message_id,EVENT_STATES[snap['event_type']],proof.rfc_message_id,datetime.fromisoformat(snap['event_at']))
        if not await self._mail.observe(bound,delivery,evidence):return Outcome('retry','RETRYABLE')
        ok=await self._repository.complete(lease,None,None)
        return Outcome('succeeded','OK') if ok else Outcome('retry','RETRYABLE')
