"""Typed worker-owned private send operation; no fabricated browser identity.

Future SQL ports must verify exact reservation graphs/fences. No private schema
or constructor is activated here. Source and transport calls are outside DB locks.
"""
from dataclasses import dataclass,field,asdict
from datetime import datetime,timezone
from uuid import UUID,uuid5
import hashlib,json,re,time

from airport_assistant.modules.jobs.service import Outcome
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.private_content import inherited_records
from airport_assistant.modules.source.models import SourceFailure
from .service import Envelope,SendResult,address


class BeginNotCommitted(Exception):
    """Adapter's synchronous pre-commit callback rejected and transaction rolled back."""


class MailAuthorizationDenied(Exception):pass


GROUPS=('case_labels','conversation_labels','draft_labels','message_labels','job_labels','email_labels','reconcile_labels')
VOLATILE={'server_now','lease_until'}
RESERVED={'delivery_status','delivery_version','email_id','envelope_hash','reconcile_job_id','reconcile_email_id'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def envelope_hash(envelope):
    return fingerprint({key:str(value) if isinstance(value,UUID) else value for key,value in asdict(envelope).items()})


@dataclass(frozen=True)
class PrivateMailContext:
    wire_json:str=field(repr=False)
    basis_hash:str
    requirements:SecurityLabels|None=field(repr=False)
    lease_deadline:float=field(repr=False)

    @property
    def data(self):return json.loads(self.wire_json)

    @classmethod
    def capture(cls,lease,raw,read_started):
        if type(raw) is not dict or raw.get('classification_version')!=2 or type(raw.get('classification_version')) is not int:
            raise MailAuthorizationDenied()
        bindings=dict(tenant_id=lease.tenant_id,case_id=lease.case_id,job_id=lease.id,draft_id=lease.draft_id,
            worker_id=lease.worker_id,lease_token=lease.token)
        for key,expected in bindings.items():
            if not isinstance(expected,UUID) or raw.get(key)!=str(expected):raise MailAuthorizationDenied()
        if (raw.get('job_kind')!=lease.kind or type(raw.get('lease_generation')) is not int
            or raw['lease_generation']!=lease.generation or raw.get('lease_valid') is not True):raise MailAuthorizationDenied()
        for key in ('case_version','source_message_version'):
            if type(raw.get(key)) is not int or raw[key]<1:raise MailAuthorizationDenied()
        for key in ('app_message_id',):
            if not isinstance(raw.get(key),str) or str(UUID(raw[key]))!=raw[key]:raise MailAuthorizationDenied()
        for key in ('contact_id','followup_contact_id','principal_id','owner_principal_id','account_id'):
            if raw.get(key) is not None and (not isinstance(raw[key],str) or str(UUID(raw[key]))!=raw[key]):raise MailAuthorizationDenied()
        for key in ('tenant_active','account_active','principal_active','verified','participant_active'):
            if type(raw.get(key)) is not bool:raise MailAuthorizationDenied()
        records=inherited_records(lease.tenant_id,*(raw.get(group) for group in GROUPS))
        if type(raw.get('labels')) is not list or raw['labels']!=records:raise MailAuthorizationDenied()
        try:
            server=datetime.fromisoformat(raw['server_now']);until=datetime.fromisoformat(raw['lease_until'])
            if server.utcoffset()!=timezone.utc.utcoffset(server) or until.utcoffset()!=timezone.utc.utcoffset(until):raise ValueError()
            remaining=(until-server).total_seconds()
            if not 0<remaining<=300:raise ValueError()
        except (KeyError,TypeError,ValueError):raise MailAuthorizationDenied() from None
        state=raw.get('delivery_status')
        if state not in {None,'sending','accepted','delivered','bounced','failed','delivery_unknown'}:raise MailAuthorizationDenied()
        for key in RESERVED:
            if key not in raw:raise MailAuthorizationDenied()
        if state is None:
            if any(raw[key] is not None for key in RESERVED):raise MailAuthorizationDenied()
        else:
            if raw['email_id']!=str(uuid5(lease.id,'case-created-email')) or type(raw['delivery_version']) is not int or raw['delivery_version']<1:
                raise MailAuthorizationDenied()
            if not isinstance(raw['envelope_hash'],str) or not re.fullmatch('[a-f0-9]{64}',raw['envelope_hash']):raise MailAuthorizationDenied()
            if raw['reconcile_email_id']!=raw['email_id'] or not isinstance(raw['reconcile_job_id'],str):raise MailAuthorizationDenied()
            UUID(raw['reconcile_job_id'])
        if lease.kind=='email_send':
            body=raw.get('body')
            if (type(body) is not str or not 1<=len(body)<=4000 or not body.strip() or '\0' in body
                or raw.get('message_hash')!=hashlib.sha256(body.encode()).hexdigest()):raise MailAuthorizationDenied()
        elif raw.get('body') is not None:raise MailAuthorizationDenied()
        text=json.dumps(raw,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        return cls(text,fingerprint({k:v for k,v in raw.items() if k not in VOLATILE}),
            SecurityLabels.from_records(lease.tenant_id,records) if records else None,read_started+remaining)

    def check_lease(self,clock):
        if clock()>=self.lease_deadline:raise SourceFailure(503)

    def verify_new_reservation(self,before,envelope):
        actual=self.data;old=before.data;required=old['labels']
        expected={key:value for key,value in old.items() if key not in VOLATILE}
        expected.update(delivery_status='sending',delivery_version=1,email_id=str(envelope.id),envelope_hash=envelope_hash(envelope),
            email_labels=required,reconcile_labels=required,reconcile_job_id=actual['reconcile_job_id'],reconcile_email_id=str(envelope.id))
        if {key:value for key,value in actual.items() if key not in VOLATILE}!=expected:raise MailAuthorizationDenied()


class PrivateOutbound:
    def __init__(self,owner,*,clock=time.monotonic):self.owner,self.clock=owner,clock

    async def context(self,lease,method,*args,**kwargs):
        started=self.clock()
        raw=await method(lease,*args,**kwargs)
        return PrivateMailContext.capture(lease,raw,started)

    def eligible(self,context):
        data=context.data
        if (not all(data[key] is True for key in ('tenant_active','account_active','principal_active','verified','participant_active'))
            or data['contact_id'] is None or data['principal_id'] is None or data['account_id'] is None
            or data['contact_id']!=data['followup_contact_id'] or data['principal_id']!=data['owner_principal_id']
            or data.get('provider_account_ref') not in self.owner._account_refs):raise MailAuthorizationDenied()
        for key in ('recipient','sender','reply_to'):
            if address(data.get(key))!=data[key]:raise MailAuthorizationDenied()
        context.check_lease(self.clock)

    async def authorize(self,lease,context):
        self.eligible(context)
        if context.requirements is not None:
            allowed=False if self.owner._private is None else await self.owner._private.allows(
                tenant_id=lease.tenant_id,principal_id=UUID(context.data['principal_id']),labels=context.requirements)
            if allowed is not True:raise MailAuthorizationDenied()
        checked=self.clock()
        def check():
            # Only source-grant age belongs to this anchor. A heartbeat can
            # extend the same fenced lease while the semantic basis stays
            # unchanged. Current/final projections check the latest lease;
            # SQL ports must also enforce their live worker/token/generation
            # fence inside the reservation/read transaction. Never retain an
            # obsolete lease expiry in a source-grant freshness callback.
            if context.requirements is not None and not 0<=self.clock()-checked<2:raise SourceFailure(503)
        check()
        return check

    async def abort(self,lease,envelope):
        # Called only before the transport invocation. The SQL port is fenced,
        # exact-attempt bound, and must never overwrite accepted/unknown evidence.
        try:ok=await self.owner._repository.abort_unsent(lease,envelope.id,envelope_hash(envelope))
        except Exception:ok=False
        return Outcome('blocked','BLOCKED') if ok is True else Outcome('unknown','UNKNOWN')

    async def __call__(self,lease):
        repo=self.owner._repository
        try:
            candidate=await self.context(lease,repo.private_snapshot)
            state=candidate.data['delivery_status']
            if state in {'accepted','delivered'}:return Outcome('succeeded','OK')
            if state is not None:return Outcome('unknown','UNKNOWN')
            check=await self.authorize(lease,candidate)
            current=await self.context(lease,repo.private_snapshot)
            if current.basis_hash!=candidate.basis_hash:raise MailAuthorizationDenied()
            self.eligible(current);check();current.check_lease(self.clock)
            envelope=self.owner._envelope(lease,current.data)
            check()
        except Exception:return Outcome('blocked','BLOCKED')
        started=self.clock()
        try:
            raw=await repo.private_begin(lease,current.data,envelope,before_commit=check)
        except BeginNotCommitted:return Outcome('blocked','BLOCKED')
        except Exception:return Outcome('unknown','UNKNOWN')
        # Only an explicit False means the repository positively rejected the
        # reservation. A missing projection can also be a lost commit result.
        if raw is False:return Outcome('blocked','BLOCKED')
        try:
            reserved=PrivateMailContext.capture(lease,raw,started)
            reserved.verify_new_reservation(current,envelope)
        except Exception:return Outcome('unknown','UNKNOWN')
        try:
            post=await self.context(lease,repo.private_send_snapshot,envelope.id)
            if post.basis_hash!=reserved.basis_hash:raise MailAuthorizationDenied()
            check=await self.authorize(lease,post)
            final=await self.context(lease,repo.private_send_snapshot,envelope.id,before_commit=check)
            if final.basis_hash!=reserved.basis_hash:raise MailAuthorizationDenied()
            self.eligible(final);check();final.check_lease(self.clock)
        except Exception:return await self.abort(lease,envelope)
        # NO awaited work between the final check and invoking send. Once this
        # call begins, failures/cancellation cannot be classified as unsent.
        try:
            result=await self.owner._transport.send(envelope)
            if type(result) is not SendResult:result=SendResult('delivery_unknown')
        except Exception:result=SendResult('delivery_unknown')
        try:ok=await repo.private_complete(lease,envelope.id,envelope_hash(envelope),result)
        except Exception:ok=False
        if ok is not True:return Outcome('unknown','UNKNOWN')
        return {'accepted':Outcome('succeeded','OK'),'failed':Outcome('dead','PERMANENT'),
            'delivery_unknown':Outcome('unknown','UNKNOWN')}[result.state]
