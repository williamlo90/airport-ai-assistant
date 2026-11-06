"""Validated user reports, server destinations, and confirmation-bound previews."""

from dataclasses import dataclass,replace,field
from datetime import datetime,timezone
import hashlib
import hmac
import json
import re
from typing import Literal,Annotated,TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel,ConfigDict,Field,field_validator
from airport_assistant.modules.identity.auth import normalized_contact
from airport_assistant.modules.identity.access import OwnedObject

if TYPE_CHECKING:
    from airport_assistant.modules.source.evidence import SourceEvidence


class DraftError(Exception):
    def __init__(self,status,code,result_ref=None):
        self.status,self.code,self.result_ref=status,code,result_ref
        super().__init__(code)


class ReportPayload(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    category:Literal['information','booking_issue','transaction_issue']
    service_type:Literal['general','parking','lounge']
    summary:str=Field(min_length=1,max_length=200)
    customer_report:str=Field(min_length=1,max_length=4000)
    booking_reference:str|None=Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,80}$')
    transaction_reference:str|None=Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,80}$')
    contact_email:str|None=Field(default=None,min_length=3,max_length=320)
    attachment_ids:tuple[Annotated[UUID,Field(strict=False)],...]=Field(default=(),max_length=3,strict=False)

    @field_validator('attachment_ids')
    @classmethod
    def unique_attachments(cls,value):
        if len(set(value))!=len(value):raise ValueError('Repeated attachment.')
        return value

    @field_validator('summary','customer_report')
    @classmethod
    def text(cls,value):
        if not value.strip() or '\x00' in value:raise ValueError('Nonempty text required.')
        return value

    @field_validator('contact_email')
    @classmethod
    def email(cls,value):
        return normalized_contact(value) if value is not None else None


