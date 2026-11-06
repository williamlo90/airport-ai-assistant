"""Narrow staff auth functions; hashes never go into HTTP DTOs."""

from uuid import UUID
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository, session_from_json
from airport_assistant.modules.identity.staff import StaffCredential, Membership


class PostgresStaffRepository(PostgresAuthRepository):
    async def begin(self, *args):
        value = await self._call("auth_staff_begin", *args)
        c = value.get("credential")
        return value["status"], StaffCredential(UUID(c["principal_id"]), UUID(c["version"]), c["password_hash"]) if c else None

    async def finish(self, *args):
        value = await self._call("auth_staff_finish", *args)
        return value["status"], session_from_json(value.get("session"))

    async def memberships(self, *args):
        rows = await self._call("auth_staff_memberships", *args)
        return tuple(Membership(UUID(r["id"]), UUID(r["organization_id"]), r["version"],
                                frozenset(r["roles"]), frozenset(UUID(q) for q in r["queue_ids"])) for r in rows)
