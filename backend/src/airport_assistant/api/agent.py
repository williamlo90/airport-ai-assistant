"""Customer workflows and explicit human approval of staff translation previews."""
from datetime import datetime,timezone
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Request,Response
from pydantic import BaseModel,Field,model_validator
from airport_assistant.api.schemas import RequestModel
from airport_assistant.api.dependencies import get_mutation_identity,get_staff_identity,get_identity
from airport_assistant.modules.agent.workflow import RunRequest
from airport_assistant.modules.agent.translation import TranslationRequest,TranslationApproval
from airport_assistant.modules.agent.case_translation import CaseTranslationRequest,CaseEmailTranslations
from airport_assistant.modules.agent.models import AgentError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.api.staff_messages import SavedMessage

router=APIRouter(prefix='/api/v1',tags=['agent workflows'])

class BeginRun(RequestModel):
    message_id:UUID=Field(strict=False)
    request_key:UUID=Field(strict=False)
    purpose:Literal['assist','handoff','answer']='assist'
    service_type:Literal['general','parking','lounge']='general'
    booking_references:list[Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]]=Field(default_factory=list,max_length=1)
    transaction_references:list[Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]]=Field(default_factory=list,max_length=1)

    @model_validator(mode='after')
    def private_assistance_only(self):
        if self.purpose!='assist' and (self.booking_references or self.transaction_references):
            raise ValueError('Source references require assistance mode.')
        return self

class RunStarted(BaseModel):
    run_id:UUID
    state:Literal['prepared']='prepared'

class RunOutcome(BaseModel):
    run_id:UUID
    state:Literal['published','discarded','draft','review']
    message_id:UUID|None=None
    sequence:int|None=None
    draft_id:UUID|None=None
    reason:str|None=None

class RunStatus(RunOutcome):
    state:Literal['published','discarded','draft','review','unresolved']

class AgentCapabilities(BaseModel):
    private_assistance:bool

class TranslationView(BaseModel):
    id:UUID
    case_id:UUID
    conversation_id:UUID|None
    original_body:str
    source_language:Literal['en','id']
    target_language:Literal['en','id']
    channel:Literal['chat','email']
    state:Literal['generating','ready','failed','sent']
    translated_body:str|None
    reviewed_body:str|None
    message_id:UUID|None
    revision:int
    expires_at:datetime

class CitationView(BaseModel):
    version_id:UUID
    generation:UUID
    ordinal:int
    basis_hash:str
    document_id:UUID
    document_revision:int
    quote:str
    source_uri:str

class CitationReceipt(BaseModel):
    message_id:UUID
    status:Literal['historical_publication']
    issued_at:datetime|None
    citations:list[CitationView]

def service(request,response,name):
    response.headers['Cache-Control']='no-store'
    if request.query_params or any(request.headers.getlist(k) for k in ('x-tenant-id','x-principal-id','x-subject-id','x-session-id','x-source-subject','x-source-context','x-source-signature')):raise DraftError(400,'BAD_REQUEST')
    value=getattr(request.app.state.services,name)
    if value is None:raise AgentError(503,'AGENT_UNAVAILABLE')
    return value

@router.get('/agent-capabilities',response_model=AgentCapabilities)
async def capabilities(request:Request,response:Response,identity=Depends(get_identity)):
    delivery=service(request,response,'agent_delivery')
    return AgentCapabilities(private_assistance=identity.session.kind=='customer' and delivery.private_enabled)

@router.post('/conversations/{id}/agent-runs',response_model=RunStarted,status_code=201)
async def begin(id:UUID,body:BeginRun,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    delivery=service(request,response,'agent_delivery')
    if (body.booking_references or body.transaction_references) and identity.session.kind!='customer':
        raise AuthError(403,'ACTION_FORBIDDEN')
    knowledge=None
    if body.purpose=='answer':
        from airport_assistant.modules.knowledge.retrieval import Search
        conversation=await request.app.state.services.conversations.get(identity,id)
        knowledge=Search(query='user_report',language=conversation.language,service_type=body.service_type,as_of=datetime.now(timezone.utc))
    command=RunRequest(conversation_id=id,message_id=body.message_id,request_key=body.request_key,
        mode='draft' if body.purpose=='handoff' else ('answer' if body.purpose=='answer' else 'assist'),knowledge=knowledge,action_type='request_handoff',
        booking_references=tuple(body.booking_references),transaction_references=tuple(body.transaction_references))
    return RunStarted(run_id=await delivery.start(identity,command))

@router.post('/agent-runs/{id}/resume',response_model=RunOutcome,response_model_exclude_none=True)
async def resume(id:UUID,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,'agent_delivery').resume(identity,id)

@router.get('/agent-runs/{id}/receipt',response_model=RunStatus,response_model_exclude_none=True)
async def run_receipt(id:UUID,request:Request,response:Response,identity=Depends(get_identity)):
    return await service(request,response,'agent_delivery').status(identity,id)

@router.get('/conversations/{id}/messages/{message_id}/citations',response_model=CitationReceipt)
async def citations(id:UUID,message_id:UUID,request:Request,response:Response,identity=Depends(get_identity)):
    return await service(request,response,'ai_publication').citations(identity,id,message_id)

@router.post('/staff/cases/{id}/email-translation-drafts',response_model=TranslationView,status_code=201)
async def translate_case_email(id:UUID,body:CaseTranslationRequest,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await CaseEmailTranslations(service(request,response,'staff_translations')).prepare(identity,id,body)

@router.get('/staff/cases/{id}/email-translation-requests/{request_key}',response_model=TranslationView)
async def case_email_translation_request(id:UUID,request_key:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    return await CaseEmailTranslations(service(request,response,'staff_translations')).get(identity,id,key=request_key)

@router.get('/staff/cases/{id}/email-translation-drafts/{draft_id}',response_model=TranslationView)
async def get_case_email_translation(id:UUID,draft_id:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    return await CaseEmailTranslations(service(request,response,'staff_translations')).get(identity,id,draft_id)

@router.post('/staff/cases/{id}/email-translation-drafts/{draft_id}/send',response_model=SavedMessage,status_code=201)
async def approve_case_email_translation(id:UUID,draft_id:UUID,body:TranslationApproval,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    owner=service(request,response,'staff_translations');keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    return await CaseEmailTranslations(owner).approve(identity,id,draft_id,body,keys[0])

@router.post('/staff/cases/{id}/translation-drafts',response_model=TranslationView,status_code=201)
async def translate(id:UUID,body:TranslationRequest,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    return await service(request,response,'staff_translations').prepare(identity,id,body)

@router.get('/staff/cases/{id}/translation-requests/{request_key}',response_model=TranslationView)
async def translation_by_request(id:UUID,request_key:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    return await service(request,response,'staff_translations').by_request(identity,id,request_key)

@router.get('/staff/cases/{id}/translation-drafts/{draft_id}',response_model=TranslationView)
async def get_translation(id:UUID,draft_id:UUID,request:Request,response:Response,identity=Depends(get_staff_identity)):
    return await service(request,response,'staff_translations').get(identity,id,draft_id)

@router.post('/staff/cases/{id}/translation-drafts/{draft_id}/send',response_model=SavedMessage,status_code=201)
async def approve(id:UUID,draft_id:UUID,body:TranslationApproval,request:Request,response:Response,identity=Depends(get_mutation_identity)):
    translations=service(request,response,'staff_translations')
    keys=request.headers.getlist('idempotency-key')
    if len(keys)!=1:raise DraftError(400,'IDEMPOTENCY_KEY_REQUIRED')
    return await translations.approve(identity,id,draft_id,body,keys[0])
