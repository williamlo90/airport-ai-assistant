"""Customer read endpoints. No client identity or source destination overrides."""

from typing import Annotated
from fastapi import APIRouter,Depends,Path,Query,Request,Response
from airport_assistant.api.dependencies import get_customer_identity
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.source.models import SourceFailure,Booking,Transaction,TransactionPage
from airport_assistant.api.schemas import ErrorResponse

router=APIRouter(prefix='/api/v1',tags=['source reads'],responses={s:{'model':ErrorResponse} for s in (400,401,404,502,503)})
Reference=Annotated[str,Path(pattern=r'^[A-Za-z0-9_-]{1,80}$')]


def service(request,allowed=()):
    keys=[k for k,v in request.query_params.multi_items()]
    if len(set(keys))!=len(keys) or set(keys)-set(allowed) or any(request.headers.getlist(k) for k in ('x-tenant-id','x-subject-id','x-principal-id','x-source-subject','x-source-context','x-source-signature')):raise SourceFailure(400)
    value=request.app.state.services.source
    if value is None:raise SourceFailure(503)
    return value


@router.get('/bookings/{reference}',response_model=Booking,response_model_exclude_none=True)
async def booking(reference:Reference,request:Request,response:Response,identity:AccessIdentity=Depends(get_customer_identity)):
    response.headers['Cache-Control']='no-store'
    return await service(request).booking(identity,reference)


@router.get('/transactions/{reference}',response_model=Transaction)
async def transaction(reference:Reference,request:Request,response:Response,identity:AccessIdentity=Depends(get_customer_identity)):
    response.headers['Cache-Control']='no-store'
    return await service(request).transaction(identity,reference)


@router.get('/bookings/{reference}/transactions',response_model=TransactionPage)
async def transactions(reference:Reference,request:Request,response:Response,limit:Annotated[int,Query(ge=1,le=100)]=20,
                       cursor:Annotated[str|None,Query(min_length=1,max_length=2048)]=None,identity:AccessIdentity=Depends(get_customer_identity)):
    response.headers['Cache-Control']='no-store'
    return await service(request,{'limit','cursor'}).booking_transactions(identity,reference,limit,cursor)
