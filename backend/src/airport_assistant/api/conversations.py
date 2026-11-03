"""Standalone conversation HTTP boundary; no public owner or actor claims."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from airport_assistant.api.dependencies import get_identity, get_mutation_identity
from airport_assistant.api.schemas import RequestModel, ErrorResponse
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.conversations.service import ConversationError

router=APIRouter(prefix='/api/v1/conversations',tags=['conversations'],
                 responses={status:{'model':ErrorResponse} for status in (400,401,403,404,409,422,503)})


class CreateConversation(RequestModel):
    language: Literal['id','en']


class SendMessage(RequestModel):
    body: str = Field(min_length=1,max_length=4000)
    client_message_id: UUID = Field(strict=False)


class ChangeLanguage(CreateConversation):
    expected_version: int = Field(ge=1,strict=True)


class ConversationView(BaseModel):
    conversation_id: UUID
    language: Literal['id','en']
    version: int
    generation_epoch: int
    last_sequence: int


class MessageAccepted(BaseModel):
    message_id: UUID
    sequence: int
    processing_state: Literal['received'] = 'received'


def service(request,response):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(key) for key in
        ('x-tenant-id','x-principal-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):
        raise ConversationError(400,'BAD_REQUEST')
    result=request.app.state.services.conversations
    if result is None:raise ConversationError(503,'CONVERSATION_UNAVAILABLE')
    return result


def view(value):
    return ConversationView(conversation_id=value.id,language=value.language,version=value.version,
                            generation_epoch=value.generation_epoch,last_sequence=value.last_sequence)


@router.post('',status_code=201,response_model=ConversationView)
async def create(body:CreateConversation,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return view(await service(request,response).create(identity,body.language))


@router.get('/{id}',response_model=ConversationView)
async def get(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return view(await service(request,response).get(identity,id))


@router.post('/{id}/messages',status_code=201,response_model=MessageAccepted)
async def append(id:UUID,body:SendMessage,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    result=await service(request,response).append(identity,id,body.client_message_id,body.body)
    return MessageAccepted(message_id=result.id,sequence=result.sequence)


@router.patch('/{id}/language',response_model=ConversationView)
async def language(id:UUID,body:ChangeLanguage,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return view(await service(request,response).change_language(identity,id,body.language,body.expected_version))
