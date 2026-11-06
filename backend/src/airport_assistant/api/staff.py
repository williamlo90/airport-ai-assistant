"""Separate staff login; caller cannot supply identity, roles or memberships."""

from uuid import UUID
from fastapi import APIRouter, Request, Response, Depends
from pydantic import BaseModel, Field, SecretStr

from airport_assistant.api.auth import context, cookie, csrf_header, ip, public_session, set_credentials, SessionView, SESSION_COOKIE
from airport_assistant.api.schemas import RequestModel, ErrorResponse
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.api.dependencies import get_staff_identity

router = APIRouter(prefix="/api/v1/staff", tags=["staff identity"], responses={s: {"model": ErrorResponse} for s in (401, 403, 429, 503)})


class LoginRequest(RequestModel):
    contact: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128)


class MembershipView(BaseModel):
    id: UUID
    organization_id: UUID
    version: int
    roles: list[str]
    queue_ids: list[UUID]


class StaffView(BaseModel):
    session_kind: str
    principal_id: UUID
    memberships: list[MembershipView]


@router.post("/auth/login", response_model=SessionView)
async def login(body: LoginRequest, request: Request, response: Response):
    _, tenant = await context(request, mutation=True)
    service = request.app.state.services.staff
    if service is None:
        raise AuthError(503, "AUTH_UNAVAILABLE")
    credentials = await service.login(tenant, cookie(request, SESSION_COOKIE), csrf_header(request),
                                      body.contact, body.password.get_secret_value(), ip(request))
    set_credentials(response, credentials)
    response.headers["Cache-Control"] = "no-store"
    return public_session(credentials.session)


@router.get("/me", response_model=StaffView)
async def me(response: Response, identity: AccessIdentity = Depends(get_staff_identity)):
    response.headers["Cache-Control"] = "no-store"
    return {**public_session(identity.session), "memberships": [dict(id=m.id, organization_id=m.organization_id,
        version=m.version, roles=sorted(m.roles), queue_ids=sorted(m.queue_ids)) for m in identity.memberships]}
