"""Release-specific proxy and readiness boundaries, with no network services."""
import asyncio,sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI,Request
from httpx import ASGITransport,AsyncClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from airport_assistant.config import Settings
from airport_assistant.main import create_app
from airport_assistant.api.security import context
from airport_assistant.api.errors import install_error_handlers
from airport_assistant.api.boundary import RequestBoundary

@pytest.mark.parametrize('mode,env,valid',[('--trusted-loopback-proxy','production',True),('--trusted-loopback-proxy','development',False),('--local-proxy','production',False)])
def test_explicit_proxy_mode(monkeypatch,mode,env,valid):
 import airport_assistant.main as main
 for name in list(__import__('os').environ):
  if name.startswith(('APP_','SOURCE_')) or name in {'DATABASE_URL','SESSION_SECRET'}:monkeypatch.delenv(name,raising=False)
 monkeypatch.setenv('APP_ENV',env);monkeypatch.setenv('APP_HOST','0.0.0.0')
 captured={};monkeypatch.setattr(sys,'argv',['api','--no-env-file',mode])
 monkeypatch.setattr(main.uvicorn,'run',lambda app,**kw:captured.update(kw))
 if valid:
  main.main();assert captured['host']=='127.0.0.1' and captured['proxy_headers'] is True and captured['forwarded_allow_ips']=='127.0.0.1'
 else:
  with pytest.raises(SystemExit) as e:main.main()
  assert e.value.code==2 and not captured

def test_proxy_flags_cannot_be_combined(monkeypatch):
 import airport_assistant.main as main
 monkeypatch.setenv('APP_ENV','production');monkeypatch.setattr(sys,'argv',['api','--no-env-file','--local-proxy','--trusted-loopback-proxy'])
 with pytest.raises(SystemExit) as error:main.main()
 assert error.value.code==2

@pytest.mark.parametrize('peer,proto,expected',[('127.0.0.1','https',200),('203.0.113.9','https',403),('127.0.0.1','http',403),('127.0.0.1',None,403)])
def test_only_loopback_forwarding_satisfies_real_https_origin_gate(peer,proto,expected):
 async def scenario():
  app=FastAPI();install_error_handlers(app);app.state.services=SimpleNamespace(auth=SimpleNamespace(tenant=AsyncMock(return_value='synthetic-tenant')))
  @app.post('/gate')
  async def gate(request:Request):await context(request,mutation=True);return {'ok':True}
  app.add_middleware(RequestBoundary,max_body_bytes=1_048_576)
  wrapped=ProxyHeadersMiddleware(app,trusted_hosts='127.0.0.1')
  async with AsyncClient(transport=ASGITransport(app=wrapped,client=(peer,1234)),base_url='http://westbridge.example') as client:
   headers={'Origin':'https://westbridge.example','X-Forwarded-For':'198.51.100.4'}
   if proto is not None:headers['X-Forwarded-Proto']=proto
   r=await client.post('/gate',headers=headers)
   assert r.status_code==expected
 asyncio.run(scenario())

@pytest.mark.parametrize('state,expected',[('up',200),('down',503),('missing',503),('wrong_row',503),('slow',503)])
def test_readiness_checks_core_db_and_redacts_failure(state,expected):
 async def scenario():
  app=create_app(Settings(env='test'))
  from airport_assistant.infrastructure.database.connections import RuntimeDatabase
  class DB:
   ready=RuntimeDatabase.ready
   @asynccontextmanager
   async def transaction(self):
    if state=='slow':await asyncio.sleep(3)
    if state=='down':raise RuntimeError('must-not-leak-dsn')
    yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value=(0,) if state=='wrong_row' else (1,)))))
  app.state.resources=SimpleNamespace(database=None if state=='missing' else DB())
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://localhost') as client:
   r=await client.get('/health/ready');assert r.status_code==expected and r.headers['cache-control']=='no-store'
   assert 'must-not-leak' not in r.text
   live=await client.get('/health/live');assert live.status_code==200
 asyncio.run(scenario())
