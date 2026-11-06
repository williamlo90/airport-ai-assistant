"""Message writes share the lifecycle unit of work and consistent lock order."""
from uuid import UUID,uuid4,uuid5
from psycopg import Error
from airport_assistant.modules.conversations.publication import GenerationExpired
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.agent.answers import KnowledgeAnswerUnavailable


class PostgresPublication(PostgresCaseLifecycle):
    def __init__(self,database,schema='app',*,mail_enabled=False):
        super().__init__(database,schema)
        self._mail_enabled=mail_enabled

    def unit(self,connection,context):
        unit=PublicationSQL(connection,self._schema,context)
        unit.mail_enabled=self._mail_enabled
        return unit


class PublicationSQL(LifecycleSQL):
    mail_enabled=False
    async def send_key(self,key):
        import json
        await self._c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(json.dumps(['staff_message',str(self._context.tenant_id),str(self._context.principal_id),key]),))

    async def staff_receipt(self,key):
        fields='id,conversation_id,sequence,request_hash,resource_security_labels'+(',delivery_channel' if self.mail_enabled else '')
        row=await(await self._c.execute(self.query('SELECT '+fields+" FROM {s}.messages WHERE tenant_id=%s AND author_principal_id=%s AND author_type='staff' AND staff_request_key=%s"),(self._context.tenant_id,self._context.principal_id,key))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def staff_receipt_ancestry(self,message_id,case_id):
        row=await(await self._c.execute(self.query('''SELECT c.resource_security_labels,
            CASE WHEN m.conversation_id IS NULL THEN '[]'::jsonb ELSE v.resource_security_labels END,m.author_type,m.case_id
            FROM {s}.messages m LEFT JOIN {s}.conversations v ON(v.tenant_id,v.id)=(m.tenant_id,m.conversation_id)
            JOIN {s}.cases c ON c.tenant_id=m.tenant_id AND c.id=coalesce(m.case_id,v.case_id)
            WHERE m.tenant_id=%s AND m.id=%s AND c.id=%s'''),
            (self._context.tenant_id,message_id,case_id))).fetchone()
        if row is None:return None
        exists=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.case_correspondence_ancestry(uuid)',))).fetchone())[0]
        if not exists and row[2]=='staff' and row[3] is not None:return None
        origin=(await(await self._c.execute(self.query('SELECT {s}.case_correspondence_ancestry(%s)'),(case_id,))).fetchone())[0] if exists else []
        if origin is None:return None
        return dict(case_labels=row[0],conversation_labels=row[1],origin_labels=origin)

    @staticmethod
    def message_view(row,case_id):return dict(state='saved',message_id=row['id'],conversation_id=row['conversation_id'],case_id=case_id,sequence=row['sequence'],channel=row.get('delivery_channel','chat'))

    async def staff_message(self,case,chat,command,key,digest,*,requirements=None):
        ctx=self._context
        seq=(await(await self._c.execute(self.query('UPDATE {s}.conversations SET last_sequence=last_sequence+1,version=version+1 WHERE tenant_id=%s AND id=%s RETURNING last_sequence'),(ctx.tenant_id,chat['id']))).fetchone())[0]
        columns=',delivery_channel' if self.mail_enabled else ''
        values=',%s' if self.mail_enabled else ''
        from airport_assistant.modules.source.labels import SecurityLabels
        from psycopg.types.json import Jsonb
        if requirements is not None and (type(requirements) is not SecurityLabels or requirements.tenant_id!=ctx.tenant_id):
            raise ValueError('Trusted same-tenant requirements required.')
        if requirements is not None:columns+=',resource_security_labels';values+=',%s'
        query="""INSERT INTO {s}.messages(tenant_id,id,conversation_id,author_principal_id,author_type,source_kind,body,language,client_message_id,sequence,created_session_id,sender_membership_id,staff_request_key,request_hash,basis_case_version,basis_conversation_version,basis_epoch"""+columns+") VALUES(%s,%s,%s,%s,'staff','staff_reply',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"+values+") ON CONFLICT DO NOTHING RETURNING id,conversation_id,sequence"+columns
        parameters=(ctx.tenant_id,uuid4(),chat['id'],ctx.principal_id,command.body,command.language,uuid5(chat['id'],str(ctx.principal_id)+':'+key),seq,ctx.session_id,chat['handler_membership_id'],key,digest,case['version'],chat['version'],chat['generation_epoch'])
        parameters+=((command.channel,) if self.mail_enabled else ())
        if requirements is not None:parameters+=(Jsonb(requirements.to_records()),)
        row=await(await self._c.execute(self.query(query),parameters)).fetchone()
        if row is None:raise DraftError(409,'IDEMPOTENCY_CONFLICT')
        fields=('id','conversation_id','sequence')+(('delivery_channel',) if self.mail_enabled else ())
        if requirements is not None:fields+=('resource_security_labels',)
        return dict(zip(fields,row))

    async def mail_available(self,case_id):
        if not self.mail_enabled:return False
        return (await(await self._c.execute(self.query("SELECT EXISTS(SELECT 1 FROM {s}.cases c JOIN {s}.email_accounts a ON a.tenant_id=c.tenant_id AND a.active WHERE c.tenant_id=%s AND c.id=%s AND c.followup_contact_id IS NOT NULL)"),(self._context.tenant_id,case_id))).fetchone())[0]

    async def case_email_ready(self):
        if not self.mail_enabled:return False
        signature=self._schema+'.case_email_ready()'
        if (await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',(signature,))).fetchone())!=(True,):return False
        return (await(await self._c.execute(self.query('SELECT {s}.case_email_ready()'))).fetchone())==(True,)

    async def case_email_context(self,case_id):
        context=(await(await self._c.execute(self.query('SELECT {s}.case_email_context(%s)'),(case_id,))).fetchone())[0]
        if context is None:return None
        context=dict(context)
        for key in ('tenant_id','case_id','assigned_membership_id'):
            if context.get(key) is not None:context[key]=UUID(context[key])
        recipient=dict(context['recipient'])
        for key in ('tenant_id','principal_id','owner_principal_id','contact_id','account_id'):
            if recipient.get(key) is not None:recipient[key]=UUID(recipient[key])
        context['recipient']=recipient
        return context

    async def case_correspondence_ancestry(self,case_id):
        exists=(await(await self._c.execute('SELECT to_regprocedure(%s) IS NOT NULL',
            (self._schema+'.case_correspondence_ancestry(uuid)',))).fetchone())[0]
        if not exists:return None
        return (await(await self._c.execute(self.query('SELECT {s}.case_correspondence_ancestry(%s)'),(case_id,))).fetchone())[0]

    async def staff_correspondence(self,case_id,before_time,before_id,limit):
        fields=('id','case_id','conversation_id','author_type','body','language','sequence','channel','created_at',
                'resource_security_labels','conversation_labels')
        # Disjoint parent shapes make each case filter independently indexable.
        # Actual scaled query plans remain part of the quality checkpoint.
        shared="""m.id,{parent} AS case_id,m.conversation_id,m.author_type,m.body,m.language,m.sequence,
            CASE WHEN m.channel='email' OR m.delivery_channel='email' THEN 'email' ELSE 'chat' END AS channel,
            m.created_at,m.resource_security_labels,{labels} AS conversation_labels"""
        position='AND(%s::timestamptz IS NULL OR(m.created_at,m.id)<(%s::timestamptz,%s::uuid))'
        direct='SELECT '+shared.format(parent='m.case_id',labels="'[]'::jsonb")+''' FROM {s}.messages m
            WHERE m.tenant_id=%s AND m.case_id=%s AND m.conversation_id IS NULL '''+position
        linked='SELECT '+shared.format(parent='v.case_id',labels='v.resource_security_labels')+''' FROM {s}.conversations v
            JOIN {s}.messages m ON(m.tenant_id,m.conversation_id)=(v.tenant_id,v.id)
            WHERE v.tenant_id=%s AND v.case_id=%s AND m.case_id IS NULL AND m.conversation_id IS NOT NULL '''+position
        params=(self._context.tenant_id,case_id,before_time,before_time,before_id,limit)
        query='WITH recent AS (('+direct+' ORDER BY m.created_at DESC,m.id DESC LIMIT %s) UNION ALL ('+linked+'''
            ORDER BY m.created_at DESC,m.id DESC LIMIT %s)) SELECT * FROM recent ORDER BY created_at DESC,id DESC LIMIT %s'''
        rows=await(await self._c.execute(self.query(query),(*params,*params,limit))).fetchall()
        return [dict(zip(fields,row)) for row in rows]

    async def case_email_message(self,case,command,key,digest,*,requirements=None):
        from psycopg.types.json import Jsonb
        from airport_assistant.modules.source.labels import SecurityLabels
        if requirements is not None and (type(requirements) is not SecurityLabels or requirements.tenant_id!=self._context.tenant_id):
            raise ValueError('Trusted same-tenant requirements required.')
        ctx=self._context
        row=await(await self._c.execute(self.query('''INSERT INTO {s}.messages
            (tenant_id,id,case_id,author_principal_id,author_type,source_kind,body,language,client_message_id,sequence,
             created_session_id,sender_membership_id,staff_request_key,request_hash,basis_case_version,delivery_channel,resource_security_labels)
            SELECT %s,%s,%s,%s,'staff','staff_reply',%s,%s,%s,coalesce(max(sequence),0)+1,%s,%s,%s,%s,%s,'email',%s
            FROM {s}.messages WHERE tenant_id=%s AND case_id=%s
            RETURNING id,conversation_id,sequence,delivery_channel'''),
            (ctx.tenant_id,uuid4(),case['id'],ctx.principal_id,command.body,command.language,
             uuid5(case['id'],str(ctx.principal_id)+':'+key),ctx.session_id,command.membership_id,key,digest,case['version'],
             Jsonb(requirements.to_records() if requirements else []),ctx.tenant_id,case['id']))).fetchone()
        return dict(zip(('id','conversation_id','sequence','delivery_channel'),row))

    async def enqueue_email(self,case_id,message_id,*,requirements=None):
        from psycopg.types.json import Jsonb
        from airport_assistant.modules.source.labels import SecurityLabels
        columns='';value='';parameters=(message_id,message_id)
        if requirements is not None:
            if type(requirements) is not SecurityLabels or requirements.tenant_id!=self._context.tenant_id:
                raise ValueError('Trusted same-tenant requirements required.')
            columns=',resource_security_labels';value=',%s';parameters+=(Jsonb(requirements.to_records()),)
        row=await(await self._c.execute(self.query("INSERT INTO {s}.outbox_jobs(tenant_id,case_id,action_draft_id,kind,source_message_id,dedupe_key,payload"+columns+") SELECT tenant_id,id,origin_draft_id,'email_send',%s,'email:'||%s::text,jsonb_build_object('case_id',id::text,'draft_id',origin_draft_id::text)"+value+" FROM {s}.cases WHERE tenant_id=%s AND id=%s RETURNING id"),(*parameters,self._context.tenant_id,case_id))).fetchone()
        if row is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        return row[0]

    async def staff_disclosure_context(self,case_id,conversation_id,channel):
        fields=('tenant_id','case_id','conversation_id','case_version','conversation_version','generation_epoch',
            'mode','handler_membership_id','assigned_membership_id','last_sequence','case_labels','conversation_labels')
        row=await(await self._c.execute(self.query('''SELECT c.tenant_id,c.id,v.id,c.version,v.version,v.generation_epoch,
            v.mode,v.handler_membership_id,c.assigned_membership_id,v.last_sequence,c.resource_security_labels,v.resource_security_labels
            FROM {s}.cases c JOIN {s}.conversations v ON(v.tenant_id,v.case_id)=(c.tenant_id,c.id)
            WHERE c.tenant_id=%s AND c.id=%s AND v.id=%s'''),(self._context.tenant_id,case_id,conversation_id))).fetchone()
        if row is None:return None
        result=dict(zip(fields,row))
        if channel=='chat':
            record=(await(await self._c.execute(self.query('SELECT {s}.source_chat_recipient(%s,%s)'),(case_id,conversation_id))).fetchone())[0]
            if record is not None:
                record=dict(record,tenant_id=UUID(record['tenant_id']),principal_id=UUID(record['principal_id']) if record['principal_id'] else None)
            result['recipient']=record
            return result
        elif channel=='email':
            record=(await(await self._c.execute(self.query('SELECT {s}.source_email_recipient(%s,%s)'),(case_id,conversation_id))).fetchone())[0]
            if record is not None:
                record=dict(record)
                for key in ('tenant_id','principal_id','owner_principal_id','contact_id','account_id'):
                    if record.get(key) is not None:record[key]=UUID(record[key])
            result['recipient']=record
            return result
        else:raise ValueError('Explicit disclosure channel required.')
        result['recipient']=dict(zip(names,recipient)) if recipient else None
        return result

    async def staff_disclosure_result(self,message_id):
        fields=('id','tenant_id','conversation_id','case_id','author_principal_id','created_session_id',
            'body','language','staff_request_key','request_hash','sequence','basis_case_version',
            'basis_conversation_version','basis_epoch','resource_security_labels','author_type','source_kind',
            'delivery_channel','sender_membership_id')
        channel='m.delivery_channel' if self.mail_enabled else "'chat'"
        row=await(await self._c.execute(self.query('''SELECT m.id,m.tenant_id,m.conversation_id,coalesce(m.case_id,c.case_id),m.author_principal_id,
            m.created_session_id,m.body,m.language,m.staff_request_key,m.request_hash,m.sequence,m.basis_case_version,
            m.basis_conversation_version,m.basis_epoch,m.resource_security_labels,m.author_type,m.source_kind,'''+channel+''',m.sender_membership_id
            FROM {s}.messages m LEFT JOIN {s}.conversations c ON(c.tenant_id,c.id)=(m.tenant_id,m.conversation_id)
            WHERE m.tenant_id=%s AND m.id=%s'''),(self._context.tenant_id,message_id))).fetchone()
        if row is None:return None
        jobs=await(await self._c.execute(self.query('''SELECT id,case_id,source_message_id,resource_security_labels
            FROM {s}.outbox_jobs WHERE tenant_id=%s AND source_message_id=%s AND kind='email_send' ORDER BY id'''),
            (self._context.tenant_id,message_id))).fetchall()
        if len(jobs)>1:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        return dict(message=dict(zip(fields,row)),job=dict(zip(('id','case_id','source_message_id','resource_security_labels'),jobs[0])) if jobs else None)

    async def ai_generation_context(self,id):
        fields=('id','tenant_id','conversation_id','origin_session_id','principal_id','input_version','generation_epoch',
            'language','nonce_hash','state','expires_at','message_id','resource_security_labels')
        # None means this future private field is absent, never public[].
        row=await(await self._c.execute(self.query('''SELECT id,tenant_id,conversation_id,origin_session_id,principal_id,
            input_version,generation_epoch,language,nonce_hash,state,expires_at,message_id,
            to_jsonb(g)->'resource_security_labels' FROM {s}.ai_generations g WHERE tenant_id=%s AND id=%s'''),
            (self._context.tenant_id,id))).fetchone()
        return dict(zip(fields,row)) if row else None

    async def ai_root_publication_packet(self,generation_id,conversation_id=None):
        """Coherent root-only packet; linked cases retain parent-first locks.

        The unlocked hint selects the lock strategy, never authorizes content.
        A root linked while acquiring its lock is rejected, not retried with a
        parent lock after the conversation lock. Each gate load repeats this
        query under READ COMMITTED and normal row policies.
        """
        from dataclasses import asdict
        from .conversation_repository import FIELDS,conversation
        tenant=self._context.tenant_id
        hint=await(await self._c.execute(self.query('''SELECT id,case_id FROM {s}.conversations
            WHERE tenant_id=%s AND id=coalesce(%s::uuid,(SELECT conversation_id FROM {s}.ai_generations WHERE tenant_id=%s AND id=%s))'''),
            (tenant,conversation_id,tenant,generation_id))).fetchone()
        if hint is None or hint[1] is not None:return None
        # Lock first, then obtain a NEW statement snapshot. A FOR UPDATE wait
        # may return a newer tuple while unlocked joins retain the old snapshot;
        # that mixed statement must never become an authorization projection.
        locked=await(await self._c.execute(self.query('''WITH root AS MATERIALIZED(
            SELECT c.id FROM {s}.conversations c WHERE c.tenant_id=%s AND c.id=%s AND c.case_id IS NULL FOR UPDATE OF c),
            generation AS MATERIALIZED(SELECT g.id FROM {s}.ai_generations g WHERE g.tenant_id=%s AND g.id=%s
                AND EXISTS(SELECT 1 FROM root) FOR UPDATE OF g)
            SELECT root.id,(SELECT id FROM generation) FROM root'''),(tenant,hint[0],tenant,generation_id))).fetchone()
        if locked is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        run_fields=('id','tenant_id','conversation_id','session_id','principal_id','phase','revision')
        gen_fields=('id','tenant_id','conversation_id','origin_session_id','principal_id','input_version','generation_epoch',
            'language','nonce_hash','state','expires_at','message_id','resource_security_labels')
        message_fields=('id','tenant_id','conversation_id','generation_id','created_session_id','author_type','source_kind',
            'body','language','sequence','basis_conversation_version','basis_epoch','resource_security_labels','knowledge_evidence')
        event_fields=('id','tenant_id','conversation_id','message_id','language','event_type','sequence')
        select=','.join('c.'+f for f in FIELDS.split(','))+',c.last_event_sequence,'
        select+=','.join('r.'+f for f in run_fields)+",coalesce(to_jsonb(r)->'resource_security_labels','[]'::jsonb),"
        select+=','.join('g.'+f for f in gen_fields)+',mp.items,ep.items,clock_timestamp(),p.source_subject_ref'
        row=await(await self._c.execute(self.query('''WITH root AS MATERIALIZED (
            SELECT c.* FROM {s}.conversations c WHERE c.tenant_id=%s AND c.id=%s AND c.case_id IS NULL FOR UPDATE OF c),
            generation AS MATERIALIZED (
            SELECT g.* FROM {s}.ai_generations g WHERE g.tenant_id=%s AND g.id=%s AND EXISTS(SELECT 1 FROM root) FOR UPDATE OF g)
            SELECT '''+select+''' FROM root c
            LEFT JOIN {s}.agent_runs r ON r.tenant_id=c.tenant_id AND r.id=%s
            LEFT JOIN generation g ON true
            LEFT JOIN {s}.principals p ON p.tenant_id=c.tenant_id AND p.id=c.customer_principal_id AND p.active
            LEFT JOIN LATERAL(SELECT jsonb_agg(to_jsonb(item)) items FROM (
                SELECT '''+','.join(message_fields)+''' FROM {s}.messages
                WHERE tenant_id=c.tenant_id AND generation_id=g.id AND author_type='ai' ORDER BY id LIMIT 2) item) mp ON true
            LEFT JOIN LATERAL(SELECT jsonb_agg(to_jsonb(item)) items FROM (
                SELECT '''+','.join(event_fields)+''' FROM {s}.conversation_events
                WHERE tenant_id=c.tenant_id AND message_id IN(SELECT m.id FROM {s}.messages m
                    WHERE m.tenant_id=c.tenant_id AND m.generation_id=g.id AND m.author_type='ai')
                ORDER BY id LIMIT 2) item) ep ON true'''),(tenant,hint[0],tenant,generation_id,generation_id))).fetchone()
        if row is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        conv=conversation(row[:11]);last_event=row[11]
        run=dict(zip(run_fields,row[12:19])) if row[12] is not None else None
        generation=dict(zip(gen_fields,row[20:33])) if row[20] is not None else None
        context=dict(conversation=dict(asdict(conv),resource_security_labels=list(conv.resource_security_labels),last_event_sequence=last_event),
            parent=None,participant_active=True,run=run,run_labels=row[19] if run else [],generation=generation,source_subject_ref=row[36])
        graph=None
        if generation is not None and generation['state']=='published':
            messages,events=row[33],row[34]
            if type(messages) is not list or len(messages)!=1 or type(events) is not list or len(events)!=1:
                raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
            message,event=dict(messages[0]),dict(events[0])
            for value,keys in ((message,('id','tenant_id','conversation_id','generation_id','created_session_id')),
                               (event,('id','tenant_id','conversation_id','message_id'))):
                for key in keys:
                    if value[key] is not None:value[key]=UUID(value[key])
            graph=dict(generation=generation,message=message,event=event)
        return dict(context=context,generation=generation,graph=graph,now=row[35])

    async def ai_publication_context(self,conversation_id,generation_id):
        from dataclasses import asdict
        from .conversation_repository import FIELDS,conversation
        locked=await self.lock_conversation_case(conversation_id)
        row=await(await self._c.execute(self.query('SELECT '+FIELDS+',last_event_sequence FROM {s}.conversations WHERE tenant_id=%s AND id=%s FOR UPDATE'),
            (self._context.tenant_id,conversation_id))).fetchone()
        conv=conversation(row[:-1]) if row else None
        if conv is None:return None
        if conv.case_id!=locked:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        last_event=row[-1]
        parent=await self.related_source_context(conv.case_id) if conv.case_id is not None else None
        run_fields=('id','tenant_id','conversation_id','session_id','principal_id','phase','revision','resource_security_labels')
        gen_fields=('id','tenant_id','conversation_id','origin_session_id','principal_id','input_version','generation_epoch',
            'language','nonce_hash','state','expires_at','message_id','resource_security_labels')
        # Parent→conversation locks are already held. Acquire the generation
        # lock inside its lateral projection and return exact typed metadata in
        # one round trip, including the no-generation-yet capture case.
        run_select=','.join('r.'+f for f in run_fields[:-1])+",coalesce(to_jsonb(r)->'resource_security_labels','[]'::jsonb)"
        gen_select=','.join('g.'+f for f in gen_fields[:-1])+",to_jsonb(g)->'resource_security_labels'"
        metadata=await(await self._c.execute(self.query('SELECT '+run_select+','+gen_select+''',p.source_subject_ref FROM (SELECT 1) seed
            LEFT JOIN {s}.agent_runs r ON r.tenant_id=%s AND r.id=%s
            LEFT JOIN LATERAL(SELECT * FROM {s}.ai_generations item WHERE item.tenant_id=%s AND item.id=%s FOR UPDATE) g ON true
            LEFT JOIN {s}.principals p ON p.tenant_id=%s AND p.id=%s AND p.active'''),
            (self._context.tenant_id,generation_id,self._context.tenant_id,generation_id,self._context.tenant_id,self._context.principal_id))).fetchone()
        run=dict(zip(run_fields[:-1],metadata[:7])) if metadata[0] is not None else None
        generation=dict(zip(gen_fields,metadata[8:])) if metadata[8] is not None else None
        participant=True
        # Participation can change without the conversation/generation locks.
        # Its check is the final awaited statement in this context projection.
        if conv.case_id is not None:
            participant=(await(await self._c.execute(self.query('''SELECT EXISTS(SELECT 1 FROM {s}.case_participants
                WHERE tenant_id=%s AND case_id=%s AND principal_id=%s AND revoked_at IS NULL)'''),
                (self._context.tenant_id,conv.case_id,self._context.principal_id))).fetchone())[0]
        return dict(conversation=dict(asdict(conv),resource_security_labels=list(conv.resource_security_labels),last_event_sequence=last_event),parent=parent,participant_active=participant,
            run=run,run_labels=metadata[7] if run else [],generation=generation,source_subject_ref=metadata[21])

    async def ai_publication_result(self,generation_id):
        generation=await self.ai_generation_context(generation_id)
        fields=('id','tenant_id','conversation_id','generation_id','created_session_id','author_type','source_kind',
            'body','language','sequence','basis_conversation_version','basis_epoch','resource_security_labels','knowledge_evidence')
        rows=await(await self._c.execute(self.query('SELECT '+','.join(fields)+''' FROM {s}.messages
            WHERE tenant_id=%s AND generation_id=%s AND author_type='ai' ORDER BY id'''),
            (self._context.tenant_id,generation_id))).fetchall()
        if len(rows)!=1:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        message=dict(zip(fields,rows[0]))
        fields=('id','tenant_id','conversation_id','message_id','language','event_type','sequence')
        events=await(await self._c.execute(self.query('SELECT '+','.join(fields)+''' FROM {s}.conversation_events
            WHERE tenant_id=%s AND message_id=%s ORDER BY id'''),(self._context.tenant_id,message['id']))).fetchall()
        if len(events)!=1:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        return dict(generation=generation,message=message,event=dict(zip(fields,events[0])))

    async def ai_create_generation(self,ticket,conversation,records):
        from types import SimpleNamespace
        from psycopg.types.json import Jsonb
        from airport_assistant.modules.source.labels import SecurityLabels
        from airport_assistant.modules.source.private_content import inherited_records
        if inherited_records(self._context.tenant_id,records)!=records:raise ValueError('Canonical requirements required.')
        # The generation INSERT trigger promotes the root atomically. Runtime
        # cannot arbitrarily relabel conversations or reseal an old snapshot.
        requirements=SecurityLabels.from_records(self._context.tenant_id,records) if records else None
        import hashlib
        await self.create_generation(ticket.id,SimpleNamespace(**conversation),hashlib.sha256(ticket.nonce.encode()).hexdigest(),requirements=requirements)

    async def ai_stale_generation(self,id):await self.stale_generation(id)

    async def ai_write_publication(self,id,body,records,*,knowledge_evidence=None):
        from airport_assistant.modules.source.labels import SecurityLabels
        from airport_assistant.modules.source.private_content import inherited_records
        from psycopg.types.json import Jsonb
        if inherited_records(self._context.tenant_id,records)!=records:raise ValueError('Canonical requirements required.')
        generation=await self.ai_generation_context(id)
        if generation is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        conv=await self.find(generation['conversation_id'],lock=True)
        if conv is None:raise DraftError(409,'SOURCE_CONTEXT_CHANGED')
        requirements=SecurityLabels.from_records(self._context.tenant_id,records) if records else None
        saved=await self.ai_message(conv,generation,body,knowledge_evidence=knowledge_evidence,requirements=requirements)
        if records:
            await self._c.execute(self.query("UPDATE {s}.ai_generations SET state='published',message_id=%s WHERE tenant_id=%s AND id=%s AND state='pending'"),
                (saved['id'],self._context.tenant_id,id))
        else:await self.published_generation(id,saved['id'])
        return saved

    async def ai_citation_context(self,conversation_id,message_id):
        row=await(await self._c.execute(self.query("SELECT generation_id FROM {s}.messages WHERE tenant_id=%s AND conversation_id=%s AND id=%s AND author_type='ai'"),
            (self._context.tenant_id,conversation_id,message_id))).fetchone()
        if row is None or row[0] is None:return None
        context=await self.ai_publication_context(conversation_id,row[0])
        graph=await self.ai_publication_result(row[0])
        if graph['message']['id']!=message_id or graph['generation'] is None:return None
        return dict(context=context,message=graph['message'],generation_labels=graph['generation']['resource_security_labels'],generation_id=row[0])

    async def create_generation(self,id,conversation,nonce_hash,*,requirements=None):
        ctx=self._context
        column='';placeholder=''
        values=(ctx.tenant_id,id,conversation.id,ctx.session_id,ctx.principal_id,conversation.version,conversation.generation_epoch,conversation.language,nonce_hash)
        if requirements is not None:
            from airport_assistant.modules.source.labels import SecurityLabels
            from psycopg.types.json import Jsonb
            if type(requirements) is not SecurityLabels or requirements.tenant_id!=ctx.tenant_id:raise ValueError('Same-tenant requirements required.')
            column=',resource_security_labels';placeholder=',%s';values+=(Jsonb(requirements.to_records()),)
        await self._c.execute(self.query('''INSERT INTO {s}.ai_generations(tenant_id,id,conversation_id,origin_session_id,principal_id,input_version,generation_epoch,language,nonce_hash'''+column+''')
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s'''+placeholder+''')'''),values)

    async def generation(self,id,*,lock=False):
        fields='id,conversation_id,input_version,generation_epoch,language,nonce_hash,state,expires_at,resource_security_labels'
        projection=fields.rsplit(',',1)[0]+",coalesce(to_jsonb(g)->'resource_security_labels','[]'::jsonb)"
        row=await(await self._c.execute(self.query('SELECT '+projection+' FROM {s}.ai_generations g WHERE tenant_id=%s AND id=%s'+(' FOR UPDATE' if lock else '')),(self._context.tenant_id,id))).fetchone()
        return dict(zip(fields.split(','),row)) if row else None

    async def generated_message(self,id):
        row=await(await self._c.execute(self.query("SELECT id,sequence,body,resource_security_labels FROM {s}.messages WHERE tenant_id=%s AND generation_id=%s AND author_type='ai'"),(self._context.tenant_id,id))).fetchone()
        return dict(zip(('id','sequence','body','resource_security_labels'),row)) if row else None

    async def stale_generation(self,id):
        await self._c.execute(self.query("UPDATE {s}.ai_generations SET state='stale' WHERE tenant_id=%s AND id=%s AND state='pending'"),(self._context.tenant_id,id))

    async def lock_knowledge_answer(self):
        try:await self._c.execute(self.query('SELECT {s}.knowledge_answer_lock()'))
        except Error as error:
            if error.sqlstate in {'55P03','57014'}:raise KnowledgeAnswerUnavailable() from None
            raise

    async def current_answer_chunks(self,selection,citations):
        from airport_assistant.infrastructure.database.knowledge import KnowledgeSQL
        return await KnowledgeSQL(self._c,self._schema,self._context).evidence_chunks(selection,citations)

    async def message_knowledge(self,conversation,id):
        row=await(await self._c.execute(self.query("SELECT knowledge_evidence,resource_security_labels FROM {s}.messages WHERE tenant_id=%s AND conversation_id=%s AND id=%s AND author_type='ai'"),(self._context.tenant_id,conversation,id))).fetchone()
        return dict(knowledge_evidence=row[0],resource_security_labels=row[1]) if row else None

    async def ai_message(self,conversation,generation,body,*,knowledge_evidence=None,requirements=None):
        try:
            ctx=self._context
            from psycopg.types.json import Jsonb
            from airport_assistant.modules.source.labels import SecurityLabels
            if requirements is not None and (type(requirements) is not SecurityLabels or requirements.tenant_id!=ctx.tenant_id):raise ValueError('Same-tenant requirements required.')
            seq=(await(await self._c.execute(self.query('UPDATE {s}.conversations SET last_sequence=last_sequence+1,version=version+1 WHERE tenant_id=%s AND id=%s RETURNING last_sequence'),(ctx.tenant_id,conversation.id))).fetchone())[0]
            column=',knowledge_evidence' if knowledge_evidence is not None else ''
            placeholder=',%s' if knowledge_evidence is not None else ''
            from psycopg.types.json import Jsonb
            parameters=(ctx.tenant_id,uuid4(),conversation.id,body,generation['language'],generation['id'],seq,ctx.session_id,generation['id'],generation['input_version'],generation['generation_epoch'])
            if knowledge_evidence is not None:parameters+= (Jsonb(knowledge_evidence),)
            if requirements is not None:column+=',resource_security_labels';placeholder+=',%s';parameters+=(Jsonb(requirements.to_records()),)
            row=await(await self._c.execute(self.query('''INSERT INTO {s}.messages(tenant_id,id,conversation_id,author_type,source_kind,body,language,client_message_id,sequence,created_session_id,generation_id,basis_conversation_version,basis_epoch'''+column+''')
                VALUES(%s,%s,%s,'ai','ai_response',%s,%s,%s,%s,%s,%s,%s,%s'''+placeholder+''') RETURNING id,sequence'''),parameters)).fetchone()
            return dict(zip(('id','sequence'),row))
        except Error as error:
            if error.sqlstate=='Z0013':raise GenerationExpired() from None
            if error.sqlstate=='Z0028':raise KnowledgeAnswerUnavailable() from None
            raise

    async def published_generation(self,id,message):
        await self._c.execute(self.query("UPDATE {s}.ai_generations SET state='published',message_id=%s WHERE tenant_id=%s AND id=%s AND state='pending'"),(message,self._context.tenant_id,id))
