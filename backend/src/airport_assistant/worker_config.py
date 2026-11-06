"""Worker credentials and execution limits are separate from API configuration."""
from pydantic import Field,SecretStr
from pydantic_settings import BaseSettings,SettingsConfigDict
from airport_assistant.database_config import RuntimeDatabaseSettings,load_database_settings


class WorkerDatabaseSettings(RuntimeDatabaseSettings):
    model_config=SettingsConfigDict(env_prefix='APP_WORKER_DB_',extra='ignore',hide_input_in_errors=True)
    url:SecretStr=Field(validation_alias='WORKER_DATABASE_URL',repr=False)
    pool_max:int=Field(default=1,ge=1,le=4)


class WorkerSettings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix='APP_WORKER_',extra='ignore',hide_input_in_errors=True)
    lease_seconds:int=Field(default=30,ge=3,le=300)
    handler_timeout:float=Field(default=60,gt=0,le=600)
    poll_seconds:float=Field(default=2,ge=0.1,le=30)


def load_worker_settings(path):return load_database_settings(WorkerSettings,path,category='worker')
