"""Provisioned staff login and membership-local permissions."""

from dataclasses import dataclass, field
import secrets
from typing import Protocol
from uuid import UUID, uuid4

from airport_assistant.modules.identity.auth import AuthError, AuthService, SessionCredentials, normalized_contact


@dataclass(frozen=True)
class StaffCredential:
    principal_id: UUID
    version: UUID
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class Membership:
    id: UUID
    organization_id: UUID
    version: int
    roles: frozenset[str]
    queue_ids: frozenset[UUID]

    def allows(self, action: str, organization_id: UUID, queue_id: UUID | None = None) -> bool:
        if self.organization_id != organization_id:
            return False
        if action == "manage_memberships":
            return "admin" in self.roles
        if action in {"read_case", "reply_case"}:
            return bool(self.roles & {"cs", "supervisor"}) and queue_id is not None and queue_id in self.queue_ids
        if action == "assign_case":
            return "supervisor" in self.roles and queue_id is not None and queue_id in self.queue_ids
        return False


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
    def __init__(self, auth: AuthService, repository: StaffRepository, passwords: PasswordVerifier, policy: StaffPolicy = StaffPolicy()):
        self._auth = auth
        self._repository = repository
        self._passwords = passwords
        self._policy = policy

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
        session = await self._auth.current(tenant, token)
        if session is None or session.kind != "staff":
            raise AuthError(401, "AUTH_REQUIRED")
        memberships = await self._repository.memberships(tenant, self._auth.digest("session", tenant, token))
        if not memberships:
            raise AuthError(401, "AUTH_REQUIRED")
        return session, memberships

    async def require(self, tenant: UUID, token: str | None, action: str, organization_id: UUID, queue_id: UUID | None = None):
        session, memberships = await self.current(tenant, token)
        if not any(m.allows(action, organization_id, queue_id) for m in memberships):
            raise AuthError(403, "ACTION_FORBIDDEN")
        return session
