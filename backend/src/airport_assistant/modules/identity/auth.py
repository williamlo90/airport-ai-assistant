"""Customer authentication use cases; no framework, database or provider SDK."""

from dataclasses import dataclass,field
from datetime import datetime
import asyncio
import hashlib
import hmac
import re
import secrets
from typing import Protocol
from uuid import UUID,uuid4

from email_validator import validate_email,EmailNotValidError


class AuthError(Exception):
    def __init__(self,status:int,code:str,retry_after:int|None=None):
        super().__init__(code)
        self.status=status
        self.code=code
        self.retry_after=retry_after


@dataclass(frozen=True)
class Session:
    id:UUID
    tenant_id:UUID
    principal_id:UUID|None
    kind:str
    expires_at:datetime
    csrf_hash:str=field(repr=False)


@dataclass(frozen=True)
class SessionCredentials:
    session:Session
    token:str=field(repr=False)
    csrf:str=field(repr=False)


@dataclass(frozen=True)
class VerificationMessage:
    challenge_id:UUID
    tenant_id:UUID
    recipient:str=field(repr=False)
    code:str=field(repr=False)
    expires_seconds:int=300


class VerificationSender(Protocol):
    async def send(self,message:VerificationMessage)->None:...


class AuthRepository(Protocol):
    async def tenant_for_host(self,host:str)->UUID|None:...
    async def session(self,tenant:UUID,token_hash:str)->Session|None:...
    async def create_anonymous(self,tenant:UUID,id:UUID,token_hash:str,csrf_hash:str,ip_hash:str,ttl:int,window:int,limit:int)->Session|None:...
    async def issue(self,tenant:UUID,token_hash:str,csrf_hash:str,id:UUID,contact:str,contact_hash:str,code_hash:str,ip_hash:str,ttl:int,max_attempts:int,window:int,contact_limit:int,ip_limit:int,session_limit:int)->str:...
    async def cancel(self,tenant:UUID,id:UUID,token_hash:str)->None:...
    async def verify(self,tenant:UUID,id:UUID,token_hash:str,csrf_hash:str,code_hash:str,new_id:UUID,new_token_hash:str,new_csrf_hash:str,ttl:int,ip_hash:str,window:int,ip_limit:int,session_limit:int)->tuple[str,Session|None]:...
    async def revoke(self,tenant:UUID,token_hash:str,csrf_hash:str)->bool:...


@dataclass(frozen=True)
class AuthPolicy:
    anonymous_ttl:int=86400
    customer_ttl:int=28800
    challenge_ttl:int=300
    max_attempts:int=5
    rate_window:int=900
    boot_limit:int=100
    contact_limit:int=3
    ip_limit:int=100
    session_limit:int=5
    verify_ip_limit:int=200
    verify_session_limit:int=15
    delivery_timeout:float=10


def normalized_contact(value:str)->str:
    try:
        return validate_email(value,check_deliverability=False,allow_smtputf8=False).ascii_email
    except EmailNotValidError:
        raise AuthError(422,"VALIDATION_ERROR") from None


