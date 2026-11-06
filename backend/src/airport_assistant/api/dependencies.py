"""HTTP adapters retrieve services; they never construct database/provider clients."""

from fastapi import Request, Depends

from airport_assistant.api.security import context, cookie, csrf_header, SESSION_COOKIE
from airport_assistant.modules.identity.access import AccessIdentity, AccessService
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.system.health import HealthService


def get_health_service(request: Request) -> HealthService:
    return request.app.state.services.health


def get_access_service(request: Request) -> AccessService:
    service = request.app.state.services.access
    if service is None:
        raise AuthError(503, "AUTH_UNAVAILABLE")
    return service


async def get_identity(request: Request) -> AccessIdentity:
    _, tenant = await context(request)
    return await get_access_service(request).resolve(tenant, cookie(request, SESSION_COOKIE))


async def get_mutation_identity(request: Request) -> AccessIdentity:
    _, tenant = await context(request, mutation=True)
    return await get_access_service(request).resolve(tenant, cookie(request, SESSION_COOKIE),
                                                      mutation=True, csrf=csrf_header(request))


async def get_staff_identity(identity: AccessIdentity = Depends(get_identity)) -> AccessIdentity:
    if identity.session.kind != "staff":
        raise AuthError(401, "AUTH_REQUIRED")
    return identity


async def get_customer_identity(identity:AccessIdentity=Depends(get_identity))->AccessIdentity:
    if identity.session.kind!='customer':raise AuthError(401,'AUTH_REQUIRED')
    return identity
