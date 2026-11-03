"""Validated user reports, server destinations, and confirmation-bound previews."""

from dataclasses import dataclass,replace
from datetime import datetime,timezone
import hashlib
import hmac
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel,ConfigDict,Field,field_validator
from airport_assistant.modules.identity.auth import normalized_contact
from airport_assistant.modules.identity.access import OwnedObject


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
    attachment_ids:tuple[UUID,...]=Field(default=(),max_length=0,strict=False)

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
    payload:ReportPayload
    destination:Destination
    displayed_summary:str
    language:str
    version:int
    payload_hash:str
    created_at:datetime
    expires_at:datetime
    state:str='pending'
    case_id:UUID|None=None
    resource_security_labels:tuple=()
    result_case_id:UUID|None=None
    result_message_id:UUID|None=None
    result_ref:str|None=None
    committed_at:datetime|None=None

    def projection(self):
        return OwnedObject(self.tenant_id,self.id,'draft',self.actor_principal_id,self.actor_session_id,
                           self.case_id is None and not self.resource_security_labels)


def displayed_summary(action_type,payload,destination,language):
    if language=='id':
        labels=('Buat pengajuan' if action_type=='create_case' else 'Minta bantuan petugas',
                'Kategori','Layanan','Ringkasan','Laporan pelanggan','Referensi booking','Referensi transaksi','Email','Tujuan','Belum diisi')
    else:
        labels=('Create support request' if action_type=='create_case' else 'Request staff assistance',
                'Category','Service','Summary','Customer report','Booking reference','Transaction reference','Email','Destination','Not provided')
    values=(payload.category,payload.service_type,payload.summary,payload.customer_report,
            payload.booking_reference or labels[-1],payload.transaction_reference or labels[-1],
            payload.contact_email or labels[-1],destination.organization_name+' / '+destination.queue_name)
    return labels[0]+'\n'+'\n'.join(label+': '+value for label,value in zip(labels[1:-1],values))


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
