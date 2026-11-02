"""Authentication settings loaded only when explicitly enabled."""

from pathlib import Path

from pydantic import Field,SecretStr,field_validator
from pydantic_settings import BaseSettings,SettingsConfigDict

from airport_assistant.database_config import load_database_settings


class AuthSettings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix="APP_AUTH_",extra="ignore",hide_input_in_errors=True)
    session_secret:SecretStr=Field(validation_alias="SESSION_SECRET",repr=False)
    anonymous_ttl:int=Field(default=86400,ge=300,le=172800)
    customer_ttl:int=Field(default=28800,ge=300,le=86400)
    challenge_ttl:int=Field(default=300,ge=30,le=600)
    max_attempts:int=Field(default=5,ge=1,le=10)
    rate_window:int=Field(default=900,ge=60,le=3600)
    boot_limit:int=Field(default=100,ge=1,le=1000)
    contact_limit:int=Field(default=3,ge=1,le=10)
    ip_limit:int=Field(default=100,ge=1,le=1000)
    session_limit:int=Field(default=5,ge=1,le=20)
    verify_ip_limit:int=Field(default=200,ge=1,le=2000)
    verify_session_limit:int=Field(default=15,ge=1,le=100)
    delivery_timeout:float=Field(default=10,ge=0.1,le=30)

    @field_validator("session_secret")
    @classmethod
    def strong_secret(cls,value:SecretStr):
        if len(value.get_secret_value().encode())<32:raise ValueError("SESSION_SECRET must contain at least 32 bytes.")
        return value


def load_auth_settings(env_file:Path|None=Path(".env"))->AuthSettings:
    return load_database_settings(AuthSettings,env_file,category="authentication")
