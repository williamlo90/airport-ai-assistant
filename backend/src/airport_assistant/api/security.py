"""Shared HTTPS/host/origin/cookie proof before identity and business access."""

from uuid import UUID
from fastapi import Request
from starlette.datastructures import URL
from airport_assistant.modules.identity.auth import AuthService, AuthError

SESSION_COOKIE="__Host-airport_session"
CSRF_COOKIE="__Host-airport_csrf"

def cookie(request:Request,name:str)->str|None:
    values=[]
    for header in request.headers.getlist("cookie"):
        for item in header.split(";"):
            key,separator,value=item.strip().partition("=")
            if separator and key==name:values.append(value)
    if len(values)>1:raise AuthError(403,"CSRF_REQUIRED")
    return values[0] if values else None


async def context(request:Request,mutation=False)->tuple[AuthService,UUID]:
    service=request.app.state.services.auth
    if service is None:raise AuthError(503,"AUTH_UNAVAILABLE")
    if request.url.scheme!="https" or len(request.headers.getlist("host"))!=1:raise AuthError(403,"CSRF_REQUIRED")
    if mutation:
        origins=request.headers.getlist("origin")
        if len(origins)!=1:raise AuthError(403,"CSRF_REQUIRED")
        try:
            origin=URL(origins[0])
            valid=(origin.scheme=="https" and origin.username is None and origin.password is None
                   and origin.hostname==request.url.hostname
                   and (origin.port if origin.port is not None else 443)==(request.url.port if request.url.port is not None else 443)
                   and origin.path in {"","/"} and not origin.query and not origin.fragment)
        except ValueError:valid=False
        if not valid:raise AuthError(403,"CSRF_REQUIRED")
    # Validate duplicate CSRF cookies even though the server-bound header is the proof.
    cookie(request,CSRF_COOKIE)
    return service,await service.tenant(request.url.hostname or "")


def csrf_header(request:Request)->str|None:
    values=request.headers.getlist("x-csrf-token")
    if len(values)>1:raise AuthError(403,"CSRF_REQUIRED")
    return values[0] if values else None


def ip(request:Request)->str:
    # Forwarded headers are not parsed here; ASGI server/proxy trust is configured separately.
    return request.client.host if request.client else "unknown"
