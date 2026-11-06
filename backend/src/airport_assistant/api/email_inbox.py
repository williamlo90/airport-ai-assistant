"""Raw webhook boundary; no browser session, tenant or recipient override."""
from fastapi import APIRouter,Request,Response
from airport_assistant.modules.identity.auth import AuthError

router=APIRouter(prefix='/integrations/email',tags=['email webhook'])


@router.post('/{alias}/events',status_code=202)
async def receive(alias:str,request:Request,response:Response):
    response.headers['Cache-Control']='no-store'
    if request.query_params:raise AuthError(400,'WEBHOOK_INVALID')
    headers={}
    for name in ('svix-id','svix-timestamp','svix-signature'):
        values=request.headers.getlist(name)
        if len(values)!=1:raise AuthError(401,'WEBHOOK_INVALID')
        headers[name]=values[0]
    if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise AuthError(415,'WEBHOOK_INVALID')
    raw=await request.body()
    if len(raw)>65536:raise AuthError(413,'WEBHOOK_INVALID')
    service=request.app.state.services.email_ingress
    if service is None:raise AuthError(503,'WEBHOOK_UNAVAILABLE')
    await service.receive(alias,raw,headers)
    return {'accepted':True}
