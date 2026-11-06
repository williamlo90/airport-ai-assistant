"""Reply capability adapter; no browser identity or raw body SQL entry point."""
from airport_assistant.infrastructure.database.mail import PostgresMail
from psycopg.types.json import Jsonb


class PostgresReplies(PostgresMail):
    async def private_candidate(self,lease):
        # Future read-only capability; never use side-effectful legacy snapshot.
        return await self._call('email_private_reply_candidate',lease)

    async def private_apply(self,lease,expected,command,*,before_commit,result_check):
        return await self._call('email_private_reply_apply',lease,Jsonb(expected),Jsonb(command),
            before_commit=before_commit,result_check=result_check)

    async def snapshot(self,lease):return await self._call('email_reply_snapshot',lease)
    async def apply(self,lease,action,body,id,hash):return await self._call('email_reply_apply',lease,action,body,id,hash)
    async def challenge(self,lease):return await self._call('email_challenge_snapshot',lease)
    async def reserve(self,lease):return await self._call('email_challenge_reserve',lease)
    async def sent(self,lease,result):return await self._call('email_challenge_sent',lease,result.state,result.provider_message_id)
