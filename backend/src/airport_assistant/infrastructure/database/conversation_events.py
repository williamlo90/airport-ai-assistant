"""Current ACL-backed public event reads; no internal events or source snapshots."""
from airport_assistant.infrastructure.database.case_lifecycle import PostgresCaseLifecycle,LifecycleSQL


class PostgresConversationEvents(PostgresCaseLifecycle):
    def unit(self,connection,context):return EventSQL(connection,self._schema,context)


class EventSQL(LifecycleSQL):
    async def event_conversation(self,id):
        fields='last_event_sequence,mode,generation_epoch,version,resource_security_labels,case_id'
        row=await(await self._c.execute(self.query('SELECT '+fields+' FROM {s}.conversations WHERE tenant_id=%s AND id=%s'),(self._context.tenant_id,id))).fetchone()
        if row is None:return None
        result=dict(zip(fields.split(','),row))
        result.update(case_security_labels=[],case_version=None)
        if result['case_id'] is not None:
            parent=await(await self._c.execute(self.query('SELECT resource_security_labels,version FROM {s}.cases WHERE tenant_id=%s AND id=%s'),
                (self._context.tenant_id,result['case_id']))).fetchone()
            if parent is None:return None
            result.update(case_security_labels=parent[0],case_version=parent[1])
        return result

    async def events(self,id,after,upper,limit):
        rows=await(await self._c.execute(self.query('''SELECT e.id,e.sequence,e.event_type,e.created_at,e.message_id,e.language,e.case_id,m.body,coalesce(to_jsonb(e)->>'conversation_mode',CASE WHEN e.event_type='handoff_requested' THEN 'waiting_staff' END),m.author_type
            FROM {s}.conversation_events e LEFT JOIN {s}.messages m ON(m.tenant_id,m.id)=(e.tenant_id,e.message_id)
            WHERE e.tenant_id=%s AND e.conversation_id=%s AND e.sequence>%s AND e.sequence<=%s ORDER BY e.sequence LIMIT %s'''),
            (self._context.tenant_id,id,after,upper,limit))).fetchall()
        result=[]
        for event,sequence,kind,created,message,language,case,body,mode,author in rows:
            payload={'message_id':message,'body':body,'language':language,'author_type':author} if message is not None else {'language':language} if kind=='language_changed' else {'case_id':case,'mode':mode}
            result.append(dict(id=event,sequence=sequence,event_type=kind,created_at=created,payload=payload))
        return result
