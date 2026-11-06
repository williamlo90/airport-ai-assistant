"""Signed session-bound polling cursors; authorization is still checked per read."""
import base64
import hashlib
import hmac
import json
import time
from airport_assistant.modules.conversations.service import ConversationError
from airport_assistant.modules.cases.lifecycle import lifecycle_transaction
from airport_assistant.modules.identity.auth import AuthError


class EventCursor:
    def __init__(self,key:bytes):
        self._key=hmac.digest(key,b'conversation-event-cursor-v1','sha256')

    @staticmethod
    def scope(identity,id,limit):
        s=identity.session
        return [str(s.tenant_id),str(s.id),str(s.principal_id) if s.principal_id else None,s.kind,str(id),limit,'customer']

    def issue(self,scope,position):
        raw=json.dumps([scope,position,int(time.time())+900],separators=(',',':')).encode()
        return base64.urlsafe_b64encode(raw+hmac.digest(self._key,raw,'sha256')).decode().rstrip('=')

    def read(self,value,scope):
        try:
            if type(value) is not str or not 1<=len(value)<=2048:raise ValueError()
            data=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
            raw,signature=data[:-32],data[-32:]
            if not hmac.compare_digest(signature,hmac.digest(self._key,raw,'sha256')):raise ValueError()
            bound,position,expires=json.loads(raw)
            if bound!=scope or type(position) is not int or position<0 or type(expires) is not int or expires<=time.time():raise ValueError()
            return position
        except (ValueError,TypeError,UnicodeError):raise ConversationError(400,'BAD_REQUEST') from None


class ConversationEvents:
    def __init__(self,repository,cursors,private_access=None):
        from airport_assistant.modules.source.private_content import PrivateContentGate
        self._repository,self._cursors=repository,cursors
        self._private_content=PrivateContentGate(private_access)

    async def poll(self,identity,id,after=None,limit=20):
        if type(limit) is not int or not 1<=limit<=100:raise ConversationError(422,'VALIDATION_ERROR')
        scope=self._cursors.scope(identity,id,limit)
        from airport_assistant.modules.source.private_content import PrivateProjection,inherited_records
        async def load(check=None):
            async with lifecycle_transaction(self._repository,identity) as tx:
                conversation=await tx.event_conversation(id)
                if conversation is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                labels=inherited_records(identity.session.tenant_id,
                    conversation.get('resource_security_labels'),conversation.get('case_security_labels'))
                candidate=PrivateProjection.capture(identity.session.tenant_id,id,value=None,
                    basis=dict(conversation=conversation,scope=scope),records=labels)
                value=None
                if check is not None:
                    check(candidate)
                    # Object AND source access precede cursor validation. Invalid
                    # cursors cannot reveal an otherwise source-denied aggregate.
                    position=self._cursors.read(after,scope) if after is not None else 0
                    if position>conversation['last_event_sequence']:raise ConversationError(400,'BAD_REQUEST')
                    rows=await tx.events(id,position,conversation['last_event_sequence'],limit+1)
                    selected=rows[:limit]
                    next_position=selected[-1]['sequence'] if selected else position
                    value=dict(items=selected,next_cursor=self._cursors.issue(scope,next_position),has_more=len(rows)>limit,
                        mode=conversation['mode'],generation_epoch=conversation['generation_epoch'],version=conversation['version'])
                    # A payload query/lock can await while a parent, labels or
                    # object ACL changes. Re-read that basis AFTER the await;
                    # rechecking the pre-query Python dict would prove nothing.
                    current=await tx.event_conversation(id)
                    if current is None:raise AuthError(404,'RESOURCE_NOT_FOUND')
                    labels=inherited_records(identity.session.tenant_id,
                        current.get('resource_security_labels'),current.get('case_security_labels'))
                    result=PrivateProjection.capture(identity.session.tenant_id,id,value=value,
                        basis=dict(conversation=current,scope=scope),records=labels)
                    check(result)
                    return result
                return PrivateProjection.capture(identity.session.tenant_id,id,value=value,
                    basis=dict(conversation=conversation,scope=scope),records=labels)
        return await self._private_content.read(identity,load,
            materialize=load,purpose='staff' if identity.session.kind=='staff' else 'customer')
