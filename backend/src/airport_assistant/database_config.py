"""Explicit, separate runtime/migration settings; no connection on import."""

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from airport_assistant.config import ConfigurationError


def parse_database_url(value: str):
    try:
        url = urlsplit(value)
        query = parse_qs(url.query, keep_blank_values=True)
        valid = (
            url.scheme in {"postgres", "postgresql"}
            and not any(char in value for char in "\r\n\x00")
            and url.hostname and url.username and url.password
            and url.path not in {"", "/"} and not url.fragment
            and (url.port is None or 1 <= url.port <= 65535)
            and not any(char in url.hostname for char in ", /\\")
            and set(query) <= {"sslmode", "channel_binding", "connect_timeout"}
            and all(len(items) == 1 for items in query.values())
            and query.get("sslmode", ["require"])[0] in {"require", "verify-full"}
            and query.get("channel_binding", ["require"])[0] in {"require", "prefer"}
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise ValueError("Invalid PostgreSQL connection configuration.") from None
    return url


class RuntimeDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_DB_", extra="ignore", hide_input_in_errors=True)
    url: SecretStr = Field(validation_alias="DATABASE_URL", repr=False)
    pool_max: int = Field(default=2, ge=1, le=20)
    max_waiting: int = Field(default=8, ge=1, le=100)
    connect_timeout: int = Field(default=10, ge=1, le=60)
    acquire_timeout: int = Field(default=10, ge=1, le=60)
    statement_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: SecretStr):
        url = parse_database_url(value.get_secret_value())
        if url.hostname.endswith(".neon.tech") and not url.hostname.split(".")[0].endswith("-pooler"):
            raise ValueError("DATABASE_URL requires a pooled Neon endpoint.")
        return value


class MigrationDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_DB_", extra="ignore", hide_input_in_errors=True)
    url: SecretStr = Field(validation_alias="DATABASE_DIRECT_URL", repr=False)
    connect_timeout: int = Field(default=10, ge=1, le=60)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: SecretStr):
        url = parse_database_url(value.get_secret_value())
        if url.hostname.split(".")[0].endswith("-pooler"):
            raise ValueError("DATABASE_DIRECT_URL requires a direct endpoint.")
        return value


def load_database_settings(model, env_file: Path | None = Path(".env"), *, category="database"):
    try:
        if env_file is not None and not env_file.is_file():
            raise ConfigurationError(f"{category.capitalize()} configuration file not found.")
        return model(_env_file=env_file, _env_file_encoding="utf-8")
    except ValidationError as exc:
        public_names = {}
        for name, field in model.model_fields.items():
            public = field.validation_alias or model.model_config.get("env_prefix","") + name.upper()
            public_names[name] = public
            public_names[public] = public
        fields = sorted({public_names[error["loc"][0]] for error in exc.errors(include_input=False)
                         if error["loc"] and error["loc"][0] in public_names})
        raise ConfigurationError(f"Missing or invalid {category} configuration: " + ", ".join(fields)) from None
    except (OSError, UnicodeError):
        raise ConfigurationError(f"{category.capitalize()} configuration file could not be read.") from None


def validate_same_target(runtime: RuntimeDatabaseSettings, migration: MigrationDatabaseSettings) -> None:
    pooled = parse_database_url(runtime.url.get_secret_value())
    direct = parse_database_url(migration.url.get_secret_value())
    host = pooled.hostname
    if host.split(".")[0].endswith("-pooler"):
        first, separator, rest = host.partition(".")
        host = first.removesuffix("-pooler") + separator + rest
    if (host, pooled.port or 5432, unquote(pooled.path)) != (direct.hostname, direct.port or 5432, unquote(direct.path)):
        raise ConfigurationError("Runtime and direct URLs must target the same endpoint and database.")
