"""Mail is disabled until its explicit credential/account configuration exists."""
import re
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from airport_assistant.database_config import load_database_settings


class WebhookConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)
    account_ref:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    secrets:list[SecretStr]=Field(min_length=1,max_length=3,repr=False)

    @model_validator(mode='after')
    def valid_keys(self):
        from airport_assistant.modules.mail.inbox import signing_key
        for value in self.secrets:signing_key(value.get_secret_value())
        return self


class AuthMailRoute(BaseModel):
    """Trusted deployment routing, never supplied by the browser."""
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)
    account_id:UUID
    account_ref:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    sender:str=Field(repr=False)
    reply_to:str=Field(repr=False)

    @model_validator(mode='after')
    def normalized_addresses(self):
        from airport_assistant.modules.mail.service import address
        for value in (self.sender,self.reply_to):
            if address(value)!=value:raise ValueError('Normalized mailbox required.')
        return self


class MailSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='APP_MAIL_', extra='ignore', hide_input_in_errors=True)
    enabled: bool = False
    auth_enabled:bool=False
    auth_routes:dict[UUID,AuthMailRoute]=Field(default_factory=dict,repr=False)
    accounts: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    ingress_enabled:bool=False
    receiving_enabled:bool=False
    reply_enabled:bool=False
    reply_secret:SecretStr|None=Field(default=None,repr=False)
    webhooks:dict[str,WebhookConfig]=Field(default_factory=dict,repr=False)

    @model_validator(mode='after')
    def complete_configuration(self):
        if len(self.auth_routes)>32:raise ValueError('Too many authentication routes.')
        if self.auth_enabled and (not self.auth_routes or any(route.account_ref not in self.accounts for route in self.auth_routes.values())):
            raise ValueError('Authentication mail requires tenant routes and account credentials.')
        if len(self.accounts)>32 or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ref) or not key.get_secret_value().strip() for ref,key in self.accounts.items()):
            raise ValueError('Invalid mail account configuration.')
        if self.enabled and not self.accounts:
            raise ValueError('Mail requires account and credential configuration.')
        if self.receiving_enabled and not self.accounts:raise ValueError('Receiving requires account credentials.')
        if self.reply_enabled and not self.receiving_enabled:raise ValueError('Reply processing requires receiving.')
        if self.reply_secret is not None and len(self.reply_secret.get_secret_value())<32:raise ValueError('Reply secret must have at least32 characters.')
        if len(self.webhooks)>32 or any(not re.fullmatch(r'[a-z][a-z0-9-]{0,62}',alias) for alias in self.webhooks):raise ValueError('Invalid webhook mapping.')
        if self.ingress_enabled and not self.webhooks:raise ValueError('Ingress requires webhook signing configuration.')
        return self


def load_mail_settings(path):
    return load_database_settings(MailSettings, path, category='mail')
