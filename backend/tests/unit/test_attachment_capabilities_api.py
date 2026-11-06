"""Read-only file availability, without reading env, scanning, or touching data."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from httpx import ASGITransport,AsyncClient
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.api.attachments import router
from airport_assistant.api.dependencies import get_identity
from airport_assistant.api.boundary import RequestBoundary


def test_capabilities_follow_configured_scanner_without_exposing_backend_details():
    async def scenario():
        app=create_app(Settings(env='test',_env_file=None));app.include_router(router)
        app.dependency_overrides[get_identity]=lambda:SimpleNamespace(session=SimpleNamespace(kind='customer'))
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            for enabled in (False,True):
                app.state.services=replace(app.state.services,attachments=SimpleNamespace(uploads_configured=enabled))
                response=await client.get('/api/v1/attachments/capabilities')
                assert response.status_code==200 and response.headers['cache-control']=='no-store'
                assert response.json()==dict(upload_enabled=enabled,max_bytes=5242880,max_files=3,
                    media_types=['image/png','image/jpeg','application/pdf'])
            assert (await client.get('/api/v1/attachments/capabilities?tenant_id=other')).status_code==400
    asyncio.run(scenario())


def test_customer_capabilities_do_not_grant_anonymous_or_staff_access():
    async def scenario():
        app=create_app(Settings(env='test',_env_file=None));app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            for kind in ('anonymous','staff'):
                app.dependency_overrides[get_identity]=lambda:SimpleNamespace(session=SimpleNamespace(kind=kind))
                assert (await client.get('/api/v1/attachments/capabilities')).status_code==401
    asyncio.run(scenario())


def test_only_raw_upload_route_has_the_advertised_five_mib_http_budget():
    async def scenario():
        reached=[]
        async def inner(scope,receive,send):
            message=await receive();reached.append(len(message['body']))
            body=json.dumps(dict(size=len(message['body']))).encode()
            await send(dict(type='http.response.start',status=200,headers=[(b'content-type',b'application/json')]))
            await send(dict(type='http.response.body',body=body))
        app=RequestBoundary(inner,1048576)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.post('/api/v1/attachments',content=b'x'*5242880)).status_code==200
            assert reached==[5242880]
            for path,size in [('/api/v1/attachments',5242881),('/api/v1/action-drafts',1048577)]:
                assert (await client.post(path,content=b'x'*size)).status_code==413
            assert reached==[5242880] # No downstream upload, scanner or model execution.
    asyncio.run(scenario())
