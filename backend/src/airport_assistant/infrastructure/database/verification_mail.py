"""Only current tenant/account routing metadata is exposed to login delivery."""
from airport_assistant.infrastructure.database.auth_repository import PostgresAuthRepository


class PostgresVerificationRouting(PostgresAuthRepository):
    async def allowed(self,tenant,route):
        return await self._call('auth_mail_route_allowed',tenant,route.account_id,route.account_ref,route.sender,route.reply_to)
