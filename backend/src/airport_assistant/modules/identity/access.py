"""Request-scoped authorization from trusted identity and resource metadata.

These projections are built by backend repositories, never from request/model
ownership claims. Domain state transitions and source grants remain separate gates.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from airport_assistant.modules.identity.auth import AuthError, AuthService, Session
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.identity.membership import Membership


@dataclass(frozen=True)
class AccessIdentity:
    session: Session
    memberships: tuple[Membership, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "memberships", tuple(self.memberships))
        session = self.session
        if not isinstance(session.tenant_id, UUID) or not isinstance(session.id, UUID):
            raise ValueError("Validated session identifiers required.")
        DatabaseContext(session.tenant_id, session.principal_id, session.kind)
        if (session.kind == "staff") != bool(self.memberships):
            raise ValueError("Staff identity requires live memberships; other identities cannot carry roles.")
        if any(not isinstance(m, Membership) for m in self.memberships) or len({m.id for m in self.memberships}) != len(self.memberships):
            raise ValueError("Unambiguous membership metadata required.")

    @property
    def database_context(self) -> DatabaseContext:
        return DatabaseContext(self.session.tenant_id, self.session.principal_id, self.session.kind, self.session.id)


@dataclass(frozen=True)
class ResourceKey:
    tenant_id: UUID
    kind: str
    id: UUID

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.id, UUID) or not isinstance(self.kind, str) or not self.kind or len(self.kind) > 64:
            raise ValueError("Validated requested resource key required.")


@dataclass(frozen=True)
class OwnedObject:
    tenant_id: UUID
    id: UUID
    kind: Literal["conversation", "draft"]
    owner_principal_id: UUID | None = None
    owner_session_id: UUID | None = None
    ownership_only: bool = False

    def __post_init__(self):
        if self.kind not in {"conversation", "draft"} or not all(isinstance(x, UUID) for x in (self.tenant_id, self.id)):
            raise ValueError("Validated object metadata required.")
        if type(self.ownership_only) is not bool:
            raise ValueError("Explicit backend access classification required.")
        owners = (self.owner_principal_id, self.owner_session_id)
        if sum(x is not None for x in owners) != 1 or any(x is not None and not isinstance(x, UUID) for x in owners):
            raise ValueError("Exactly one validated owner required.")


@dataclass(frozen=True)
class StaffObject:
    tenant_id: UUID
    id: UUID
    kind: Literal["case", "membership"]
    organization_id: UUID
    queue_id: UUID | None = None
    target_principal_id: UUID | None = None

    def __post_init__(self):
        if not all(isinstance(x, UUID) for x in (self.tenant_id, self.id, self.organization_id)):
            raise ValueError("Validated object metadata required.")
        if self.kind == "case":
            valid = isinstance(self.queue_id, UUID) and self.target_principal_id is None
        elif self.kind == "membership":
            valid = self.queue_id is None and isinstance(self.target_principal_id, UUID)
        else:
            valid = False
        if not valid:
            raise ValueError("Complete, unambiguous staff object metadata required.")


class ObjectRepository(Protocol):
    async def find(self, key: ResourceKey) -> OwnedObject | StaffObject | None: ...


class MembershipReader(Protocol):
    async def memberships(self, tenant: UUID, token_hash: str) -> tuple[Membership, ...]: ...


def owned_visible(identity: AccessIdentity, target: OwnedObject) -> bool:
    session = identity.session
    if session.tenant_id != target.tenant_id or not target.ownership_only:
        return False
    if session.kind == "anonymous":
        return target.owner_session_id == session.id
    if session.kind == "customer":
        return target.owner_principal_id == session.principal_id
    return False


def staff_visible(identity: AccessIdentity, target: StaffObject) -> bool:
    session = identity.session
    if session.kind != "staff" or session.tenant_id != target.tenant_id:
        return False
    if target.kind == "case":
        return any(m.allows("read_case", target.organization_id, target.queue_id) for m in identity.memberships)
    return any(m.organization_id == target.organization_id and
               ("admin" in m.roles or (m.id == target.id and session.principal_id == target.target_principal_id))
               for m in identity.memberships)


def object_allows(identity: AccessIdentity, target: OwnedObject | StaffObject, action: str) -> bool:
    if isinstance(target, OwnedObject):
        actions = {"conversation": {"read_conversation", "append_message"},
                   "draft": {"read_draft", "edit_draft", "confirm_draft", "cancel_draft"}}
        return owned_visible(identity, target) and action in actions[target.kind]
    if not staff_visible(identity, target):
        return False
    if target.kind == "case":
        return action in {"read_case", "reply_case", "assign_case"} and any(
            m.allows(action, target.organization_id, target.queue_id) for m in identity.memberships)
    if action == "read_membership":
        return True
    return action == "manage_memberships" and identity.session.principal_id != target.target_principal_id and any(
        m.allows(action, target.organization_id) for m in identity.memberships)


class AccessService:
    def __init__(self, auth: AuthService, memberships: MembershipReader | None = None):
        self._auth = auth
        self._memberships = memberships

    async def resolve(self, tenant: UUID, token: str | None, *, mutation=False, csrf: str | None = None) -> AccessIdentity:
        session = await self._auth.current(tenant, token)
        if session is None or session.tenant_id != tenant:
            raise AuthError(401, "AUTH_REQUIRED")
        memberships = ()
        if session.kind == "staff":
            if self._memberships is None:
                raise AuthError(503, "AUTH_UNAVAILABLE")
            # This DB operation rechecks session/credential eligibility as well.
            memberships = await self._memberships.memberships(tenant, self._auth.digest("session", tenant, token))
            if not memberships:
                raise AuthError(401, "AUTH_REQUIRED")
        identity = AccessIdentity(session, memberships)
        if mutation:
            self._auth.validate_csrf(session, csrf)
        return identity

    async def staff_identity(self, tenant: UUID, token: str | None) -> AccessIdentity:
        identity = await self.resolve(tenant, token)
        if identity.session.kind != "staff":
            raise AuthError(401, "AUTH_REQUIRED")
        return identity

    @staticmethod
    def require_object(identity: AccessIdentity, key: ResourceKey, target: OwnedObject | StaffObject | None, action: str):
        if key.tenant_id != identity.session.tenant_id or not isinstance(target, (OwnedObject, StaffObject)) or (target.tenant_id, target.kind, target.id) != (key.tenant_id, key.kind, key.id):
            raise AuthError(404, "RESOURCE_NOT_FOUND")
        visible = (owned_visible(identity, target) if isinstance(target, OwnedObject)
                   else staff_visible(identity, target) if isinstance(target, StaffObject) else False)
        if not visible:
            raise AuthError(404, "RESOURCE_NOT_FOUND")
        if not object_allows(identity, target, action):
            raise AuthError(403, "ACTION_FORBIDDEN")
        return target

    async def require_resource(self, tenant: UUID, token: str | None, key: ResourceKey,
                               repository: ObjectRepository, action: str, *, mutation=False, csrf: str | None = None):
        mutating_actions = {"append_message", "edit_draft", "confirm_draft", "cancel_draft", "reply_case", "assign_case", "manage_memberships"}
        identity = await self.resolve(tenant, token, mutation=mutation or action in mutating_actions, csrf=csrf)
        if key.tenant_id != identity.session.tenant_id or key.kind not in {"conversation", "draft", "case", "membership"}:
            raise AuthError(404, "RESOURCE_NOT_FOUND")
        target = await repository.find(key)
        return self.require_object(identity, key, target, action)

    async def require_staff_scope(self, tenant: UUID, token: str | None, action: str, organization_id: UUID, queue_id: UUID | None = None):
        identity = await self.staff_identity(tenant, token)
        # This legacy scope guard has no object lookup or existence information.
        if not any(m.allows(action, organization_id, queue_id) for m in identity.memberships):
            raise AuthError(403, "ACTION_FORBIDDEN")
        return identity.session
