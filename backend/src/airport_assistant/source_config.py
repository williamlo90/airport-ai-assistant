"""Opt-in source configuration. Request input never selects a destination."""

from pathlib import Path
import re
import json
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID
from pydantic import Field,SecretStr,field_validator,model_validator
from pydantic_settings import BaseSettings,SettingsConfigDict,NoDecode
from airport_assistant.config import ConfigurationError


class SourceSettings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix="SOURCE_",extra="ignore",hide_input_in_errors=True)
    base_url:str=Field(validation_alias="SOURCE_API_BASE_URL")
    token:SecretStr=Field(validation_alias="SOURCE_API_TOKEN",min_length=32,max_length=256,repr=False)
    context_secret:SecretStr=Field(min_length=32,max_length=256,repr=False)
    tenant_map:Annotated[dict[UUID,UUID],NoDecode]=Field(min_length=1,max_length=100)
    allow_loopback_http:bool=False
    timeout:float=Field(default=5,ge=.1,le=30)
    max_response_bytes:int=Field(default=262144,ge=1024,le=1048576)

    @field_validator("token","context_secret")
    @classmethod
    def safe_key(cls,value):
        raw=value.get_secret_value()
        if not raw.isascii() or any(ch.isspace() or ord(ch)<33 or ord(ch)>126 for ch in raw):raise ValueError("Visible ASCII source key required.")
        return value

    @field_validator("tenant_map",mode="before")
    @classmethod
    def canonical_mapping(cls,value):
        if isinstance(value,str):
            def unique(pairs):
                result={}
                for key,item in pairs:
                    if key in result:raise ValueError("Duplicate tenant key.")
                    result[key]=item
                return result
            value=json.loads(value,object_pairs_hook=unique)
        if not isinstance(value,dict):raise ValueError("Tenant map required.")
        for key,item in value.items():
            for identifier in (key,item):
                if not isinstance(identifier,UUID) and (not isinstance(identifier,str) or str(UUID(identifier))!=identifier):raise ValueError("Canonical UUID required.")
        return value

    @field_validator("base_url")
    @classmethod
    def origin(cls,value):
        parsed=urlsplit(value)
        if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in {"","/"} or parsed.query or parsed.fragment:
            raise ValueError("Fixed source origin required.")
        if not parsed.hostname.isascii() or not re.fullmatch(r"[a-zA-Z0-9.:-]+",parsed.hostname) or (parsed.port is not None and not 1<=parsed.port<=65535):raise ValueError("Invalid source origin.")
        return parsed.geturl().rstrip('/')

    @model_validator(mode="after")
    def closed_origin(self):
        parsed=urlsplit(self.base_url)
        if parsed.scheme=="http" and not (self.allow_loopback_http and parsed.hostname in {"127.0.0.1","::1"}):raise ValueError("TLS required except explicit numeric loopback.")
        if self.token==self.context_secret or len(set(self.tenant_map.values()))!=len(self.tenant_map):raise ValueError("Distinct keys and one-to-one tenant mapping required.")
        return self


def load_source_settings(path:Path|None=Path('.env')):
    try:
        if path is not None and not path.is_file():raise ValueError()
        return SourceSettings(_env_file=path,_env_file_encoding='utf-8')
    except Exception:raise ConfigurationError("Missing or invalid source configuration; check SOURCE_* fields.") from None
