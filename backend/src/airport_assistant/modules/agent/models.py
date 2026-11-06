"""Only server-owned scope can authorize an agent operation."""
from dataclasses import dataclass,field
import json
from typing import Literal, Protocol
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from airport_assistant.modules.knowledge.models import text
from airport_assistant.modules.knowledge.retrieval import Search


class AgentError(Exception):
    def __init__(self,status,code):self.status,self.code=status,code;super().__init__(code)


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)


class Reference(Model):
    reference:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


class KnowledgeQuery(Model):
    query:str=Field(min_length=1,max_length=1000)
    _text=field_validator('query')(text)


class DraftProposal(Model):
    category:Literal['information','booking_issue','transaction_issue']
    service_type:Literal['general','parking','lounge']
    summary:str=Field(min_length=1,max_length=200)
    _text=field_validator('summary')(text)


class Clarification(Model):
    question:str=Field(min_length=1,max_length=1000)
    _text=field_validator('question')(text)


class ToolCall(Model):
    call_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    name:Literal['read_booking','read_transaction','read_booking_policy','search_knowledge']
    arguments:dict


class Decision(Model):
    kind:Literal['tool','clarify','draft']
    tool:ToolCall|None=None
    clarification:Clarification|None=None
    proposal:DraftProposal|None=None
    @model_validator(mode='after')
    def exactly_one(self):
        expected={'tool':self.tool,'clarify':self.clarification,'draft':self.proposal}
        if expected[self.kind] is None or sum(v is not None for v in expected.values())!=1:raise ValueError('One decision required.')
        return self


def strict_json(value):
    def pairs(items):
        result={}
        for key,item in items:
            if key in result:raise ValueError('Duplicate JSON key.')
            result[key]=item
        return result
    def constant(value):raise ValueError('Nonfinite JSON number.')
    return json.loads(value,object_pairs_hook=pairs,parse_constant=constant)


class Planner(Protocol):
    async def decide(self,report:str,language:str,tools:tuple[str,...],*,draft:bool=False)->Decision:...


@dataclass(frozen=True)
class AgentScope:
    # Constructed by the backend from a current owned customer message, never from model JSON.
    identity:object=field(repr=False)
    conversation_id:UUID
    message_id:UUID
    conversation_version:int
    generation_epoch:int
    report:str=field(repr=False)
    language:str
    booking_references:frozenset[str]=frozenset()
    transaction_references:frozenset[str]=frozenset()
    knowledge:Search|None=None
    memory:tuple[str,...]=field(default=(),repr=False)
    labels:object|None=field(default=None,repr=False)


@dataclass(frozen=True)
class ToolObservation:
    name:str
    data:object=field(repr=False)
    labels:object|None=field(default=None,repr=False)
    evidence:object|None=field(default=None,repr=False)
    authority:str='untrusted_tool_data'
    booking:object|None=field(default=None,repr=False)
