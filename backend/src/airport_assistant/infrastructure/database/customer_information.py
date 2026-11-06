"""Atomic customer information graph; authorization decisions stay in modules."""
from datetime import datetime, timezone
from psycopg.types.json import Jsonb

from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle, LifecycleSQL, FIELDS


class PostgresCustomerInformation(PostgresCaseLifecycle):
    def unit(self,connection,context):return CustomerInformationSQL(connection,self._schema,context)


class CustomerInformationSQL(LifecycleSQL):
    async def information_ready(self):
        exists=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.customer_information_ready()',))).fetchone())[0]
        return bool(exists and (await(await self._c.execute(self.query('SELECT {s}.customer_information_ready()'))).fetchone())[0])

    async def customer_ancestry(self,id):
        return (await(await self._c.execute(self.query('SELECT {s}.customer_case_ancestry(%s)'),(id,))).fetchone())[0]

    async def information_key_lock(self,key):
        await self._c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
            ('customer-information:'+str(self._context.tenant_id)+':'+str(self._context.principal_id)+':'+key,))

    async def information_receipt(self,*,key=None,preview=None):
        fields=('id','case_id','principal_id','session_id','request_key','request_hash','message_id','message','language',
                'expected_version','resource_security_labels','preview_expires_at')
        predicate='u.request_key=%s' if key is not None else 'u.id=%s'
        row=await(await self._c.execute(self.query('SELECT '+','.join('u.'+f for f in fields)+''',m.sequence,m.body,m.language,m.resource_security_labels
            FROM {s}.customer_case_updates u LEFT JOIN {s}.messages m ON(m.tenant_id,m.id)=(u.tenant_id,u.message_id)
            WHERE u.tenant_id=%s AND u.principal_id=%s AND '''+predicate),
            (self._context.tenant_id,self._context.principal_id,key if key is not None else preview))).fetchone()
        return dict(zip((*fields,'sequence','stored_body','stored_language','message_labels'),row)) if row else None

    async def publish_information(self,record,key,digest,message_id,labels):
        ctx=self._context
        await self._c.execute(self.query('''INSERT INTO {s}.customer_case_updates(tenant_id,id,case_id,principal_id,session_id,
            request_key,request_hash,message_id,message,language,expected_version,preview_expires_at,resource_security_labels)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'''),
            (ctx.tenant_id,record.preview_id,record.case_id,ctx.principal_id,ctx.session_id,key,digest,message_id,
             record.message,record.language,record.expected_version,datetime.fromtimestamp(record.expires_at,timezone.utc),Jsonb(labels)))
        sequence=(await(await self._c.execute(self.query('SELECT coalesce(max(sequence),0)+1 FROM {s}.messages WHERE tenant_id=%s AND case_id=%s'),(ctx.tenant_id,record.case_id))).fetchone())[0]
        await self._c.execute(self.query('''INSERT INTO {s}.messages(tenant_id,id,case_id,author_principal_id,author_type,body,
            language,client_message_id,sequence,source_kind,created_session_id,request_hash,basis_case_version,resource_security_labels)
            VALUES(%s,%s,%s,%s,'customer',%s,%s,%s,%s,'customer_update',%s,%s,%s,%s)'''),
            (ctx.tenant_id,message_id,record.case_id,ctx.principal_id,record.message,record.language,record.preview_id,
             sequence,ctx.session_id,digest,record.expected_version,Jsonb(labels)))
        row=await(await self._c.execute(self.query('UPDATE {s}.cases SET version=version+1 WHERE tenant_id=%s AND id=%s AND version=%s RETURNING '+','.join(FIELDS)),
            (ctx.tenant_id,record.case_id,record.expected_version))).fetchone()
        if row is None:
            from airport_assistant.modules.drafts.models import DraftError
            raise DraftError(409,'VERSION_CONFLICT')
        updated=dict(zip(FIELDS,row))
        # Existing lifecycle snapshot writer captures unchanged state and the
        # exact original customer message/preview nonce for the new revision.
        from types import SimpleNamespace
        before={**updated,'version':record.expected_version}
        await self.history(before,updated,'customer_information',command=SimpleNamespace(message=record.message,client_request_id=record.preview_id),digest=digest)
        return updated
