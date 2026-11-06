"""Fenced persistence only; recipient decisions are made by OutboundMail."""
from dataclasses import asdict
from uuid import UUID
from psycopg import sql
from psycopg.types.json import Jsonb
from airport_assistant.infrastructure.database.migrations import validate_schema


class PostgresMail:
    def __init__(self, database, schema='app',*,private_delivery=False):
        validate_schema(schema)
        if type(private_delivery) is not bool:raise ValueError('Explicit private delivery capability required.')
        self._database, self._schema = database, schema
        self._private_delivery=private_delivery

    async def _call(self, name, lease, *args,before_commit=None,result_check=None):
        placeholders = sql.SQL(',').join(sql.Placeholder() for _ in range(5 + len(args)))
        query = sql.SQL('SELECT {}.{}({})').format(sql.Identifier(self._schema), sql.Identifier(name), placeholders)
        async with self._database.transaction() as connection:
            result=(await (await connection.execute(query, (lease.tenant_id, lease.id, lease.worker_id, lease.token, lease.generation, *args))).fetchone())[0]
            from airport_assistant.modules.identity.customer_transaction import run_before_commit
            if result_check is not None:
                run_before_commit(lambda:result_check(result))
            run_before_commit(before_commit)
            return result

    async def private_snapshot(self,lease):
        return await self._call('mail_private_snapshot',lease)

    async def private_begin(self,lease,expected,envelope,*,before_commit):
        import time
        from airport_assistant.modules.mail.private_outbound import PrivateMailContext
        from airport_assistant.modules.identity.customer_transaction import run_before_commit
        from airport_assistant.modules.mail.private_outbound import BeginNotCommitted
        def check():
            try:run_before_commit(before_commit)
            except Exception as error:raise BeginNotCommitted() from error
        record={k:str(v) if isinstance(v,UUID) else v for k,v in asdict(envelope).items()}
        started=time.monotonic()
        before=PrivateMailContext.capture(lease,expected,started)
        def verify(raw):
            check()
            if raw is False:return
            PrivateMailContext.capture(lease,raw,started).verify_new_reservation(before,envelope)
        return await self._call('mail_private_begin',lease,Jsonb(expected),Jsonb(record),before_commit=check,result_check=verify)

    async def private_send_snapshot(self,lease,email_id,*,before_commit=None):
        return await self._call('mail_private_send_snapshot',lease,email_id,before_commit=before_commit)

    async def abort_unsent(self,lease,email_id,envelope_hash):
        return await self._call('mail_private_abort_unsent',lease,email_id,envelope_hash)

    async def private_complete(self,lease,email_id,envelope_hash,result):
        return await self._call('mail_private_complete',lease,email_id,envelope_hash,result.state,result.provider_message_id)

    async def snapshot(self, lease):
        return await self._call('mail_snapshot', lease)

    async def begin(self, lease, expected, envelope):
        record = {k: str(v) if v is not None else None for k, v in asdict(envelope).items()}
        return await self._call('mail_begin', lease, Jsonb(expected), Jsonb(record))

    async def complete(self, lease, id, result):
        return await self._call('mail_complete', lease, id, result.state, result.provider_message_id)

    async def delivery_snapshot(self, lease):
        return await self._call('mail_private_delivery_snapshot' if self._private_delivery else 'mail_delivery_snapshot', lease)

    async def observe(self, lease, snapshot, evidence, reason=None):
        return await self._call('mail_private_delivery_observe' if self._private_delivery else 'mail_delivery_observe', lease, UUID(snapshot['id']), snapshot['delivery_version'],
                                Jsonb(evidence.record()) if evidence else None, reason)
