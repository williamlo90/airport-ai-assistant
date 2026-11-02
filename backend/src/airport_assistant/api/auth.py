"""HTTPS/same-origin customer auth adapters; tokens stay in host-only cookies."""

from datetime import datetime,timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter,Request,Response
from pydantic import BaseModel,Field
from starlette.datastructures import URL

from airport_assistant.api.schemas import RequestModel,ErrorResponse
from airport_assistant.modules.identity.auth import AuthError,AuthService,Session,SessionCredentials

SESSION_COOKIE="__Host-airport_session"
CSRF_COOKIE="__Host-airport_csrf"
router=APIRouter(prefix="/api/v1",tags=["customer identity"],responses={status:{"model":ErrorResponse} for status in (401,403,429,503)})


class ChallengeRequest(RequestModel):
    contact:str=Field(min_length=3,max_length=320)
    purpose:Literal["customer_login"]


class VerifyRequest(RequestModel):
    code:str=Field(pattern=r"^[0-9]{8}$")


class EmptyRequest(RequestModel):
    pass


class SessionView(BaseModel):
    session_kind:Literal["anonymous","customer"]
    principal_id:UUID|None


class ChallengeAccepted(BaseModel):
    challenge_id:UUID
    status:Literal["verification_requested"]


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


def set_credentials(response:Response,credentials:SessionCredentials):
    age=max(0,int((credentials.session.expires_at-datetime.now(timezone.utc)).total_seconds()))
    for name,value,http_only in ((SESSION_COOKIE,credentials.token,True),(CSRF_COOKIE,credentials.csrf,False)):
        response.set_cookie(name,value,max_age=age,expires=credentials.session.expires_at.astimezone(timezone.utc),path="/",secure=True,httponly=http_only,samesite="lax")


def public_session(session:Session):
    return {"session_kind":session.kind,"principal_id":str(session.principal_id) if session.principal_id else None}


@router.post("/auth/sessions",status_code=200,response_model=SessionView)
async def bootstrap(request:Request,response:Response,body:EmptyRequest|None=None):
    service,tenant=await context(request,mutation=True)
    result=await service.bootstrap(tenant,cookie(request,SESSION_COOKIE),ip(request))
    response.headers["Cache-Control"]="no-store"
    if isinstance(result,SessionCredentials):
        set_credentials(response,result);return public_session(result.session)
    service.validate_csrf(result,cookie(request,CSRF_COOKIE))
    return public_session(result)


@router.post("/auth/challenges",status_code=202,response_model=ChallengeAccepted)
async def challenge(body:ChallengeRequest,request:Request,response:Response):
    service,tenant=await context(request,mutation=True)
    id=await service.request_challenge(tenant,cookie(request,SESSION_COOKIE),csrf_header(request),body.contact,ip(request))
    response.headers["Cache-Control"]="no-store"
    return {"challenge_id":str(id),"status":"verification_requested"}


@router.post("/auth/challenges/{id}/verify",response_model=SessionView)
async def verify(id:UUID,body:VerifyRequest,request:Request,response:Response):
    service,tenant=await context(request,mutation=True)
    result=await service.verify_challenge(tenant,id,cookie(request,SESSION_COOKIE),csrf_header(request),body.code,ip(request))
    set_credentials(response,result);response.headers["Cache-Control"]="no-store"
    return public_session(result.session)


@router.get("/me",response_model=SessionView)
async def me(request:Request,response:Response):
    service,tenant=await context(request)
    session=await service.current(tenant,cookie(request,SESSION_COOKIE))
    if session is None:raise AuthError(401,"AUTH_REQUIRED")
    response.headers["Cache-Control"]="no-store"
    return public_session(session)


@router.post("/auth/logout",status_code=204)
async def logout(request:Request,response:Response,body:EmptyRequest|None=None):
    service,tenant=await context(request,mutation=True)
    await service.logout(tenant,cookie(request,SESSION_COOKIE),csrf_header(request))
    response.headers["Cache-Control"]="no-store"
    for name,http_only in ((SESSION_COOKIE,True),(CSRF_COOKIE,False)):
        response.delete_cookie(name,path="/",secure=True,httponly=http_only,samesite="lax")
