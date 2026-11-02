"""Service injection exercises the real HTTP route and separate app instances."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from httpx import ASGITransport, AsyncClient

from airport_assistant.api.dependencies import get_health_service
from airport_assistant.composition import ApplicationServices, build_services
from airport_assistant.config import Settings
from airport_assistant.main import create_app
from airport_assistant.modules.system.health import ProcessHealthService, ProcessStatus


class RecordingHealth:
    def __init__(self):
        self.calls = 0

    async def check(self) -> ProcessStatus:
        self.calls += 1
        return ProcessStatus()


def get_health(app):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/health/live")
    return asyncio.run(run())


def test_plain_service_runs_without_fastapi_or_external_resources():
    result = asyncio.run(ProcessHealthService().check())
    assert result == ProcessStatus(status="ok", scope="process")


def test_app_uses_injected_service_without_rebuilding_default(monkeypatch):
    def unexpected_build():
        pytest.fail("default composition must not run for explicit services")
    monkeypatch.setattr("airport_assistant.main.build_services", unexpected_build)
    service = RecordingHealth()
    app = create_app(Settings(env="test"), services=ApplicationServices(health=service))
    response = get_health(app)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scope": "process"}
    assert service.calls == 1


def test_service_bindings_and_overrides_do_not_leak_between_apps():
    first = create_app(Settings(env="test"))
    second = create_app(Settings(env="test"))
    replacement = RecordingHealth()
    first.dependency_overrides[get_health_service] = lambda: replacement
    assert get_health(first).status_code == 200
    assert get_health(second).status_code == 200
    assert replacement.calls == 1
    assert first.state.services is not second.state.services
    assert first.state.services.health is not second.state.services.health
    first.dependency_overrides.clear()
    assert get_health(first).status_code == 200
    assert replacement.calls == 1


def test_services_cannot_have_bindings_reassigned():
    services = build_services()
    with pytest.raises(FrozenInstanceError):
        services.health = RecordingHealth()


def test_dependency_failure_preserves_be02_safe_error_contract():
    class FailingHealth:
        async def check(self):
            raise RuntimeError("secret-sentinel")
    app = create_app(Settings(env="test"), services=ApplicationServices(health=FailingHealth()))
    response = get_health(app)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "secret-sentinel" not in response.text
