"""Bounded read-only transport and value-free error responses."""

import logging
from uuid import UUID,uuid4
from starlette.responses import JSONResponse

ERRORS={400:("BAD_REQUEST","Permintaan tidak valid.",False),401:("AUTH_REQUIRED","Identitas service dan pengguna diperlukan.",False),403:("ACTION_FORBIDDEN","Tindakan tidak diizinkan.",False),404:("RESOURCE_NOT_FOUND","Data tidak ditemukan.",False),405:("METHOD_NOT_ALLOWED","Metode tidak diizinkan.",False),413:("PAYLOAD_TOO_LARGE","Permintaan terlalu besar.",False),422:("VALIDATION_ERROR","Format permintaan tidak valid.",False),500:("INTERNAL_ERROR","Permintaan belum dapat diproses.",False),503:("SOURCE_UNAVAILABLE","Sumber belum dapat diperiksa.",True)}


def error_response(status,request_id,headers=None):
    code,message,retryable=ERRORS.get(status,ERRORS[500])
    return JSONResponse({"error":dict(code=code,message=message,request_id=request_id,retryable=retryable)},status_code=status,headers={"Cache-Control":"no-store",**(headers or {})})


class Boundary:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        values=[v for k,v in scope['headers'] if k.lower()==b'x-request-id']
        try:request_id=str(UUID(values[0].decode('ascii'))) if len(values)==1 and len(values[0])==36 else str(uuid4())
        except (ValueError,UnicodeError):request_id=str(uuid4())
        scope.setdefault('state',{})['request_id']=request_id
        started=False
        async def wrapped(message):
            nonlocal started
            if message['type']=='http.response.start':
                started=True
                message={**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower() not in {b'x-request-id',b'cache-control'}]+[(b'x-request-id',request_id.encode()),(b'cache-control',b'no-store')]}
            await send(message)
        try:
            lengths=[v for k,v in scope['headers'] if k.lower()==b'content-length']
            if lengths and (len(lengths)!=1 or not lengths[0].isdigit() or len(lengths[0])>20):return await error_response(400,request_id)(scope,receive,wrapped)
            if lengths and int(lengths[0])>1024:return await error_response(413,request_id)(scope,receive,wrapped)
            body=bytearray()
            while True:
                message=await receive()
                if message['type']=='http.disconnect':return
                body.extend(message.get('body',b''))
                if len(body)>1024:return await error_response(413,request_id)(scope,receive,wrapped)
                if not message.get('more_body',False):break
            if lengths and int(lengths[0])!=len(body):return await error_response(400,request_id)(scope,receive,wrapped)
            if body:return await error_response(400,request_id)(scope,receive,wrapped)
            async def replay():return {'type':'http.request','body':b'','more_body':False}
            await self.app(scope,replay,wrapped)
        except Exception:
            logging.getLogger('airport_reference').error('request_failed request_id=%s',request_id)
            if started:raise RuntimeError('Reference response interrupted.') from None
            await error_response(500,request_id)(scope,receive,wrapped)
