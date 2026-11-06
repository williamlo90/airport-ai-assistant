"""Bounded human-review token; signed, not encrypted, never a URL parameter."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from airport_assistant.modules.drafts.models import DraftError


class InformationInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    expected_version:int=Field(ge=1)
    message:str=Field(min_length=1,max_length=4000,repr=False)
    language:Literal['en','id']

    @field_validator('message')
    @classmethod
    def meaningful(cls,value):
        if not value.strip() or '\x00' in value:
            raise ValueError('Nonblank message required.')
        try:value.encode('utf-8')
        except UnicodeError:raise ValueError('Valid Unicode required.') from None
        return value


class PreviewRecord(InformationInput):
    preview_id:UUID=Field(strict=False)
    tenant_id:UUID=Field(strict=False)
    principal_id:UUID=Field(strict=False)
    session_id:UUID=Field(strict=False)
    case_id:UUID=Field(strict=False)
    expires_at:int=Field(ge=1)


class InformationConfirmation(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    confirmation_token:str=Field(min_length=1,max_length=32768,repr=False)
    confirmed:bool

    @field_validator('confirmed')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('Explicit confirmation required.')
        return value


class InformationPreviewCodec:
    """One nonce per review; durable ledger must consume preview_id only once."""
    def __init__(self,key,*,clock=None):
        if len(key)<32:raise ValueError('Preview signing key required.')
        self._key=hmac.digest(key,b'customer-case-information-preview-v1','sha256')
        self._clock=clock or (lambda:datetime.now(timezone.utc).timestamp())

    def issue(self,identity,case_id,command):
        if identity.session.kind!='customer' or type(command) is not InformationInput:
            raise DraftError(401,'AUTH_REQUIRED')
        s=identity.session
        record=PreviewRecord(**command.model_dump(),preview_id=uuid4(),tenant_id=s.tenant_id,
            principal_id=s.principal_id,session_id=s.id,case_id=case_id,expires_at=int(self._clock())+900)
        raw=json.dumps(record.model_dump(mode='json'),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
        token=base64.urlsafe_b64encode(raw+hmac.digest(self._key,raw,'sha256')).decode().rstrip('=')
        if len(token)>32768:raise DraftError(422,'VALIDATION_ERROR')
        return record,token

    def read(self,identity,case_id,token,*,allow_expired=False):
        try:
            if identity.session.kind!='customer' or type(token) is not str or not 1<=len(token)<=32768:
                raise ValueError()
            data=base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True)
            raw,signature=data[:-32],data[-32:]
            if not hmac.compare_digest(signature,hmac.digest(self._key,raw,'sha256')):raise ValueError()
            record=PreviewRecord.model_validate_json(raw)
            s=identity.session
            if (record.tenant_id,record.principal_id,record.session_id,record.case_id)!=(s.tenant_id,s.principal_id,s.id,case_id):
                raise ValueError()
            if record.expires_at>self._clock()+900:raise ValueError()
        except (ValueError,TypeError,UnicodeError):
            raise DraftError(400,'BAD_REQUEST') from None
        if not allow_expired and record.expires_at<=self._clock():raise DraftError(410,'PREVIEW_EXPIRED')
        return record

    @staticmethod
    def digest(record):
        return hashlib.sha256(record.model_dump_json().encode('utf-8')).hexdigest()
