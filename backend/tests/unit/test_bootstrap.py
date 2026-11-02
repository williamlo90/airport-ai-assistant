"""BE-01 configuration and application bootstrap acceptance checks."""

import asyncio
import os
import subprocess
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from airport_assistant.config import ConfigurationError, Settings, load_settings
from airport_assistant.main import create_app


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    for name in ("APP_ENV", "APP_HOST", "APP_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_dotenv_and_environment_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=development\nAPP_PORT=8001\nMODEL_API_KEY=not-a-real-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_PORT", "8002")
    settings = load_settings(env_file)
    assert settings.port == 8002
    assert settings.host == "127.0.0.1"
    assert "not-a-real-key" not in repr(settings)


def test_missing_required_environment_has_safe_diagnostic():
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        load_settings(None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("APP_ENV", "secret-sentinel"),
        ("APP_PORT", "secret-sentinel"),
        ("APP_PORT", "65536"),
        ("APP_HOST", "secret-sentinel"),
    ],
)
def test_invalid_environment_never_prints_values(monkeypatch, field, value):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(field, value)
    with pytest.raises(ConfigurationError) as error:
        load_settings(None)
    assert field in str(error.value)
    assert value not in str(error.value)


def test_missing_file_is_not_silently_ignored(tmp_path):
    with pytest.raises(ConfigurationError, match="file not found"):
        load_settings(tmp_path / "absent.env")


def test_factory_serves_openapi_without_external_services():
    app = create_app(Settings(env="test", _env_file=None))

    async def request():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/openapi.json")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Airport AI Assistant API"
    assert response.json()["paths"] == {}


def test_production_does_not_expose_development_docs():
    app = create_app(Settings(env="production", _env_file=None))

    async def request():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/docs")).status_code == 404
            assert (await client.get("/openapi.json")).status_code == 404

    asyncio.run(request())


def test_cli_configuration_failure_exits_before_server_start():
    env = dict(os.environ, APP_ENV="secret-sentinel", APP_PORT="secret-sentinel")
    result = subprocess.run(
        [sys.executable, "-m", "airport_assistant.main", "--no-env-file", "--check-config"],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "APP_ENV" in result.stderr
    assert "APP_PORT" in result.stderr
    assert "secret-sentinel" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_cli_accepts_environment_only_configuration():
    env = dict(os.environ, APP_ENV="test", APP_HOST="127.0.0.1", APP_PORT="8000")
    result = subprocess.run(
        [sys.executable, "-m", "airport_assistant.main", "--no-env-file", "--check-config"],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "Configuration valid."
