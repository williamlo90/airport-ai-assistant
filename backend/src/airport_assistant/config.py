"""Configuration for the executable API; provider settings are added with features."""

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    """A safe configuration diagnostic that excludes supplied values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )

    env: Literal["development", "test", "production"]
    host: Literal["127.0.0.1", "0.0.0.0", "::1", "::"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Read an explicit dotenv file and environment, reporting field names only."""
    if env_file is not None and not env_file.is_file():
        raise ConfigurationError(
            "Configuration file not found. Copy .env.example to .env, "
            "or supply an existing --env-file."
        )
    try:
        return Settings(_env_file=env_file, _env_file_encoding="utf-8")
    except ValidationError as exc:
        fields = sorted(
            {
                f"APP_{str(error['loc'][0]).upper()}"
                for error in exc.errors(include_input=False, include_url=False)
                if error["loc"] and error["loc"][0] in Settings.model_fields
            }
        )
        raise ConfigurationError(
            "Missing or invalid configuration: " + ", ".join(fields)
        ) from None
    except (OSError, UnicodeError):
        raise ConfigurationError("Configuration file could not be read.") from None
