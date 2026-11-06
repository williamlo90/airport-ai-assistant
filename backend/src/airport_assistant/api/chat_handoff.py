"""Explicit staff actions; accept returns an immutable operation receipt."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel
from airport_assistant.api.dependencies import get_staff_identity,get_mutation_identity
from airport_assistant.modules.conversations.handoff import AcceptChat
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1/staff/conversations',tags=['staff conversation handoff'])


class ChatView(BaseModel):
    conversation_id:UUID
    case_id:UUID
    case_version:int
    version:int
    generation_epoch:int
    mode:Literal['waiting_staff','human','ended']
    handler_membership_id:UUID|None


class AcceptReceipt(ChatView):
    state:Literal['accepted']
    mode:Literal['human']
    handler_membership_id:UUID


def service(request,response):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.chat_handoff
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return result


@router.get('/{id}',response_model=ChatView)
async def get(id:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    return await service(request,response).get(identity,id)


@router.post('/{id}/accept',response_model=AcceptReceipt)
async def accept(id:UUID,body:AcceptChat,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    handler=service(request,response)
    keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    return await handler.accept(identity,id,body,keys[0])
