"""Typed knowledge metadata; source text/URI never grants execution or access."""
from datetime import datetime,timezone
from typing import Literal
from uuid import UUID
import re
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator,TypeAdapter,AnyHttpUrl

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)

def text(value):
    if value is not None and (not value.strip() or '\0' in value):raise ValueError('Nonblank text required.')
    return value

def datetime_input(value):
    if value is not None and not isinstance(value,(str,datetime)):raise ValueError('ISO datetime required.')
    return value

def aware(value):
    if value is not None:
        if value.tzinfo is None or value.utcoffset() is None:raise ValueError('Explicit timezone required.')
        return value.astimezone(timezone.utc)
    return value

class Applicability(Model):
    terminal:str|None=Field(default=None,min_length=1,max_length=32,pattern=r'^[A-Za-z0-9_-]+$')
    booking_created_from:datetime|None=Field(default=None,strict=False)
    booking_created_to:datetime|None=Field(default=None,strict=False)
    _date_input=field_validator('booking_created_from','booking_created_to',mode='before')(datetime_input)
    _aware=field_validator('booking_created_from','booking_created_to')(aware)
    @model_validator(mode='after')
    def interval(self):
        if self.booking_created_from and self.booking_created_to and self.booking_created_to<=self.booking_created_from:raise ValueError('Invalid booking interval.')
        return self

class Content(Model):
    body:str=Field(min_length=1,max_length=20000)
    source_uri:str=Field(min_length=1,max_length=2048)
    service_type:Literal['general','parking','lounge']
    language:Literal['id','en']
    visibility:Literal['public','staff']
    allowed_roles:tuple[Literal['admin','cs','supervisor'],...]=Field(default=(),max_length=3,strict=False)
    valid_from:datetime=Field(strict=False)
    valid_to:datetime|None=Field(default=None,strict=False)
    applicability:Applicability=Field(default_factory=Applicability)
    policy_reference:str|None=Field(default=None,min_length=1,max_length=200)
    supersedes_version_id:UUID|None=Field(default=None,strict=False)
    _text=field_validator('body','policy_reference')(text)
    _date_input=field_validator('valid_from','valid_to',mode='before')(datetime_input)
    _aware=field_validator('valid_from','valid_to')(aware)
    @field_validator('source_uri')
    @classmethod
    def source(cls,value):
        text(value)
        if any(ord(c)<33 for c in value):raise ValueError('Invalid source metadata.')
        if value.startswith('urn:airport:'):
            if not re.fullmatch(r'urn:airport:[A-Za-z0-9][A-Za-z0-9:._/-]*',value):raise ValueError('Invalid managed URI.')
        else:
            u=TypeAdapter(AnyHttpUrl).validate_python(value)
            if u.scheme!='https' or u.username or u.password or '?' in value or '#' in value:raise ValueError('Credential-free HTTPS source required.')
        return value
    @model_validator(mode='after')
    def valid(self):
        if self.valid_to and self.valid_to<=self.valid_from:raise ValueError('Invalid validity interval.')
        if len(set(self.allowed_roles))!=len(self.allowed_roles) or (self.visibility=='staff')!=bool(self.allowed_roles):raise ValueError('Explicit staff roles required only for staff visibility.')
        object.__setattr__(self,'allowed_roles',tuple(sorted(self.allowed_roles)))
        return self

class CreateDocument(Model):
    membership_id:UUID=Field(strict=False)
    document_key:str=Field(min_length=1,max_length=100,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    title:str=Field(min_length=1,max_length=200)
    content:Content
    _title=field_validator('title')(text)
    @model_validator(mode='after')
    def first_version(self):
        if self.content.supersedes_version_id is not None:raise ValueError('Initial version has no predecessor.')
        return self
class CreateVersion(Model):
    membership_id:UUID=Field(strict=False)
    expected_document_revision:int=Field(ge=1)
    content:Content
class EditVersion(Model):
    membership_id:UUID=Field(strict=False)
    expected_revision:int=Field(ge=1)
    content:Content
class Transition(Model):
    membership_id:UUID=Field(strict=False)
    expected_revision:int=Field(ge=1)
class Withdraw(Transition):
    reason:str=Field(min_length=1,max_length=1000)
    _reason=field_validator('reason')(text)

class Selection(Model):
    # Internal only: as_of/policy reference must come from trusted service context.
    document_key:str=Field(min_length=1,max_length=100,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    service_type:Literal['general','parking','lounge']
    language:Literal['id','en']
    as_of:datetime=Field(strict=False)
    policy_reference:str|None=Field(default=None,min_length=1,max_length=200)
    terminal:str|None=Field(default=None,min_length=1,max_length=32,pattern=r'^[A-Za-z0-9_-]+$')
    booking_created_at:datetime|None=Field(default=None,strict=False)
    _date_input=field_validator('as_of','booking_created_at',mode='before')(datetime_input)
    _aware=field_validator('as_of','booking_created_at')(aware)
    _text=field_validator('policy_reference')(text)
