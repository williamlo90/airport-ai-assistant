"""HTTP protection and response shape with injected service doubles only."""

import asyncio
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

from httpx import ASGITransport,AsyncClient
import pytest

from airport_assistant.api.auth import SESSION_COOKIE,CSRF_COOKIE
from airport_assistant.auth_config import AuthSettings
from airport_assistant.composition import ApplicationServices
from airport_assistant.config import Settings
from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.main import create_app
from airport_assistant.modules.identity.auth import Session,SessionCredentials
from airport_assistant.modules.identity.access import AccessService
from airport_assistant.modules.system.health import ProcessHealthService


def app():
    tenant=uuid4();session=Session(uuid4(),tenant,None,"anonymous",datetime.now(timezone.utc)+timedelta(hours=1),"hidden-hash")
    service=Mock()
    service.tenant=AsyncMock(return_value=tenant)
    service.bootstrap=AsyncMock(return_value=SessionCredentials(session,"A"*43,"B"*43))
    service.current=AsyncMock(return_value=session)
    service.validate_csrf=Mock()
    application=create_app(Settings(env="test",database_enabled=True,auth_enabled=True),
        database_settings=RuntimeDatabaseSettings(DATABASE_URL="postgresql://test:fake@ep-test-pooler.region.aws.neon.tech/db?sslmode=require"),
        auth_settings=AuthSettings(SESSION_SECRET="k"*48),services=ApplicationServices(ProcessHealthService(),service,access=AccessService(service)))
    return application,service


def call(application,headers=None,url="https://a.example.test",path="/api/v1/auth/sessions",method="POST"):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=application),base_url=url) as client:
            return await client.request(method,path,headers=headers)
    return asyncio.run(run())


def test_bootstrap_cookies_are_host_only_secure_and_no_hash_in_body():
    application,_=app()
    response=call(application,{"Origin":"https://a.example.test"})
    assert response.status_code==200
    assert response.json()=={"session_kind":"anonymous","principal_id":None}
    assert response.headers["cache-control"]=="no-store"
    cookies=response.headers.get_list("set-cookie")
    session=next(x for x in cookies if x.startswith(SESSION_COOKIE+"="))
    csrf=next(x for x in cookies if x.startswith(CSRF_COOKIE+"="))
    assert "Secure" in session and "HttpOnly" in session and "SameSite=lax" in session
    assert "Secure" in csrf and "HttpOnly" not in csrf
    assert "Domain=" not in session+csrf and "Path=/" in session+csrf
    assert "hidden-hash" not in response.text and "A"*43 not in response.text


@pytest.mark.parametrize("origin",[None,"null","https://other.example.test","http://a.example.test","https://a.example.test:0","https://a.example.test/path"])
def test_bootstrap_origin_rejected_before_tenant_or_auth_calls(origin):
    application,service=app()
    response=call(application,{"Origin":origin} if origin is not None else {})
    assert response.status_code==403
    service.tenant.assert_not_awaited();service.bootstrap.assert_not_awaited()


def test_plain_http_is_rejected():
    application,service=app()
    assert call(application,{"Origin":"http://a.example.test"},url="http://a.example.test").status_code==403
    service.bootstrap.assert_not_awaited()


@pytest.mark.parametrize("headers",[
    [("Origin","https://a.example.test"),("Origin","https://a.example.test")],
    [("Origin","https://a.example.test"),("Host","a.example.test"),("Host","a.example.test")],
    [("Origin","https://a.example.test"),("Cookie",SESSION_COOKIE+"=a; "+SESSION_COOKIE+"=b")],
    [("Origin","https://a.example.test"),("Cookie",CSRF_COOKIE+"=a; "+CSRF_COOKIE+"=b")],
])
def test_duplicate_origin_host_or_auth_cookies_fail_closed(headers):
    application,_=app()
    assert call(application,headers).status_code==403


def test_me_does_not_return_internal_session_or_hash():
    application,_=app()
    response=call(application,method="GET",path="/api/v1/me")
    assert response.status_code==200
    assert set(response.json())=={"session_kind","principal_id"}
    assert response.headers["cache-control"]=="no-store"
