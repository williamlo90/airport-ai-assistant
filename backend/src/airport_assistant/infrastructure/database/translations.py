"""Translation previews share staff messaging transactions and lock order."""
from uuid import uuid4
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.infrastructure.database.publication import PostgresPublication,PublicationSQL

FIELDS='id,case_id,conversation_id,request_key,request_hash,original_body,source_language,target_language,channel,case_version,conversation_version,generation_epoch,state,translated_body,reviewed_body,message_id,send_key,revision,created_at,expires_at'
INTERNAL=',tenant_id,principal_id,session_id,resource_security_labels,source_context_hash'


class PostgresTranslations(PostgresPublication):
    def unit(self,connection,context):
        result=TranslationSQL(connection,self._schema,context)
        result.mail_enabled=self._mail_enabled
        return result


class TranslationSQL(PublicationSQL):
    async def case_translation_ready(self):
        if not self.mail_enabled:return False
        if (await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.case_email_translation_ready()',))).fetchone())!=(True,):return False
        return (await(await self._c.execute(self.query('SELECT {s}.case_email_translation_ready()'))).fetchone())==(True,)

    async def case_translation(self,id=None,*,key=None,lock=False):
        fields='id,case_id,principal_id,session_id,membership_id,request_key,request_hash,original_body,source_language,target_language,channel,case_version,source_context_hash,resource_security_labels,state,translated_body,reviewed_body,message_id,send_key,revision,created_at,expires_at'
        predicate='id=%s' if id is not None else 'principal_id=%s AND request_key=%s'
        params=(id,) if id is not None else (self._context.principal_id,key)
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.case_email_translation_drafts WHERE tenant_id=%s AND '+predicate+(' FOR UPDATE' if lock else '')),
            (self._context.tenant_id,*params))).fetchone()
        return dict(zip(fields.split(','),row),conversation_id=None) if row else None

    async def create_case_translation(self,case,request,digest,context_hash,requirements):
        from psycopg.types.json import Jsonb
        ctx=self._context;id=uuid4()
        await self._c.execute(self.query('''INSERT INTO {s}.case_email_translation_drafts
          (tenant_id,id,case_id,principal_id,session_id,membership_id,request_key,request_hash,original_body,source_language,target_language,case_version,source_context_hash,resource_security_labels)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'''),
          (ctx.tenant_id,id,case,ctx.principal_id,ctx.session_id,request.membership_id,request.request_key,digest,
           request.body,request.language,request.target_language,request.expected_version,context_hash,
           Jsonb(requirements.to_records() if requirements else [])))
        return await self.case_translation(id)

    async def finish_case_translation(self,row,state,*,text=None,reviewed=None,message_id=None,key=None):
        await self._c.execute(self.query('''UPDATE {s}.case_email_translation_drafts SET state=%s,
            translated_body=CASE WHEN %s='ready' THEN %s ELSE translated_body END,
            reviewed_body=%s,message_id=%s,send_key=%s,revision=revision+1 WHERE tenant_id=%s AND id=%s AND revision=%s'''),
            (state,state,text,reviewed,message_id,key,self._context.tenant_id,row['id'],row['revision']))
        return await self.case_translation(row['id'])

    async def translation(self,id,*,lock=False):
        columns=FIELDS+",tenant_id,principal_id,session_id,coalesce(to_jsonb(t)->'resource_security_labels','[]'::jsonb),to_jsonb(t)->>'source_context_hash'"
        row=await(await self._c.execute(self.query('SELECT '+columns+' FROM {s}.staff_translation_drafts t WHERE tenant_id=%s AND id=%s'+(' FOR UPDATE' if lock else '')),(self._context.tenant_id,id))).fetchone()
        return dict(zip((FIELDS+INTERNAL).split(','),row)) if row else None

    async def translation_request(self,key):
        row=await(await self._c.execute(self.query('SELECT id FROM {s}.staff_translation_drafts WHERE tenant_id=%s AND principal_id=%s AND request_key=%s'),(self._context.tenant_id,self._context.principal_id,key))).fetchone()
        return await self.translation(row[0]) if row else None

    async def translation_context_clear(self,case,conversation):
        row=await(await self._c.execute(self.query("SELECT EXISTS(SELECT 1 FROM {s}.cases c JOIN {s}.conversations v ON(v.tenant_id,v.case_id)=(c.tenant_id,c.id) WHERE c.tenant_id=%s AND c.id=%s AND v.id=%s AND c.resource_security_labels='[]'::jsonb AND v.resource_security_labels='[]'::jsonb)"),(self._context.tenant_id,case,conversation))).fetchone()
        return row[0]

    async def create_translation(self,case,request,digest,*,requirements=None,source_context_hash=None):
        ctx=self._context;id=uuid4()
        columns='';placeholders='';extra=()
        if requirements is not None:
            from airport_assistant.modules.source.labels import SecurityLabels
            from psycopg.types.json import Jsonb
            if type(requirements) is not SecurityLabels or requirements.tenant_id!=ctx.tenant_id:raise ValueError('Same-tenant requirements required.')
            columns+=',resource_security_labels';placeholders+=',%s';extra+=(Jsonb(requirements.to_records()),)
        if source_context_hash is not None:
            import re
            if type(source_context_hash) is not str or not re.fullmatch('[a-f0-9]{64}',source_context_hash):raise ValueError('Exact source-context hash required.')
            columns+=',source_context_hash';placeholders+=',%s';extra+=(source_context_hash,)
        await self._c.execute(self.query('''INSERT INTO {s}.staff_translation_drafts
          (tenant_id,id,case_id,conversation_id,principal_id,session_id,request_key,request_hash,original_body,source_language,target_language,channel,case_version,conversation_version,generation_epoch'''+columns+''')
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s'''+placeholders+''')'''),
          (ctx.tenant_id,id,case,request.conversation_id,ctx.principal_id,ctx.session_id,request.request_key,digest,request.body,request.language,request.target_language,request.channel,request.expected_version,request.expected_conversation_version,request.expected_epoch,*extra))
        return await self.translation(id)

    async def translation_source_context(self,case_id,conversation_id):
        fields=('tenant_id','case_id','conversation_id','case_version','conversation_version','generation_epoch','mode',
            'handler_membership_id','assigned_membership_id','last_sequence','last_event_sequence','case_labels','conversation_labels','handler_principal_id','supported_languages')
        row=await(await self._c.execute(self.query('''SELECT c.tenant_id,c.id,v.id,c.version,v.version,v.generation_epoch,v.mode,
            v.handler_membership_id,c.assigned_membership_id,v.last_sequence,v.last_event_sequence,c.resource_security_labels,v.resource_security_labels,
            ({s}.case_target_membership(v.handler_membership_id,c.organization_id,c.queue_id)->>'principal_id')::uuid,t.supported_languages
            FROM {s}.cases c JOIN {s}.conversations v ON(v.tenant_id,v.case_id)=(c.tenant_id,c.id)
            JOIN {s}.tenants t ON t.id=c.tenant_id
            WHERE c.tenant_id=%s AND c.id=%s AND v.id=%s'''),(self._context.tenant_id,case_id,conversation_id))).fetchone()
        return dict(zip(fields,row)) if row else None

    async def translation_message_event(self,message_id):
        fields=('tenant_id','conversation_id','message_id','event_type','language','sequence','last_event_sequence')
        rows=await(await self._c.execute(self.query('''SELECT e.tenant_id,e.conversation_id,e.message_id,e.event_type,e.language,
            e.sequence,v.last_event_sequence FROM {s}.conversation_events e
            JOIN {s}.conversations v ON(v.tenant_id,v.id)=(e.tenant_id,e.conversation_id)
            WHERE e.tenant_id=%s AND e.message_id=%s ORDER BY e.id'''),(self._context.tenant_id,message_id))).fetchall()
        if len(rows)!=1:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        return dict(zip(fields,rows[0]))

    async def finish_translation(self,row,body):
        await self._c.execute(self.query("UPDATE {s}.staff_translation_drafts SET state='ready',translated_body=%s,revision=revision+1 WHERE tenant_id=%s AND id=%s AND revision=%s"),(body,self._context.tenant_id,row['id'],row['revision']))
        return await self.translation(row['id'])

    async def fail_translation(self,row):
        await self._c.execute(self.query("UPDATE {s}.staff_translation_drafts SET state='failed',revision=revision+1 WHERE tenant_id=%s AND id=%s AND revision=%s"),(self._context.tenant_id,row['id'],row['revision']))

    async def sent_translation(self,row,body,message,key):
        await self._c.execute(self.query("UPDATE {s}.staff_translation_drafts SET state='sent',reviewed_body=%s,message_id=%s,send_key=%s,revision=revision+1 WHERE tenant_id=%s AND id=%s AND revision=%s"),(body,message,key,self._context.tenant_id,row['id'],row['revision']))
