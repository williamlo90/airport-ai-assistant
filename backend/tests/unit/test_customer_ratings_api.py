"""HTTP request/redaction contracts; identity and persistence are controlled."""
import asyncio
from dataclasses import replace
from httpx import ASGITransport,AsyncClient
from test_customer_ratings import Fixture
from airport_assistant.api.ratings import router
from airport_assistant.api.dependencies import get_identity,get_mutation_identity
from airport_assistant.main import create_app
from airport_assistant.config import Settings


def app_for(f):
    app=create_app(Settings(env='test',_env_file=None));app.include_router(router)
    app.state.services=replace(app.state.services,ratings=f.service)
    app.dependency_overrides[get_identity]=lambda:f.identity
    app.dependency_overrides[get_mutation_identity]=lambda:f.identity
    return app


def test_http_optional_original_feedback_reads_and_idempotent_upsert():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            route='/api/v1/cases/'+str(f.id)+'/ratings'
            response=await c.get(route);assert response.status_code==200 and response.json()['rating'] is None
            assert response.headers['cache-control']=='no-store'
            body=dict(expected_version=0,score=5,comment='Original feedback, not a case closure.')
            first=await c.post(route,json=body);assert first.status_code==200 and first.json()['rating']['score']==5
            assert first.headers['cache-control']=='no-store'
            again=await c.post(route,json=body);assert again.json()==first.json() and f.writes==1
            assert (await c.get(route)).json()==first.json()
            assert not any(key in first.text for key in ('resource_security_labels','created_session_id','tenant_id'))
    asyncio.run(scenario())


def test_http_rejects_bad_score_scope_and_redacts_invalid_comment():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            route='/api/v1/cases/'+str(f.id)+'/ratings'
            for body in [dict(expected_version=0,score=True),dict(expected_version=0,score=0),dict(expected_version=0,score=6),
                         dict(expected_version=0,score=5,comment='private-sentinel\x00'),dict(expected_version=0,score=5,tenant_id=str(f.identity.session.tenant_id))]:
                response=await c.post(route,json=body)
                assert response.status_code==422 and 'private-sentinel' not in response.text
            assert (await c.post(route,params={'tenant_id':'forged'},json=dict(expected_version=0,score=5))).status_code==400
            assert (await c.get(route,headers={'x-principal-id':'forged'})).status_code==400
            assert not f.writes
    asyncio.run(scenario())


def test_http_anonymous_conversation_is_separate_from_verified_case_feedback():
    async def scenario():
        f=Fixture(kind='anonymous');app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.post('/api/v1/cases/'+str(f.id)+'/ratings',json=dict(expected_version=0,score=4))).status_code==401
            response=await c.post('/api/v1/conversations/'+str(f.id)+'/ratings',json=dict(expected_version=0,score=4))
            assert response.status_code==200 and response.json()['target_type']=='conversation'
    asyncio.run(scenario())
