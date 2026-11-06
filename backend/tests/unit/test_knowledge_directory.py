"""Scoped discovery/recovery additions, without provider work."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from httpx import ASGITransport,AsyncClient
from test_knowledge import Fixture
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.api.knowledge import router
from airport_assistant.api.dependencies import get_identity
from airport_assistant.main import create_app
from airport_assistant.config import Settings

def test_directory_uses_selected_scope_bounded_lookahead_and_rechecks_admin():
    async def run():
        f=Fixture();ids=[uuid4() for _ in range(3)];f.documents=AsyncMock(return_value=[dict(id=id) for id in ids])
        result=await f.service.directory(f.identity,f.mid,limit=2,document_key='parking')
        f.documents.assert_awaited_once_with(f.org,None,3,'parking')
        assert result['items']==[dict(id=id) for id in ids[:2]] and result['next_after']==ids[1] and result['has_more']
        async def revoke(*args):f.scope=False;return []
        f.documents=AsyncMock(side_effect=revoke)
        with pytest.raises(AuthError):await f.service.directory(f.identity,f.mid)
    asyncio.run(run())

@pytest.mark.parametrize('limit',[0,51,True,'25'])
def test_invalid_directory_bounds(limit):
    f=Fixture()
    with pytest.raises(DraftError):asyncio.run(f.service.directory(f.identity,f.mid,limit=limit))

def test_index_status_uses_actual_basis_proof_and_no_provider():
    async def run():
        f=Fixture();proof=await f.readiness(f.tenant,f.v['id'],f.v['basis_hash']);f.index_proof=AsyncMock(return_value=proof)
        result=await f.service.index_status(f.identity,f.v['id'],f.mid)
        assert result['ready'] and result['generation']==proof.generation and not result['indexing_available']
        f.index_proof.assert_awaited_once_with(f.v['id'],f.v['basis_hash'])
        f.scope=False
        with pytest.raises(AuthError):await f.service.index_status(f.identity,f.v['id'],f.mid)
    asyncio.run(run())

def test_http_directory_queries_and_openapi_are_bounded_no_store():
    async def run():
        f=Fixture();f.service.directory=AsyncMock(return_value=dict(membership_id=f.mid,organization_id=f.org,items=[],limit=25,has_more=False,next_after=None,consistency='live_keyset'))
        app=create_app(Settings(env='test',_env_file=None));app.include_router(router);app.state.services=replace(app.state.services,knowledge=f.service);app.dependency_overrides[get_identity]=lambda:f.identity
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
            path='/api/v1/admin/knowledge/documents';q={'membership_id':str(f.mid)}
            r=await c.get(path,params=q);assert r.status_code==200 and r.headers['cache-control']=='no-store'
            for bad in [dict(limit=0),dict(limit=51),dict(document_key='bad key')]:assert (await c.get(path,params=q|bad)).status_code==422
            assert (await c.get(path,params=q|{'tenant_id':'forged'})).status_code==400
            assert (await c.get(path,params=[('membership_id',str(f.mid)),('membership_id',str(f.mid))])).status_code==400
            assert (await c.get(path,params=q,headers={'x-session-id':'forged'})).status_code==400
            spec=app.openapi();assert 'get' in spec['paths'][path] and 'get' in spec['paths']['/api/v1/admin/knowledge/versions/{id}/index']
    asyncio.run(run())
