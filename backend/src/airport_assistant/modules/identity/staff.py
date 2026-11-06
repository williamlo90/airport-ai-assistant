"""Provisioned staff login and membership-local permissions."""

from dataclasses import dataclass, field
import secrets
from typing import Protocol
from uuid import UUID, uuid4

from airport_assistant.modules.identity.auth import AuthError, AuthService, SessionCredentials, normalized_contact
from airport_assistant.modules.identity.membership import Membership
from airport_assistant.modules.identity.access import AccessService


@dataclass(frozen=True)
class StaffCredential:
    principal_id: UUID
    version: UUID
    password_hash: str = field(repr=False)



class PasswordVerifier(Protocol):
    async def verify(self, password: str, encoded: str | None) -> bool: ...


class StaffRepository(Protocol):
    async def begin(self, *args) -> tuple[str, StaffCredential | None]: ...
    async def finish(self, *args): ...
    async def memberships(self, tenant: UUID, token_hash: str) -> tuple[Membership, ...]: ...


@dataclass(frozen=True)
class StaffPolicy:
    ttl: int = 28800
    ip_limit: int = 100
    contact_limit: int = 10
    session_limit: int = 10


class StaffService:
    def __init__(self, auth: AuthService, repository: StaffRepository, passwords: PasswordVerifier, access: AccessService, policy: StaffPolicy = StaffPolicy()):
        self._auth = auth
        self._repository = repository
        self._passwords = passwords
        self._policy = policy
        self._access = access

    async def login(self, tenant: UUID, token: str | None, csrf: str | None, contact: str, password: str, ip: str) -> SessionCredentials:
        await self._auth._bound(tenant, token, csrf, anonymous=True)
        try:
            address = normalized_contact(contact)
        except AuthError:
            address = "invalid-address"  # Same KDF and rate path; no account enumeration.
        p = self._policy
        digest = self._auth.digest
        status, credential = await self._repository.begin(
            tenant, digest("session", tenant, token), digest("csrf", tenant, csrf), address,
            digest("staff_contact", tenant, address), digest("ip", tenant, ip),
            self._auth._policy.rate_window, p.ip_limit, p.contact_limit, p.session_limit)
        self._auth._check_status(status)
        valid = await self._passwords.verify(password, credential.password_hash if credential else None)
        if not valid or credential is None:
            raise AuthError(401, "STAFF_LOGIN_FAILED")
        raw, new_csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        status, session = await self._repository.finish(
            tenant, digest("session", tenant, token), digest("csrf", tenant, csrf),
            credential.principal_id, credential.version, uuid4(), digest("session", tenant, raw),
            digest("csrf", tenant, new_csrf), p.ttl)
        if status in {"invalid", "invalid_session"}:
            raise AuthError(401, "STAFF_LOGIN_FAILED")
        self._auth._check_status(status)
        if session is None:
            raise AuthError(401, "STAFF_LOGIN_FAILED")
        return SessionCredentials(session, raw, new_csrf)

    async def current(self, tenant: UUID, token: str | None):
        identity = await self._access.staff_identity(tenant, token)
        return identity.session, identity.memberships

    async def require(self, tenant: UUID, token: str | None, action: str, organization_id: UUID, queue_id: UUID | None = None):
        return await self._access.require_staff_scope(tenant, token, action, organization_id, queue_id)
