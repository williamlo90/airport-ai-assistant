"""Bounded asynchronous source GETs. No redirects, proxies, retries or writes."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from uuid import uuid4
import httpx
from airport_assistant.modules.source.models import SourceCaller,SourceFailure,Booking,Transaction,Policy,TransactionPage,RelationPage


class RedactHttpLog(logging.Filter):
    def filter(self,record):
        record.msg='source_http_event (details redacted)';record.args=();record.exc_info=None;record.exc_text=None;record.stack_info=None
        return True


def protect_logs():
    for name in ('httpx','httpcore','httpcore.connection','httpcore.connection_pool','httpcore.http11','httpcore.http2','httpcore.proxy','httpcore.socks'):
        logger=logging.getLogger(name)
        if not any(isinstance(f,RedactHttpLog) for f in logger.filters):logger.addFilter(RedactHttpLog())


class SourceHttpClient:
    def __init__(self,base_url,token,context_secret,*,timeout=5,max_response_bytes=262144,transport=None):
        protect_logs()
        self._base=httpx.URL(base_url);self._token=token;self._secret=context_secret
        self._timeout=timeout;self._max=max_response_bytes
        self._client=httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(2,connect=2,pool=1),
                                      limits=httpx.Limits(max_connections=4,max_keepalive_connections=2),transport=transport)

    async def aclose(self):await self._client.aclose()

    def _headers(self,caller,url):
        now=int(time.time())
        claims=dict(v=1,aud='airport-reference-v1',tenant_id=str(caller.tenant_id),subject=caller.subject,iat=now,exp=now+60,nonce=str(uuid4()))
        context=base64.urlsafe_b64encode(json.dumps(claims,separators=(',',':')).encode()).rstrip(b'=').decode()
        raw_path=url.raw_path.partition(b'?')[0]
        message=b'source-v1\nGET\n'+raw_path+b'\n'+url.query+b'\n'+context.encode()
        signature=hmac.new(self._secret.encode(),message,hashlib.sha256).hexdigest()
        return {'Authorization':'Bearer '+self._token,'X-Source-Context':context,'X-Source-Signature':signature,'Accept':'application/json','Accept-Encoding':'identity'}

    async def _get(self,caller,path,params=None):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',caller.subject):raise SourceFailure(503)
        url=self._base.copy_with(path=path,query=str(httpx.QueryParams(params)).encode()) if params else self._base.copy_with(path=path)
        self._client.cookies.clear()
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream('GET',url,headers=self._headers(caller,url)) as response:
                    self._client.cookies.clear()
                    if response.headers.get_list('set-cookie'):raise SourceFailure(502)
                    if response.status_code==404:raise SourceFailure(404)
                    if response.status_code in {401,403,408,429} or response.status_code>=500:raise SourceFailure(503)
                    if response.status_code==400:raise SourceFailure(400)
                    if response.status_code!=200:raise SourceFailure(502)
                    if response.headers.get('content-type','').split(';')[0].strip().lower()!='application/json' or response.headers.get('content-encoding','identity').lower()!='identity':raise SourceFailure(502)
                    lengths=response.headers.get_list('content-length')
                    if lengths and (len(lengths)!=1 or not lengths[0].isdigit() or len(lengths[0])>12 or int(lengths[0])>self._max):raise SourceFailure(502)
                    body=bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body)+len(chunk)>self._max:raise SourceFailure(502)
                        body.extend(chunk)
                    if lengths and len(body)!=int(lengths[0]):raise SourceFailure(502)
                    def unique(pairs):
                        result={}
                        for key,value in pairs:
                            if key in result:raise ValueError('Duplicate source key.')
                            result[key]=value
                        return result
                    value=json.loads(body.decode('utf-8'),object_pairs_hook=unique)
                    if not isinstance(value,dict):raise ValueError('Source object required.')
                    return value
        except SourceFailure:raise
        except httpx.RemoteProtocolError:raise SourceFailure(502) from None
        except (httpx.TransportError,TimeoutError):raise SourceFailure(503) from None
        except (ValueError,UnicodeError,RecursionError):raise SourceFailure(502) from None

    def _ref(self,reference):
        if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',reference):raise SourceFailure(404)
        return reference

    def _parse(self,model,value,reference=None):
        try:
            if model in {Booking,Transaction}:
                known={'confirmed','cancelled','pending'} if model is Booking else {'pending','paid','failed','refunded'}
                raw=value.get('status')
                if not isinstance(raw,str) or not re.fullmatch(r'[a-z0-9_]{1,64}',raw):raise ValueError('Invalid source status.')
                if raw not in known|{'unknown'}:
                    value={**value,'status':'unknown','source_status_label':raw}
                if model is Transaction and 'refund_status' in value:
                    refund=value['refund_status']
                    if not isinstance(refund,str) or not re.fullmatch(r'[a-z0-9_]{1,64}',refund):raise ValueError('Invalid refund status.')
                    if refund not in {'none','pending','completed','unknown'}:value={**value,'refund_status':'unknown'}
            result=model.model_validate_json(json.dumps(value))
            if reference is not None and result.reference!=reference:raise ValueError('Mismatched source reference.')
            return result
        except Exception:raise SourceFailure(502) from None

    def _page_args(self,limit,cursor):
        if type(limit) is not int or not 1<=limit<=100 or (cursor is not None and (not isinstance(cursor,str) or not 1<=len(cursor)<=2048)):raise SourceFailure(400)
        return {'limit':limit,**({'cursor':cursor} if cursor is not None else {})}

    async def booking(self,caller,reference):return self._parse(Booking,await self._get(caller,'/source/v1/bookings/'+self._ref(reference)),reference)
    async def transaction(self,caller,reference):return self._parse(Transaction,await self._get(caller,'/source/v1/transactions/'+self._ref(reference)),reference)
    async def policy(self,caller,reference):return self._parse(Policy,await self._get(caller,'/source/v1/policies/'+self._ref(reference)),reference)
    async def booking_transactions(self,caller,reference,limit=20,cursor=None):
        value=await self._get(caller,'/source/v1/bookings/'+self._ref(reference)+'/transactions',self._page_args(limit,cursor))
        try:
            value={**value,'items':[self._parse(Transaction,item).model_dump(mode='json') for item in value['items']]}
            page=self._parse(TransactionPage,value)
            if len(page.items)>limit or len({i.reference for i in page.items})!=len(page.items) or any(i.booking_reference is not None and i.booking_reference!=reference for i in page.items):raise ValueError()
            return page
        except Exception:raise SourceFailure(502) from None
    async def relations(self,caller,limit=20,cursor=None):
        value=await self._get(caller,'/source/v1/subjects/me/relations',self._page_args(limit,cursor))
        page=self._parse(RelationPage,value)
        if len(page.items)>limit or len({(i.resource_type,i.resource_reference) for i in page.items})!=len(page.items):raise SourceFailure(502)
        return page
