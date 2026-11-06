"""Short-lived backend assertions bound to method, raw path, and query."""

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from uuid import UUID,uuid4
from typing import Literal
from pydantic import Field
from airport_reference.models import Record


class SourceError(Exception):
    def __init__(self,status:int,code:str):self.status=status;self.code=code;super().__init__(code)


class Claims(Record):
    v:int=Field(ge=1,le=1)
    aud:Literal["airport-reference-v1"]
    tenant_id:UUID
    subject:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    iat:int
    exp:int
    nonce:UUID


@dataclass(frozen=True)
class Caller:
    tenant_id:str
    subject:str


def encode(value:bytes)->str:return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value:str)->bytes:
    raw=base64.b64decode(value+"="*((-len(value))%4),altchars=b"-_",validate=True)
    if encode(raw)!=value:raise ValueError("Noncanonical encoding.")
    return raw


def signature(secret:str,method:str,path:bytes,query:bytes,context:str)->str:
    message=b"source-v1\n"+method.encode("ascii")+b"\n"+path+b"\n"+query+b"\n"+context.encode("ascii")
    return hmac.new(secret.encode(),message,hashlib.sha256).hexdigest()


def signed_headers(token:str,secret:str,tenant:UUID,subject:str,*,path:str,query:bytes=b"",now:int|None=None,ttl=60):
    now=int(time.time()) if now is None else now
    claims=Claims(v=1,aud="airport-reference-v1",tenant_id=tenant,subject=subject,iat=now,exp=now+ttl,nonce=uuid4())
    context=encode(claims.model_dump_json().encode())
    return {"Authorization":"Bearer "+token,"X-Source-Context":context,
            "X-Source-Signature":signature(secret,"GET",path.encode("ascii"),query,context)}


def authenticate(headers,method,path,query,token,secret,*,now=None):
    try:
        def single(name):
            values=headers.getlist(name)
            if len(values)!=1:raise ValueError("Missing or duplicate proof.")
            return values[0]
        bearer=single("authorization")
        if len(bearer)>300 or not hmac.compare_digest(bearer.encode(),("Bearer "+token).encode()):raise ValueError("Invalid service proof.")
        context=single("x-source-context");supplied=single("x-source-signature")
        if len(context)>2048 or len(supplied)!=64:raise ValueError("Oversized proof.")
        expected=signature(secret,method,path,query,context)
        if not hmac.compare_digest(supplied,expected):raise ValueError("Invalid context proof.")
        raw=decode(context)
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value:raise ValueError("Duplicate claim.")
                value[key]=item
            return value
        json.loads(raw,object_pairs_hook=unique)
        claims=Claims.model_validate_json(raw)
        now=int(time.time()) if now is None else now
        if claims.iat>now+5 or claims.exp<=now or not 1<=claims.exp-claims.iat<=60:raise ValueError("Expired context.")
        return Caller(str(claims.tenant_id),claims.subject)
    except Exception:
        raise SourceError(401,"AUTH_REQUIRED") from None


def cursor_encode(secret:str,binding:dict,last:str,now:int|None=None):
    value=encode(json.dumps(dict(binding=binding,last=last,exp=(int(time.time()) if now is None else now)+900),sort_keys=True,separators=(",",":")).encode())
    digest=hmac.new(secret.encode(),b"cursor-v1:"+value.encode(),hashlib.sha256).hexdigest()
    return value+"."+digest


def cursor_decode(secret:str,binding:dict,value:str,now:int|None=None):
    try:
        if len(value)>2048:raise ValueError()
        body,digest=value.split(".")
        expected=hmac.new(secret.encode(),b"cursor-v1:"+body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest,expected):raise ValueError()
        payload=json.loads(decode(body))
        if payload["binding"]!=binding or type(payload["exp"]) is not int or payload["exp"]<=(int(time.time()) if now is None else now) or not isinstance(payload["last"],str):raise ValueError()
        return payload["last"]
    except Exception:raise SourceError(400,"BAD_REQUEST") from None
