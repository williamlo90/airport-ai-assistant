"""Bounded single-attempt Resend transport; no automatic POST retries."""
import httpx
from airport_assistant.modules.mail.service import SendResult
from airport_assistant.integrations.diagnostics import protect_http_logs
from airport_assistant.modules.mail.delivery import DeliveryEvidence
from airport_assistant.modules.mail.service import address
from airport_assistant.modules.mail.inbox import ReceivedContent,ReceiptContext


class ResendTransport:
    def __init__(self, key, *, client=None):
        protect_http_logs()
        self._key = key
        self._client = client

    async def receive(self,snapshot):
        """Fetch only the fixed receiving API. HTML and sender remain untrusted."""
        import re
        import json
        from email.utils import getaddresses
        id=snapshot['provider_message_id'];target=snapshot['inbound_address']
        if not isinstance(id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id) or not target:return None
        key=self._key.get(snapshot['provider_account_ref']) if isinstance(self._key,dict) else self._key
        if not key:return None
        client=self._client or httpx.AsyncClient(timeout=httpx.Timeout(10),follow_redirects=False,trust_env=False)
        try:
            async with client.stream('GET','https://api.resend.com/emails/receiving/'+id,params={'html_format':'cid'},headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'}) as response:
                if response.status_code!=200:return None
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>262144:return None
                data=json.loads(content)
                if data.get('id')!=id or data.get('object')!='email' or data.get('cc',[]) or data.get('bcc',[]):return None
                to=data.get('to');forwarded=data.get('received_for',[])
                if not isinstance(to,list) or len(to)!=1 or not isinstance(forwarded,list) or len(forwarded)>1:return None
                routes=[address(v) for v in to+forwarded]
                actual=next((v for v in routes if v==target or (v.rsplit('@',1)[1]==target.rsplit('@',1)[1] and v.split('+',1)[0]==target.rsplit('@',1)[0] and re.fullmatch(r'[^@]+\+(?:r-[A-Za-z0-9_-]{32}|v-[a-f0-9]{32})@[^@]+',v))),None)
                if actual is None:return None
                sender=data.get('from')
                if not isinstance(sender,str) or any(c in sender for c in '\r\n\x00'):return None
                parsed=getaddresses([sender])
                if len(parsed)!=1:return None
                sender=address(parsed[0][1])
                subject=data.get('subject','');text=data.get('text');html=data.get('html')
                if not isinstance(subject,str) or len(subject)>998 or any(c in subject for c in '\r\n\x00'):return None
                if any(v is not None and (not isinstance(v,str) or len(v)>65536 or '\x00' in v) for v in (text,html)):return None
                headers=data.get('headers',{})
                if not isinstance(headers,dict) or len(headers)>200:return None
                if any(not isinstance(k,str) for k in headers) or len({k.lower() for k in headers})!=len(headers):return None
                normalized={k.lower():v for k,v in headers.items()}
                def header(name):
                    value=normalized.get(name)
                    if value is None:return None
                    if not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid header.')
                    value=re.sub(r'\r?\n[ \t]+',' ',value)
                    if any(c in value for c in '\r\n\x00'):raise ValueError('Invalid header.')
                    return value
                rfc=data.get('message_id')
                if rfc is not None and (not isinstance(rfc,str) or not re.fullmatch(r'<[^<>\s\x00-\x1f\x7f]{1,996}>',rfc)):return None
                attachments=data.get('attachments',[])
                if not isinstance(attachments,list) or len(attachments)>1000:return None
                auth=data.get('authentication')
                if auth is not None:
                    if not isinstance(auth,dict):return None
                    auth={k:auth.get(k,'unknown') for k in ('spf','dkim','dmarc')}
                    if any(v not in {'pass','fail','gray','processing_failed','unknown'} for v in auth.values()):return None
                return ReceivedContent(sender,target,subject,text,html,rfc,header('in-reply-to'),header('references'),header('reply-to'),len(attachments),auth,actual)
        except Exception:return None
        finally:
            if self._client is None:await client.aclose()

    async def lookup(self, snapshot):
        """GET only. Missing/malformed receipts never prove non-delivery."""
        return await self._sent(snapshot,context_only=False)

    async def context(self,snapshot):
        """Authenticate receipt/envelope independently of its latest tracking event."""
        return await self._sent(snapshot,context_only=True)

    async def _sent(self,snapshot,*,context_only):
        import re
        import json
        from email.utils import getaddresses
        receipt=snapshot['provider_message_id']
        if not isinstance(receipt,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',receipt):return None
        key=self._key.get(snapshot['provider_account_ref']) if isinstance(self._key,dict) else self._key
        if not key:return None
        client=self._client or httpx.AsyncClient(timeout=httpx.Timeout(10),follow_redirects=False,trust_env=False)
        try:
            async with client.stream('GET','https://api.resend.com/emails/'+receipt,headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'}) as response:
                if response.status_code!=200:return None
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>65536:return None
                data=json.loads(content)
                if data.get('id')!=receipt or data.get('object')!='email' or data.get('cc',[]) or data.get('bcc',[]):return None
                sender=data.get('from')
                if not isinstance(sender,str) or any(c in sender for c in '\r\n\x00'):return None
                parsed=getaddresses([sender])
                if len(parsed)!=1 or address(parsed[0][1])!=snapshot['sender']:return None
                if not isinstance(data.get('to'),list) or len(data['to'])!=1 or address(data['to'][0])!=snapshot['recipient']:return None
                if context_only:return ReceiptContext(receipt,data.get('message_id'))
                states={'sent':'accepted','delivery_delayed':'accepted','delivered':'delivered','bounced':'bounced','failed':'failed','suppressed':'failed'}
                state=states.get(data.get('last_event'))
                if state is None:return None
                # created_at describes the email, not the last_event timestamp.
                return DeliveryEvidence(receipt,state,data.get('message_id'))
        except Exception:return None
        finally:
            if self._client is None:await client.aclose()

    async def send(self, envelope):
        key = self._key.get(envelope.provider_account_ref) if isinstance(self._key,dict) else self._key
        if not key:
            return SendResult('failed')
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10), follow_redirects=False, trust_env=False)
        try:
            async with client.stream('POST', 'https://api.resend.com/emails', headers={
                'Authorization': 'Bearer ' + key, 'Idempotency-Key': envelope.delivery_key,
            }, json={'from': envelope.sender, 'to': [envelope.recipient], 'reply_to': envelope.reply_to,
                     'subject': envelope.subject, 'text': envelope.text,
                     'headers': {'Message-ID': envelope.rfc_message_id}}) as response:
                # Do not log provider bodies, addresses, credentials or exceptions.
                if response.status_code in {400, 401, 403, 404, 422}:
                    return SendResult('failed')
                if response.status_code != 200:
                    return SendResult('delivery_unknown')
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 4096:
                        return SendResult('delivery_unknown')
                import json
                payload = json.loads(chunks)
                return SendResult('accepted', payload['id'])
        except Exception:
            return SendResult('delivery_unknown')
        finally:
            if self._client is None:
                await client.aclose()
