"""Agent planning is opt-in, with explicit credentials independent of eval/embedding."""
from pathlib import Path
from pydantic import Field,SecretStr,model_validator
from pydantic_settings import BaseSettings,SettingsConfigDict
from airport_assistant.database_config import load_database_settings


class AgentSettings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix='APP_AGENT_',extra='ignore',hide_input_in_errors=True)
    enabled:bool=False
    api_key:SecretStr|None=Field(default=None,repr=False)
    model:str=Field(default='gpt-5.4-mini-2026-03-17',pattern=r'^[a-z0-9][a-z0-9._-]{0,90}-\d{4}-\d{2}-\d{2}$')
    knowledge_min_score:float|None=Field(default=None,ge=-1,le=1,allow_inf_nan=False)
    knowledge_policy_id:str|None=Field(default=None,min_length=1,max_length=100,pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
    @model_validator(mode='after')
    def valid(self):
        if self.enabled and (self.api_key is None or not self.api_key.get_secret_value().strip()):raise ValueError('Agent credential required.')
        if (self.knowledge_min_score is None)!=(self.knowledge_policy_id is None):raise ValueError('Explicit knowledge calibration policy required.')
        return self


def load_agent_settings(path:Path|None=Path('.env')):
    return load_database_settings(AgentSettings,path,category='agent')
