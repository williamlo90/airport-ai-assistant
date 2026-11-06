"""HTTP DTO/redaction boundaries; identity and persistence are controlled here."""
import asyncio
from dataclasses import replace
from uuid import uuid4

from httpx import ASGITransport,AsyncClient

from test_customer_information import Fixture
from airport_assistant.api.case_lifecycle import router
from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.config import Settings
from airport_assistant.main import create_app


def app_for(f):
    app=create_app(Settings(env='test',_env_file=None))
    app.state.services=replace(app.state.services,case_lifecycle=object(),customer_information=f.service)
    app.include_router(router)
    app.dependency_overrides[get_identity]=lambda:f.identity
    app.dependency_overrides[get_mutation_identity]=lambda:f.identity
    return app


def test_http_preview_confirmation_receipt_and_once_only_intent():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            prefix='/api/v1/cases/'+str(f.cid)
            preview=await c.post(prefix+'/information-previews',json=f.command.model_dump())
            assert preview.status_code==200 and preview.headers['cache-control']=='no-store'
            token=preview.json()['confirmation_token'];key=uuid4().hex
            response=await c.post(prefix+'/information',json=dict(confirmation_token=token,confirmed=True),headers={'Idempotency-Key':key})
            assert response.status_code==201 and response.json()['state']=='saved'
            saved=await c.get(prefix+'/information-receipts/'+key)
            assert saved.status_code==200 and saved.json()==response.json() and saved.headers['cache-control']=='no-store'
            replay=await c.post(prefix+'/information',json=dict(confirmation_token=token,confirmed=True),headers={'Idempotency-Key':key})
            assert replay.json()==saved.json() and f.writes==1
            missing=await c.get(prefix+'/information-receipts/'+uuid4().hex)
            assert missing.status_code==404 and f.writes==1
    asyncio.run(scenario())


def test_http_invalid_confirmation_does_not_echo_token_or_publish():
    async def scenario():
        f=Fixture();app=app_for(f);sentinel='private-confirmation-token-sentinel'
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path='/api/v1/cases/'+str(f.cid)+'/information'
            for confirmed in [False,1,'true',None]:
                r=await c.post(path,json=dict(confirmation_token=sentinel,confirmed=confirmed),headers={'Idempotency-Key':uuid4().hex})
                assert r.status_code==422 and sentinel not in r.text and r.headers['cache-control']=='no-store'
            r=await c.post(path,json=dict(confirmation_token=sentinel,confirmed=True),headers={'Idempotency-Key':uuid4().hex})
            assert r.status_code==400 and sentinel not in r.text and f.writes==0
    asyncio.run(scenario())


def test_http_duplicate_key_headers_and_query_identity_injection_rejected():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            prefix='/api/v1/cases/'+str(f.cid)
            token=(await c.post(prefix+'/information-previews',json=f.command.model_dump())).json()['confirmation_token']
            r=await c.post(prefix+'/information',json=dict(confirmation_token=token,confirmed=True),headers=[('Idempotency-Key',uuid4().hex),('Idempotency-Key',uuid4().hex)])
            assert r.status_code==400
            for kwargs in [dict(params={'source_subject':'forged'}),dict(headers={'x-principal-id':str(uuid4())})]:
                r=await c.post(prefix+'/information-previews',json=f.command.model_dump(),**kwargs)
                assert r.status_code==400
            assert f.writes==0
    asyncio.run(scenario())


def test_http_expiry_has_specific_public_error_and_no_write():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            prefix='/api/v1/cases/'+str(f.cid)
            token=(await c.post(prefix+'/information-previews',json=f.command.model_dump())).json()['confirmation_token']
            f.clock[0]=1900
            r=await c.post(prefix+'/information',json=dict(confirmation_token=token,confirmed=True),headers={'Idempotency-Key':uuid4().hex})
            assert r.status_code==410 and r.json()['error']['code']=='PREVIEW_EXPIRED' and token not in r.text
            assert f.writes==0
    asyncio.run(scenario())
