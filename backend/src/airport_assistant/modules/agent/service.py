"""Single-step planning and explicit tool execution, not an autonomous loop."""
import re
from uuid import UUID
from pydantic import ValidationError
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import ReportPayload
from airport_assistant.modules.source.labels import SecurityLabels,merge_labels,labels_for_observation,labels_for_policy
from airport_assistant.modules.source.models import Booking,Transaction,Policy
from .models import AgentError,AgentScope,ToolCall,Reference,KnowledgeQuery,Decision,ToolObservation


class AgentFoundation:
    def __init__(self,model,conversations,drafts,source=None,private_access=None,evidence=None):
        self.model,self.conversations,self.drafts=model,conversations,drafts
        self.source,self.private_access,self.evidence=source,private_access,evidence

    async def scope(self,identity,conversation_id,message_id,*,booking_references=(),transaction_references=(),knowledge=None,include_history=False):
        if not isinstance(conversation_id,UUID) or not isinstance(message_id,UUID):raise AgentError(422,'AGENT_INVALID_INPUT')
        conv,message=await self.conversations.customer_message(identity,conversation_id,message_id)
        self._ai(conv)
        if message.sequence!=conv.last_sequence:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        history=await self.conversations.customer_history(identity,conversation_id) if include_history else ()
        if history and history[-1].id!=message.id:raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        memory=tuple(m.body for m in history)
        if sum(len(v) for v in memory)>16000:raise AgentError(413,'AGENT_CONTEXT_LIMIT')
        def refs(values):
            if not isinstance(values,(tuple,list,frozenset)) or len(values)>8:raise AgentError(422,'AGENT_INVALID_INPUT')
            try:result=frozenset(Reference(reference=r).reference for r in values)
            except ValidationError:raise AgentError(422,'AGENT_INVALID_INPUT') from None
            # A model cannot expand the references beyond the customer's actual report.
            def mentioned(ref,body):return re.search(r'(?<![A-Za-z0-9_-])'+re.escape(ref)+r'(?![A-Za-z0-9_-])',body) is not None
            if any(not mentioned(r,'\n'.join(memory) if memory else message.body) for r in result):raise AgentError(422,'AGENT_INVALID_INPUT')
            latest=frozenset(r for r in result if mentioned(r,message.body))
            return latest or result
        if knowledge is not None:
            from airport_assistant.modules.knowledge.retrieval import Search
            try:knowledge=Search.model_validate(knowledge.model_dump())
            except (ValidationError,AttributeError):raise AgentError(422,'AGENT_INVALID_INPUT') from None
        return AgentScope(identity,conversation_id,message_id,conv.version,conv.generation_epoch,message.body,
            conv.language,refs(booking_references),refs(transaction_references),knowledge,memory,self._labels(identity,conv))

    @staticmethod
    def _labels(identity,conv):
        records=getattr(conv,'resource_security_labels',None)
        if type(records) not in {tuple,list}:raise AgentError(502,'AGENT_INVALID_RESPONSE')
        try:return SecurityLabels.from_records(identity.session.tenant_id,list(records)) if records else None
        except (ValueError,TypeError):raise AgentError(502,'AGENT_INVALID_RESPONSE') from None

    @staticmethod
    def _ai(conv):
        if conv.mode!='ai' or conv.case_id is not None:raise AgentError(409,'AGENT_CONTEXT_CHANGED')

    async def current(self,scope):
        conv,message=await self.conversations.customer_message(scope.identity,scope.conversation_id,scope.message_id)
        self._ai(conv)
        if (conv.version,conv.generation_epoch,conv.language,message.body)!=(scope.conversation_version,scope.generation_epoch,scope.language,scope.report):
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        current=self._labels(scope.identity,conv)
        if (current.to_records() if current is not None else [])!=(scope.labels.to_records() if scope.labels is not None else []):
            raise AgentError(409,'AGENT_CONTEXT_CHANGED')
        return conv

    def tools(self,scope):
        names=[]
        if scope.identity.session.kind=='customer' and self.source is not None and self.private_access is not None:
            if scope.booking_references:names+=['read_booking','read_booking_policy']
            if scope.transaction_references:names+=['read_transaction']
        if self.evidence is not None and scope.knowledge is not None:names+=['search_knowledge']
        return tuple(names)

    async def plan(self,scope):
        await self.current(scope)
        decision=await self.model.decide(scope.report,scope.language,self.tools(scope))
        await self.current(scope)
        try:decision=Decision.model_validate(decision.model_dump())
        except (ValidationError,AttributeError):raise AgentError(502,'AGENT_INVALID_RESPONSE') from None
        if decision.kind=='draft':raise AgentError(502,'AGENT_INVALID_RESPONSE')
        if decision.tool is not None:self.validate_call(scope,decision.tool)
        return decision

    def validate_call(self,scope,call):
        try:
            call=ToolCall.model_validate(call.model_dump() if isinstance(call,ToolCall) else call)
            if call.name not in self.tools(scope):raise ValueError
            if call.name=='search_knowledge':return call,KnowledgeQuery.model_validate(call.arguments)
            args=Reference.model_validate(call.arguments)
            allowed=scope.transaction_references if call.name=='read_transaction' else scope.booking_references
            if args.reference not in allowed:raise ValueError
            return call,args
        except (ValidationError,ValueError,TypeError):raise AgentError(422,'AGENT_TOOL_DENIED') from None

    async def execute_read(self,scope,call):
        call,args=self.validate_call(scope,call)
        await self.current(scope)
        if call.name=='search_knowledge':
            selection=scope.knowledge.model_copy(update={'query':args.query})
            result=await self.evidence.collect(scope.identity,selection)
            await self.current(scope)
            return ToolObservation(call.name,result,labels=scope.labels,evidence=result.snapshot)
        identity=scope.identity
        if call.name=='read_transaction':
            value=await self.source.transaction(identity,args.reference)
            if type(value) is not Transaction or value.reference!=args.reference:raise AgentError(502,'AGENT_INVALID_RESPONSE')
            labels=labels_for_observation(identity.session.tenant_id,value)
        else:
            booking=await self.source.booking(identity,args.reference)
            if type(booking) is not Booking or booking.reference!=args.reference:raise AgentError(502,'AGENT_INVALID_RESPONSE')
            if call.name=='read_booking_policy':
                value=await self.source.policy(identity,booking.policy_reference)
                if type(value) is not Policy or value.reference!=booking.policy_reference or value.service_type!=booking.service_type:raise AgentError(502,'AGENT_INVALID_RESPONSE')
                labels=labels_for_policy(identity.session.tenant_id,booking,value)
            else:value=booking;labels=labels_for_observation(identity.session.tenant_id,value)
        if scope.labels is not None:labels=merge_labels(scope.labels,labels)
        if await self.private_access.allows(tenant_id=identity.session.tenant_id,principal_id=identity.session.principal_id,labels=labels) is not True:
            raise AuthError(404,'RESOURCE_NOT_FOUND')
        await self.current(scope)
        return ToolObservation(call.name,value,labels,booking=booking if call.name=='read_booking_policy' else None)

    async def prepare_draft(self,scope,*,action_type='create_case',contact_email=None):
        # Dedicated stateless model request: user report only, NEVER tool output/history.
        # Private source-derived persistence is not silently downgraded to a free-text draft.
        if action_type not in {'create_case','request_handoff'}:raise AgentError(422,'AGENT_TOOL_DENIED')
        await self.current(scope)
        decision=await self.model.decide(scope.report,scope.language,(),draft=True)
        await self.current(scope)
        try:
            decision=Decision.model_validate(decision.model_dump())
            if decision.kind!='draft':raise ValueError
            p=decision.proposal
            payload=ReportPayload(category=p.category,service_type=p.service_type,summary=p.summary,customer_report=scope.report,
                booking_reference=next(iter(scope.booking_references)) if len(scope.booking_references)==1 else None,
                transaction_reference=next(iter(scope.transaction_references)) if len(scope.transaction_references)==1 else None,
                contact_email=contact_email)
        except (ValidationError,ValueError,AttributeError):raise AgentError(502,'AGENT_INVALID_RESPONSE') from None
        return await self.drafts.create(scope.identity,scope.conversation_id,action_type,payload,
            expected_conversation_version=scope.conversation_version,expected_generation_epoch=scope.generation_epoch)
