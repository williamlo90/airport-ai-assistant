"""Strict fictional dataset validation before atomic database creation."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


class Record(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True,frozen=True)


class Tenant(Record):
    id:UUID
    slug:str=Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    active:bool


class Subject(Record):
    tenant_id:UUID
    reference:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    active:bool


class Timed(Record):
    tenant_id:UUID
    reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    source_updated_at:datetime

    @model_validator(mode="after")
    def utc(self):
        for name in ("source_updated_at","start_at","end_at","effective_from"):
            value=getattr(self,name,None)
            if value is not None and value.utcoffset()!=timedelta(0):
                raise ValueError("Source timestamps must be UTC.")
        return self


class Booking(Timed):
    service_type:Literal["parking","lounge"]
    status:str=Field(min_length=1,max_length=64,pattern=r"^[a-z0-9_]+$")
    start_at:datetime
    end_at:datetime
    service_timezone:str=Field(max_length=64)
    location:str=Field(min_length=1,max_length=200)
    policy_reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")

    @model_validator(mode="after")
    def interval(self):
        ZoneInfo(self.service_timezone)
        if self.start_at>=self.end_at:raise ValueError("Invalid booking interval.")
        return self


class Transaction(Timed):
    booking_reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    currency:Literal["IDR","SGD"]
    amount_minor:int=Field(ge=0,le=10**12)
    currency_exponent:int=Field(ge=0,le=3)
    status:str=Field(min_length=1,max_length=64,pattern=r"^[a-z0-9_]+$")
    refund_status:str=Field(min_length=1,max_length=64,pattern=r"^[a-z0-9_]+$")

    @model_validator(mode="after")
    def exponent(self):
        if self.currency_exponent!={"IDR":0,"SGD":2}[self.currency]:raise ValueError("Currency exponent mismatch.")
        return self


class Policy(Timed):
    service_type:Literal["parking","lounge"]
    version:int=Field(ge=1)
    effective_from:datetime
    instructions:dict[Literal["id","en"],str]

    @model_validator(mode="after")
    def languages(self):
        if set(self.instructions)!={"id","en"} or any(not 1<=len(v)<=4000 for v in self.instructions.values()):
            raise ValueError("Both policy languages required.")
        return self


class Relation(Record):
    tenant_id:UUID
    subject_reference:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    resource_type:Literal["booking","transaction"]
    resource_reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    relation:Literal["owner","recorded_representative","beneficiary","payer"]
    scopes:list[Literal["booking:read","payment_status:read","payment_details:read"]]=Field(min_length=1,max_length=3)

    @model_validator(mode="after")
    def compatible(self):
        if len(set(self.scopes))!=len(self.scopes):raise ValueError("Duplicate scopes.")
        if self.resource_type=="booking" and self.scopes!=["booking:read"]:raise ValueError("Invalid booking scope.")
        if self.resource_type=="transaction" and ("booking:read" in self.scopes or "payment_status:read" not in self.scopes):raise ValueError("Invalid transaction scope.")
        return self


class Dataset(Record):
    version:Literal[1]
    tenants:list[Tenant]=Field(min_length=1,max_length=20)
    subjects:list[Subject]=Field(max_length=1000)
    policies:list[Policy]=Field(max_length=1000)
    bookings:list[Booking]=Field(max_length=10000)
    transactions:list[Transaction]=Field(max_length=10000)
    relations:list[Relation]=Field(max_length=50000)


class Observation(BaseModel):
    model_config=ConfigDict(extra="forbid")
    reference:str
    source_updated_at:datetime
    observed_at:datetime


class BookingView(Observation):
    service_type:Literal["parking","lounge"]
    status:Literal["confirmed","cancelled","pending","unknown"]
    start_at:datetime
    end_at:datetime
    service_timezone:str
    location:str
    policy_reference:str
    allowed_scopes:list[Literal["booking:read"]]


class TransactionView(Observation):
    status:Literal["pending","paid","failed","refunded","unknown"]
    allowed_scopes:list[Literal["payment_status:read","payment_details:read"]]
    booking_reference:str|None=None
    currency:Literal["IDR","SGD"]|None=None
    amount_minor:int|None=None
    currency_exponent:int|None=None
    refund_status:Literal["none","pending","completed","unknown"]|None=None
    source_status_label:str|None=None


    @model_serializer(mode="wrap")
    def visible_fields(self,handler):
        return {key:value for key,value in handler(self).items() if value is not None}


class PolicyView(Observation):
    service_type:Literal["parking","lounge"]
    version:int
    effective_from:datetime
    instructions:dict[Literal["id","en"],str]


class RelationView(BaseModel):
    model_config=ConfigDict(extra="forbid")
    resource_type:Literal["booking","transaction"]
    resource_reference:str
    relation:Literal["owner","recorded_representative","beneficiary","payer"]
    allowed_scopes:list[str]


class TransactionPage(BaseModel):
    items:list[TransactionView]
    next_cursor:str|None


class RelationPage(BaseModel):
    items:list[RelationView]
    next_cursor:str|None