class AuthService:
    def __init__(self,repository:AuthRepository,key:bytes,policy:AuthPolicy=AuthPolicy(),sender:VerificationSender|None=None):
        if len(key)<32:raise ValueError("Authentication key must contain at least 32 bytes.")
        self._repository=repository;self._key=key;self._policy=policy;self._sender=sender

    def digest(self,tag:str,tenant:UUID,value:str)->str:
        return hmac.new(self._key,f"{tag}:{tenant}:{value}".encode(),hashlib.sha256).hexdigest()

    async def tenant(self,host:str)->UUID:
        canonical=host.lower().rstrip(".")
        if len(canonical)>253 or not re.fullmatch(r"[a-z0-9.-]+",canonical):raise AuthError(404,"RESOURCE_NOT_FOUND")
        tenant=await self._repository.tenant_for_host(canonical)
        if tenant is None:raise AuthError(404,"RESOURCE_NOT_FOUND")
        return tenant

    async def current(self,tenant:UUID,token:str|None)->Session|None:
        if token is None or not re.fullmatch(r"[A-Za-z0-9_-]{43}",token):return None
        return await self._repository.session(tenant,self.digest("session",tenant,token))

    async def bootstrap(self,tenant:UUID,token:str|None,ip:str)->SessionCredentials|Session:
        current=await self.current(tenant,token)
        if current:return current
        raw,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        session=await self._repository.create_anonymous(tenant,uuid4(),self.digest("session",tenant,raw),self.digest("csrf",tenant,csrf),self.digest("ip",tenant,ip),self._policy.anonymous_ttl,self._policy.rate_window,self._policy.boot_limit)
        if session is None:raise AuthError(429,"RATE_LIMITED",self._policy.rate_window)
        return SessionCredentials(session,raw,csrf)

    async def _bound(self,tenant:UUID,token:str|None,csrf:str|None,anonymous=False)->Session:
        session=await self.current(tenant,token)
        if session is None or (anonymous and session.kind!="anonymous"):raise AuthError(401,"AUTH_REQUIRED")
        self.validate_csrf(session,csrf)
        return session

    def validate_csrf(self,session:Session,csrf:str|None)->None:
        if csrf is None or len(csrf)!=43 or not hmac.compare_digest(self.digest("csrf",session.tenant_id,csrf),session.csrf_hash):raise AuthError(403,"CSRF_REQUIRED")

    async def request_challenge(self,tenant:UUID,token:str|None,csrf:str|None,contact:str,ip:str)->UUID:
        await self._bound(tenant,token,csrf,anonymous=True)
        if self._sender is None:raise AuthError(503,"VERIFICATION_UNAVAILABLE")
        address=normalized_contact(contact)
        id=uuid4();code=f"{secrets.randbelow(100_000_000):08d}"
        p=self._policy
        status=await self._repository.issue(tenant,self.digest("session",tenant,token),self.digest("csrf",tenant,csrf),id,address,self.digest("contact",tenant,address),self.digest("otp",tenant,f"{id}:{code}"),self.digest("ip",tenant,ip),p.challenge_ttl,p.max_attempts,p.rate_window,p.contact_limit,p.ip_limit,p.session_limit)
        self._check_status(status)
        try:
            async with asyncio.timeout(p.delivery_timeout):
                await self._sender.send(VerificationMessage(id,tenant,address,code,p.challenge_ttl))
        except asyncio.CancelledError:
            await self._repository.cancel(tenant,id,self.digest("session",tenant,token))
            raise
        except Exception:
            await self._repository.cancel(tenant,id,self.digest("session",tenant,token))
            raise AuthError(503,"VERIFICATION_UNAVAILABLE") from None
        return id

    async def verify_challenge(self,tenant:UUID,id:UUID,token:str|None,csrf:str|None,code:str,ip:str)->SessionCredentials:
        await self._bound(tenant,token,csrf,anonymous=True)
        if not re.fullmatch(r"[0-9]{8}",code):raise AuthError(422,"VALIDATION_ERROR")
        raw,new_csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        p=self._policy
        status,session=await self._repository.verify(tenant,id,self.digest("session",tenant,token),self.digest("csrf",tenant,csrf),self.digest("otp",tenant,f"{id}:{code}"),uuid4(),self.digest("session",tenant,raw),self.digest("csrf",tenant,new_csrf),p.customer_ttl,self.digest("ip",tenant,ip),p.rate_window,p.verify_ip_limit,p.verify_session_limit)
        self._check_status(status)
        if session is None:raise AuthError(422,"VERIFICATION_FAILED")
        return SessionCredentials(session,raw,new_csrf)

    async def logout(self,tenant:UUID,token:str|None,csrf:str|None)->None:
        await self._bound(tenant,token,csrf)
        if not await self._repository.revoke(tenant,self.digest("session",tenant,token),self.digest("csrf",tenant,csrf)):raise AuthError(401,"AUTH_REQUIRED")

    def _check_status(self,status:str):
        if status=="ok":return
        if status=="rate_limited":raise AuthError(429,"RATE_LIMITED",self._policy.rate_window)
        if status=="csrf_failed":raise AuthError(403,"CSRF_REQUIRED")
        if status=="invalid_session":raise AuthError(401,"AUTH_REQUIRED")
        raise AuthError(422,"VERIFICATION_FAILED")
