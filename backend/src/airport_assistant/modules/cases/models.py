"""Case confirmation protocol and stable public receipts."""

import hashlib
import json
import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field
from airport_assistant.modules.drafts.models import DraftError


class CommitResult(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    state:Literal['committed']='committed'
    case_id:UUID
    result_ref:str=Field(pattern=r'^CASE-[a-f0-9]{32}$')
    message_id:UUID


class CaseDeadlineLost(Exception):pass


def validate_request(expected_version,payload_hash,key):
    if type(expected_version) is not int or expected_version<1 or not isinstance(payload_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',payload_hash):
        raise DraftError(422,'VALIDATION_ERROR')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{15,127}',key):
        raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')


def request_hash(draft_id,expected_version,payload_hash):
    value=dict(draft_id=str(draft_id),expected_version=expected_version,payload_hash=payload_hash)
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
