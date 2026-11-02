"""Validated source observations; no framework or provider SDK."""

from datetime import datetime,timedelta
from typing import Literal
from uuid import UUID
from dataclasses import dataclass
from pydantic import BaseModel,ConfigDict,Field,model_validator,model_serializer


class SourceFailure(Exception):
    def __init__(self,status:int):
        self.status=status if status in {400,404,502,503} else 502
        super().__init__({400:"BAD_REQUEST",404:"RESOURCE_NOT_FOUND",502:"SOURCE_INVALID_RESPONSE",503:"SOURCE_UNAVAILABLE"}[self.status])


@dataclass(frozen=True)
class SourceCaller:
    tenant_id:UUID
    subject:str


class SourceRecord(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True,frozen=True,hide_input_in_errors=True)


class Observation(SourceRecord):
    reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    source_updated_at:datetime
    observed_at:datetime

    @model_validator(mode="after")
    def utc(self):
        for name in ("source_updated_at","observed_at","start_at","end_at","effective_from"):
            value=getattr(self,name,None)
            if value is not None and value.utcoffset()!=timedelta(0):raise ValueError("UTC timestamp required.")
        return self


class Booking(Observation):
    service_type:Literal["parking","lounge"]
    status:Literal["confirmed","cancelled","pending","unknown"]
    start_at:datetime
    end_at:datetime
    service_timezone:str=Field(min_length=1,max_length=64)
    location:str=Field(min_length=1,max_length=200)
    policy_reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    allowed_scopes:tuple[Literal["booking:read"],...]=Field(min_length=1,max_length=1)
    source_status_label:str|None=Field(default=None,pattern=r"^[a-z0-9_]{1,64}$")

    @model_validator(mode="after")
    def interval(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.service_timezone)
        if self.start_at>=self.end_at:raise ValueError("Invalid source interval.")
        return self


class Transaction(Observation):
    status:Literal["pending","paid","failed","refunded","unknown"]
    allowed_scopes:tuple[Literal["payment_status:read","payment_details:read"],...]=Field(min_length=1,max_length=2)
    booking_reference:str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]{1,80}$")
    currency:Literal["IDR","SGD"]|None=None
    amount_minor:int|None=Field(default=None,ge=0,le=10**12)
    currency_exponent:int|None=Field(default=None,ge=0,le=3)
    refund_status:Literal["none","pending","completed","unknown"]|None=None
    source_status_label:str|None=Field(default=None,pattern=r"^[a-z0-9_]{1,64}$")

    @model_validator(mode="after")
    def payment_scope(self):
        scopes=set(self.allowed_scopes)
        if len(scopes)!=len(self.allowed_scopes) or "payment_status:read" not in scopes:raise ValueError("Payment status scope required.")
        details=(self.currency,self.amount_minor,self.currency_exponent,self.refund_status)
        if "payment_details:read" in scopes:
            if any(v is None for v in details) or self.currency_exponent!={"IDR":0,"SGD":2}[self.currency]:raise ValueError("Payment details invalid.")
        elif self.model_fields_set & {"currency","amount_minor","currency_exponent","refund_status"}:raise ValueError("Unexpected payment fields.")
        return self

    @model_serializer(mode="wrap")
    def visible_fields(self,handler):return {k:v for k,v in handler(self).items() if v is not None}


class Policy(Observation):
    service_type:Literal["parking","lounge"]
    version:int=Field(ge=1)
    effective_from:datetime
    instructions:dict[Literal["id","en"],str]

    @model_validator(mode="after")
    def languages(self):
        if set(self.instructions)!={"id","en"} or any(not 1<=len(v)<=4000 for v in self.instructions.values()):raise ValueError("Invalid policy text.")
        return self


class Relation(SourceRecord):
    resource_type:Literal["booking","transaction"]
    resource_reference:str=Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    relation:Literal["owner","recorded_representative","beneficiary","payer"]
    allowed_scopes:tuple[Literal["booking:read","payment_status:read","payment_details:read"],...]=Field(min_length=1,max_length=3)

    @model_validator(mode="after")
    def resource_scope(self):
        scopes=set(self.allowed_scopes)
        if len(scopes)!=len(self.allowed_scopes):raise ValueError("Duplicate scopes.")
        if self.resource_type=="booking" and scopes!={"booking:read"}:raise ValueError("Invalid booking scope.")
        if self.resource_type=="transaction" and ("booking:read" in scopes or "payment_status:read" not in scopes):raise ValueError("Invalid payment scope.")
        return self


class TransactionPage(SourceRecord):
    items:tuple[Transaction,...]=Field(max_length=100)
    next_cursor:str|None=Field(min_length=1,max_length=2048)


class RelationPage(SourceRecord):
    items:tuple[Relation,...]=Field(max_length=100)
    next_cursor:str|None=Field(min_length=1,max_length=2048)
