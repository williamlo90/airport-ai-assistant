"""Durable private bytea blobs; no public URLs or filesystem paths."""
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL


class PostgresAttachments(PostgresCaseLifecycle):
    def unit(self,connection,context):return AttachmentSQL(connection,self._schema,context)


class AttachmentSQL(LifecycleSQL):
    async def private_attachments_ready(self):
        exists=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.private_attachments_ready()',))).fetchone())[0]
        return bool(exists and (await(await self._c.execute(self.query('SELECT {s}.private_attachments_ready()'))).fetchone())[0])

    async def case_attachments(self,id):
        fields='id,byte_size,mime_type,scan_status,resource_security_labels,content_hash'
        rows=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.attachments WHERE tenant_id=%s AND case_id=%s ORDER BY created_at,id'),(self._context.tenant_id,id))).fetchall()
        return [dict(zip(fields.split(','),row)) for row in rows]

    async def attachment(self,id,*,body=False):
        fields='id,draft_id,byte_size,mime_type,scan_status,content_hash,case_id,resource_security_labels'+(',body' if body else '')
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.attachments WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def attachment_parent(self,row):
        # The parent query uses the SAME current RLS/session context as the blob.
        # Table choice comes from stored metadata, not a request identifier/type.
        if row['case_id'] is not None:
            table,id,extra='cases',row['case_id'],''
        else:
            table,id,extra='action_drafts',row['draft_id'],',payload_hash'
        fields='id,version,resource_security_labels'+extra
        parent=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.'+table+' WHERE tenant_id=%s AND id=%s'),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),parent)) if parent else None

    async def attachment_count(self,id):
        return (await(await self._c.execute(self.query('SELECT count(*) FROM {s}.attachments WHERE tenant_id=%s AND draft_id=%s'),(self._context.tenant_id,id))).fetchone())[0]

    async def insert_attachment(self,id,draft,body,mime,digest):
        ctx=self._context
        await self._c.execute(self.query('''INSERT INTO {s}.attachments(tenant_id,id,draft_id,uploader_principal_id,uploader_session_id,created_session_id,body,mime_type,byte_size,content_hash,scan_status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'clean')'''),
            (ctx.tenant_id,id,draft.id,ctx.principal_id,ctx.session_id if ctx.actor_kind=='anonymous' else None,ctx.session_id,body,mime,len(body),digest))
        return await self.attachment(id)
