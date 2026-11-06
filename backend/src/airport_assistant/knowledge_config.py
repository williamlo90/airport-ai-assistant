"""Embedding opt-in, separate from evaluation credentials and chat models."""
from pathlib import Path
from pydantic import SecretStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from airport_assistant.database_config import load_database_settings


class KnowledgeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='APP_KNOWLEDGE_', extra='ignore', hide_input_in_errors=True)
    enabled: bool = False
    api_key: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode='after')
    def credentials(self):
        if self.enabled and (self.api_key is None or not self.api_key.get_secret_value().strip()):
            raise ValueError('Knowledge embedding credentials required.')
        return self


def load_knowledge_settings(path: Path | None = Path('.env')):
    return load_database_settings(KnowledgeSettings, path, category='knowledge')
