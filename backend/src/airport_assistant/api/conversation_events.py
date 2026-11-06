"""Bounded polling; current session and parent ACL are checked on every request."""
from uuid import UUID
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict
from fastapi import APIRouter,Depends,Request,Response,Query
from airport_assistant.api.dependencies import get_identity
from airport_assistant.modules.conversations.service import ConversationError

router=APIRouter(prefix='/api/v1/conversations',tags=['conversation events'])


class MessagePayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message_id:UUID
    body:str
    language:Literal['id','en']
    author_type:Literal['customer','staff','ai']


class LanguagePayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    language:Literal['id','en']


class HandoffPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    case_id:UUID
    mode:Literal['waiting_staff','human','ended']


class EventView(BaseModel):
    id:UUID
    sequence:int
    event_type:Literal['message_received','language_changed','handoff_requested','chat_accepted','chat_reassigned','chat_ended','chat_updated','staff_message','ai_message']
    created_at:datetime
    payload:MessagePayload|LanguagePayload|HandoffPayload


class PollView(BaseModel):
    items:list[EventView]
    next_cursor:str
    has_more:bool
    mode:Literal['ai','waiting_staff','human','ended']
    generation_epoch:int
    version:int


@router.get('/{id}/events',response_model=PollView)
async def events(id:UUID,request:Request,response:Response,after:str|None=Query(default=None,max_length=2048),limit:int=Query(default=20,ge=1,le=100),identity=Depends(get_identity)):
    if set(request.query_params)-{'after','limit'} or any(len(request.query_params.getlist(k))>1 for k in ('after','limit')) or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-session-id','x-subject-id','x-source-subject','x-source-context','x-source-signature')):
        raise ConversationError(400,'BAD_REQUEST')
    service=request.app.state.services.conversation_events
    if service is None:raise ConversationError(503,'CONVERSATION_UNAVAILABLE')
    response.headers['Cache-Control']='no-store'
    return await service.poll(identity,id,after,limit)
