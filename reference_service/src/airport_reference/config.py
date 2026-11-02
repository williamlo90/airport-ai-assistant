"""Local configuration, with value-free diagnostics."""

from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):pass


class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix="REF_",extra="ignore",hide_input_in_errors=True)
    env:Literal["development","test","production"]="development"
    host:Literal["127.0.0.1","::1","0.0.0.0"]="127.0.0.1"
    port:int=Field(default=8010,ge=1,le=65535)
    database_path:Path=Path("reference_service/local/reference.sqlite3")
    service_token:SecretStr=Field(min_length=32,max_length=256,repr=False)
    context_secret:SecretStr=Field(min_length=32,max_length=256,repr=False)

    @model_validator(mode="after")
    def distinct(self):
        if self.service_token==self.context_secret:raise ValueError("Independent service and context keys required.")
        return self


def load_settings(path:Path=Path("reference_service/.env")):
    try:
        if not path.is_file():raise ConfigurationError("Reference environment missing; run airport-reference-setup.")
        return Settings(_env_file=path,_env_file_encoding="utf-8")
    except (ValidationError,OSError,UnicodeError):
        raise ConfigurationError("Invalid reference configuration; check REF_* fields without sharing secret values.") from None
