"""Email-only verification and explicit continuation; never infer identity from subject."""
import base64
from dataclasses import replace
import hashlib,hmac,re
from html.parser import HTMLParser
from uuid import UUID,uuid4
from airport_assistant.modules.jobs.service import Outcome
from airport_assistant.modules.mail.service import Envelope,SendResult


def code(secret,id):return str(int.from_bytes(hmac.new(secret,b'mail-code:'+id.bytes,hashlib.sha256).digest()[:8],'big')%1000000).zfill(6)
def digest(secret,id,value):return hmac.new(secret,b'mail-code-hash:'+id.bytes+value.encode(),hashlib.sha256).hexdigest()
def thread_token(secret,tenant,id):return base64.urlsafe_b64encode(hmac.new(secret,b'mail-thread:'+tenant.bytes+id.bytes,hashlib.sha256).digest()[:24]).rstrip(b'=').decode()


def legacy_public(snapshot,tenant_id):
    """Legacy capabilities are trusted empty-only; supplied metadata may not weaken that."""
    from airport_assistant.modules.source.private_content import inherited_records
    from .private_replies import GROUPS
    if type(snapshot) is not dict:return False
    version=snapshot.get('classification_version',1)
    if type(version) is not int or version not in {1,2}:return False
    if version==2 and any(key not in snapshot for key in ('labels',*GROUPS)):return False
    groups=[snapshot[key] for key in ('labels',*GROUPS) if key in snapshot]
    try:return not groups or inherited_records(tenant_id,*groups)==[]
    except Exception:return False


def text_body(text,html):
    if text is not None and text.strip():return text
    class Plain(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts=[];self.stack=[];self.stopped=False
        def handle_starttag(self,tag,attrs):
            attributes=dict(attrs)
            quoted=any('quote' in str(attributes.get(key,'')).lower() for key in ('class','id'))
            if attributes.get('id','').lower()=='divrplyfwdmsg':self.stopped=True
            hidden=tag in {'script','style','blockquote'} or quoted or (self.stack and self.stack[-1][1])
            if not self.stopped and not hidden and tag in {'p','div','br','li'}:self.parts.append('\n')
            if tag not in {'br','hr','img','meta','link','input','wbr'}:self.stack.append((tag,bool(hidden)))
        def handle_endtag(self,tag):
            for index in range(len(self.stack)-1,-1,-1):
                if self.stack[index][0]==tag:
                    del self.stack[index:];break
        def handle_data(self,value):
            if not self.stopped and not (self.stack and self.stack[-1][1]):self.parts.append(value)
    parser=Plain();parser.feed(html or '');return ''.join(parser.parts).strip()


class ReplyTransport:
    """Control codes are digested/redacted before inbox body persistence."""
    def __init__(self,transport,secret):self._transport,self._secret=transport,secret
    async def receive(self,snapshot):
        content=await self._transport.receive(snapshot)
        if content is None:return None
        first=text_body(content.text,content.html).strip().split('\n',1)[0].strip()
        match=re.fullmatch(r'VERIFY ([a-fA-F0-9-]{36}) ([0-9]{6})',first)
        if match:
            try:id=UUID(match[1])
            except ValueError:return content
            return replace(content,text='Verification response (code redacted).',html=None,verification_challenge_id=str(id),verification_response_hash=digest(self._secret,id,match[2]))
        # Redact quoted control secrets too, without treating them as commands.
        scrub=lambda value: re.sub(r'(VERIFY\s+[a-fA-F0-9-]{36}\s+)[0-9]{6}',r'\1[redacted]',value,flags=re.I) if value else value
        return replace(content,text=scrub(text_body(content.text,content.html)),html=None)
    async def send(self,envelope):return await self._transport.send(envelope)
    async def lookup(self,snapshot):return await self._transport.lookup(snapshot)
    async def context(self,snapshot):return await self._transport.context(snapshot)


class ReplyWorker:
    def __init__(self,repository,secret,*,private_replies=False,private_access=None):
        if type(private_replies) is not bool:raise ValueError('Explicit private reply switch required.')
        self._repository,self._secret=repository,secret
        self._private_replies=None
        if private_replies:
            from .private_replies import PrivateReplies
            self._private_replies=PrivateReplies(repository,private_access)
    async def __call__(self,lease):
        if self._private_replies is not None:return await self._private_replies(lease)
        snap=await self._repository.snapshot(lease)
        if not snap:return Outcome('blocked','BLOCKED')
        if not legacy_public(snap,lease.tenant_id):return Outcome('blocked','BLOCKED')
        if snap['state'] in {'published','review','rejected','verification_reply','challenged'}:return Outcome('succeeded','OK')
        if snap.get('verification_challenge_id'):
            ok=await self._repository.apply(lease,'verify','',None,None)
        elif not snap['eligible']:
            ok=await self._repository.apply(lease,'reject','',None,None)
        else:
            body=text_body(snap['body'],snap.get('body_html'))
            first=body.strip().split('\n',1)[0].strip()
            if not 1<=len(body)<=4000 or not body.strip() or '\x00' in body or first.upper().startswith('VERIFY '):
                ok=await self._repository.apply(lease,'review','',None,None)
            elif not (isinstance(snap['authentication'],dict) and snap['authentication'].get('dkim')=='pass' and snap['authentication'].get('dmarc')=='pass'):
                id=uuid4();ok=await self._repository.apply(lease,'challenge',body,id,digest(self._secret,id,code(self._secret,id)))
            else:ok=await self._repository.apply(lease,'publish',body,None,None)
        return Outcome('succeeded','OK') if ok else Outcome('retry','RETRYABLE')


class ChallengeWorker:
    def __init__(self,repository,transport,secret):self._repository,self._transport,self._secret=repository,transport,secret
    async def __call__(self,lease):
        snap=await self._repository.challenge(lease)
        if not snap:return Outcome('blocked','BLOCKED')
        if not legacy_public(snap,lease.tenant_id):return Outcome('blocked','BLOCKED')
        if snap['send_state']!='queued':return Outcome('succeeded','OK')
        id=UUID(snap['id']);value=code(self._secret,id)
        if not hmac.compare_digest(digest(self._secret,id,value),snap['code_hash']):return Outcome('blocked','BLOCKED')
        if not await self._repository.reserve(lease):return Outcome('blocked','BLOCKED')
        local,domain=snap['reply_address'].rsplit('@',1)
        envelope=Envelope(id,UUID(snap['app_message_id']),UUID(snap['account_id']),snap['sender'],snap['recipient'],local+'+v-'+id.hex+'@'+domain,
            '<'+id.hex+'@'+domain+'>','mail/'+lease.tenant_id.hex+'/'+id.hex,'Verify your email reply',
            'To verify your reply, send this as the first line of your response within 5 minutes:\nVERIFY '+str(id)+' '+value+'\nIf you did not send a reply, ignore this email.',snap['provider_account_ref'])
        try:result=await self._transport.send(envelope)
        except Exception:result=SendResult('delivery_unknown')
        if type(result) is not SendResult:result=SendResult('delivery_unknown')
        ok=await self._repository.sent(lease,result)
        if not ok:return Outcome('unknown','UNKNOWN')
        return {'accepted':Outcome('succeeded','OK'),'failed':Outcome('dead','PERMANENT'),'delivery_unknown':Outcome('unknown','UNKNOWN')}[result.state]