class Destination(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    queue_id:UUID
    organization_id:UUID
    queue_name:str=Field(min_length=1,max_length=200)
    organization_name:str=Field(min_length=1,max_length=200)
    service_type:Literal['general','parking','lounge']


@dataclass(frozen=True)
class Draft:
    id:UUID
    tenant_id:UUID
    conversation_id:UUID
    actor_principal_id:UUID|None
    actor_session_id:UUID|None
    created_session_id:UUID
    action_type:str
    payload:ReportPayload=field(repr=False)
    destination:Destination
    displayed_summary:str=field(repr=False)
    language:str
    version:int
    payload_hash:str
    created_at:datetime
    expires_at:datetime
    state:str='pending'
    case_id:UUID|None=None
    resource_security_labels:tuple=field(default=(),repr=False)
    result_case_id:UUID|None=None
    result_message_id:UUID|None=None
    result_ref:str|None=None
    committed_at:datetime|None=None
    case_access_verified:bool=False
    source_evidence:'SourceEvidence | None'=field(default=None,repr=False)

    def projection(self):
        return OwnedObject(self.tenant_id,self.id,'draft',self.actor_principal_id,self.actor_session_id,
                           not self.resource_security_labels and (self.case_id is None or self.case_access_verified))


def displayed_summary(action_type,payload,destination,language,case_id=None):
    if language=='id':
        labels=('Buat pengajuan' if action_type=='create_case' else 'Minta bantuan petugas',
                'Kategori','Layanan','Ringkasan','Laporan pelanggan','Referensi booking','Referensi transaksi','Email','Tujuan','Belum diisi')
    else:
        labels=('Create support request' if action_type=='create_case' else 'Request staff assistance',
                'Category','Service','Summary','Customer report','Booking reference','Transaction reference','Email','Destination','Not provided')
    values=(payload.category,payload.service_type,payload.summary,payload.customer_report,
            payload.booking_reference or labels[-1],payload.transaction_reference or labels[-1],
            payload.contact_email or labels[-1],destination.organization_name+' / '+destination.queue_name)
    result=labels[0]+'\n'+'\n'.join(label+': '+value for label,value in zip(labels[1:-1],values))
    if case_id is not None:result+='\n'+('Kasus yang sudah ada' if language=='id' else 'Existing case')+': CASE-'+case_id.hex
    if payload.attachment_ids:result+='\n'+('Lampiran' if language=='id' else 'Attachments')+': '+', '.join(map(str,payload.attachment_ids))
    return result


def source_review(payload,evidence):
    """Internal typed snapshot must describe this report, never another order."""
    from airport_assistant.modules.source.evidence import SourceEvidence
    if type(evidence) is not SourceEvidence:
        raise ValueError('Typed source evidence required.')
    checked=SourceEvidence.from_records(evidence.tenant_id,evidence.to_records(),expected_labels=evidence.labels)
    if checked!=evidence:
        raise ValueError('Invalid source evidence basis.')
    for record in checked.to_records():
        kind,data=record['kind'],record['data']
        if kind!='transaction' and data['service_type']!=payload.service_type:
            raise ValueError('Source service does not match the report.')
        if kind=='transaction':
            if data['reference']!=payload.transaction_reference:
                raise ValueError('Source transaction does not match the report.')
            if data.get('booking_reference') is not None and data['booking_reference']!=payload.booking_reference:
                raise ValueError('Source booking link does not match the report.')
        else:
            book=record['booking'] if kind=='policy' else data
            if book['reference']!=payload.booking_reference:
                raise ValueError('Source booking does not match the report.')
    return checked


def reviewed_summary(action_type,payload,destination,language,case_id=None,*,source_evidence=None):
    result=displayed_summary(action_type,payload,destination,language,case_id)
    if source_evidence is not None:
        result+='\n\n'+source_review(payload,source_evidence).display(language)
    if not 1<=len(result)<=7000:
        raise ValueError('Review exceeds the bounded human preview.')
    return result


def with_source_evidence(draft,evidence):
    """Server-only construction before persistence; not a public edit operation."""
    from airport_assistant.modules.source.private_content import inherited_records
    source_review(draft.payload,evidence)
    if evidence.tenant_id!=draft.tenant_id or draft.source_evidence is not None:
        raise ValueError('A source snapshot cannot replace another review.')
    labels=inherited_records(draft.tenant_id,list(draft.resource_security_labels),evidence.labels.to_records())
    return replace(draft,source_evidence=evidence,resource_security_labels=tuple(labels),
        displayed_summary=reviewed_summary(draft.action_type,draft.payload,draft.destination,draft.language,
            draft.case_id,source_evidence=evidence))


def canonical_hash(draft):
    def stamp(value):return value.astimezone(timezone.utc).isoformat(timespec='microseconds')
    record=dict(draft_id=str(draft.id),tenant_id=str(draft.tenant_id),conversation_id=str(draft.conversation_id),
                actor_principal_id=str(draft.actor_principal_id) if draft.actor_principal_id else None,
                actor_session_id=str(draft.actor_session_id) if draft.actor_session_id else None,
                created_session_id=str(draft.created_session_id),action_type=draft.action_type,
                payload=draft.payload.model_dump(mode='json'),destination=draft.destination.model_dump(mode='json'),
                displayed_summary=draft.displayed_summary,language=draft.language,version=draft.version,
                created_at=stamp(draft.created_at),expires_at=stamp(draft.expires_at),
                resource_security_labels=list(draft.resource_security_labels),case_id=str(draft.case_id) if draft.case_id else None)
    if draft.source_evidence is not None:
        from airport_assistant.modules.source.labels import SecurityLabels,merge_labels
        try:
            evidence=source_review(draft.payload,draft.source_evidence)
            labels=SecurityLabels.from_records(draft.tenant_id,list(draft.resource_security_labels))
            if (evidence.tenant_id!=draft.tenant_id
                or merge_labels(labels,evidence.labels).to_records()!=labels.to_records()
                or draft.displayed_summary!=reviewed_summary(draft.action_type,draft.payload,draft.destination,
                    draft.language,draft.case_id,source_evidence=evidence)):
                raise ValueError('Source review binding changed.')
            record['source_observations']=evidence.to_records()
        except (ValueError,TypeError,KeyError,UnicodeError):
            raise DraftError(500,'INTERNAL_ERROR') from None
    return hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def seal(draft):
    return replace(draft,payload_hash=canonical_hash(draft))


def require_integrity(draft):
    if not isinstance(draft.payload_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',draft.payload_hash) or not hmac.compare_digest(draft.payload_hash,canonical_hash(draft)):
        raise DraftError(500,'INTERNAL_ERROR')


def require_confirmation(draft,*,expected_version,payload_hash,now):
    """Pure prerequisite for CASE-02; does not accept consent or perform a commit.

    Caller must also recheck current identity, object/source access and routing.
    """
    require_integrity(draft)
    if draft.state=='expired' or draft.expires_at<=now:raise DraftError(410,'DRAFT_EXPIRED')
    if draft.state!='pending':raise DraftError(409,'DRAFT_NOT_PENDING')
    if type(expected_version) is not int or expected_version!=draft.version or not isinstance(payload_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',payload_hash) or not hmac.compare_digest(draft.payload_hash,payload_hash):
        raise DraftError(409,'VERSION_CONFLICT')
