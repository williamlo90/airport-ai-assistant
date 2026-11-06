"""Staff administration DTO/no-store/redaction and explicit organization selection."""
import asyncio
from dataclasses import replace
from httpx import ASGITransport,AsyncClient
from test_staff_administration import Fixture
from airport_assistant.api.administration import router
from airport_assistant.api.dependencies import get_staff_identity,get_mutation_identity
from airport_assistant.main import create_app
from airport_assistant.config import Settings


def app_for(f):
    app=create_app(Settings(env='test',_env_file=None));app.include_router(router)
    app.state.services=replace(app.state.services,administration=f.service)
    app.dependency_overrides[get_staff_identity]=lambda:f.identity
    app.dependency_overrides[get_mutation_identity]=lambda:f.identity
    return app


def test_http_selected_workspace_directory_member_and_versioned_edit():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            query=dict(membership_id=str(f.actor))
            workspace=await c.get('/api/v1/admin/workspace',params=query)
            assert workspace.status_code==200 and workspace.headers['cache-control']=='no-store'
            directory=await c.get('/api/v1/admin/staff',params=query)
            assert directory.status_code==200 and directory.json()['items']==[f.member]
            route='/api/v1/admin/staff/'+str(f.target)+'/memberships'
            assert (await c.get(route,params=query)).json()['member']==f.member
            body=f.command().model_dump(mode='json')
            response=await c.patch(route,json=body);assert response.status_code==200 and response.json()['member']['version']==2
            assert response.headers['cache-control']=='no-store'
            assert (await c.patch(route,json=body)).json()==response.json() and f.audits==1
            assert not any(value in response.text for value in ('password','contact_email','customer_report','source_subject'))
    asyncio.run(scenario())


def test_http_extra_fields_claimed_scope_duplicates_and_bad_versions_do_not_write():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            route='/api/v1/admin/staff/'+str(f.target)+'/memberships';base=f.command().model_dump(mode='json')
            for changes in (dict(active='true'),dict(expected_version=True),dict(roles=['root']),dict(password='private-sentinel')):
                response=await c.patch(route,json=base|changes)
                assert response.status_code==422 and 'private-sentinel' not in response.text
            response=await c.patch(route+'?tenant_id=forged',json=base);assert response.status_code==400
            response=await c.get('/api/v1/admin/workspace',params=[('membership_id',str(f.actor)),('membership_id',str(f.actor))]);assert response.status_code==400
            response=await c.get('/api/v1/admin/workspace',params={'membership_id':str(f.actor)},headers={'x-principal-id':'forged'});assert response.status_code==400
            assert not f.audits
    asyncio.run(scenario())


def test_http_self_edit_and_current_authority_loss_cannot_be_acknowledged():
    async def scenario():
        f=Fixture();app=app_for(f)
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            response=await c.patch('/api/v1/admin/staff/'+str(f.identity.session.principal_id)+'/memberships',json=f.command().model_dump(mode='json'))
            assert response.status_code==403
            f.scope_changed=True
            response=await c.patch('/api/v1/admin/staff/'+str(f.target)+'/memberships',json=f.command().model_dump(mode='json'))
            assert response.status_code==409 and f.audits==0 and f.member['version']==1
    asyncio.run(scenario())
