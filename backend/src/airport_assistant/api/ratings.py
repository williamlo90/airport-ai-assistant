"""Explicit optional feedback, separate from service resolution or AI tools."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel
from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.cases.ratings import RatingInput
from airport_assistant.modules.drafts.models import DraftError

router=APIRouter(prefix='/api/v1',tags=['customer feedback'])


class RatingView(BaseModel):
    id:UUID
    score:int
    comment:str|None
    version:int
    created_at:datetime
    updated_at:datetime


class FeedbackView(BaseModel):
    target_type:Literal['case','conversation']
    target_id:UUID
    eligible:bool
    rating:RatingView|None


def service(request,response):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(key) for key in
        ('x-tenant-id','x-principal-id','x-session-id','x-subject-id','x-source-subject','x-source-context','x-source-signature')):
        raise DraftError(400,'BAD_REQUEST')
    result=request.app.state.services.ratings
    if result is None:raise DraftError(503,'CASE_UNAVAILABLE')
    return result


@router.get('/cases/{id}/ratings',response_model=FeedbackView)
async def case_rating(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return await service(request,response).get(identity,'case',id)


@router.post('/cases/{id}/ratings',response_model=FeedbackView)
async def save_case_rating(id:UUID,body:RatingInput,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).save(identity,'case',id,body)


@router.get('/conversations/{id}/ratings',response_model=FeedbackView)
async def conversation_rating(id:UUID,request:Request,response:Response,identity:AccessIdentity=Depends(get_identity)):
    return await service(request,response).get(identity,'conversation',id)


@router.post('/conversations/{id}/ratings',response_model=FeedbackView)
async def save_conversation_rating(id:UUID,body:RatingInput,request:Request,response:Response,identity:AccessIdentity=Depends(get_mutation_identity)):
    return await service(request,response).save(identity,'conversation',id,body)
