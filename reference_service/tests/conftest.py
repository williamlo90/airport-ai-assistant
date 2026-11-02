from pathlib import Path
from uuid import UUID
import asyncio
import httpx
import pytest
from airport_reference.config import Settings
from airport_reference.main import create_app
from airport_reference.seed import seed_database
from airport_reference.security import signed_headers

A=UUID('11111111-1111-4111-8111-111111111111')
B=UUID('22222222-2222-4222-8222-222222222222')
TOKEN='fictional-service-token-'+'a'*32
SECRET='fictional-context-secret-'+'b'*32


@pytest.fixture
def source(tmp_path):
    path=tmp_path/'reference.sqlite3';seed_database(path)
    settings=Settings(env='test',database_path=path,service_token=TOKEN,context_secret=SECRET)
    return create_app(settings),path,settings


def request(source,path,*,subject='owner',tenant=A,params=None,method='GET',headers=None,body=None,base='http://127.0.0.1:8010',peer='127.0.0.1'):
    app=source[0]
    url=httpx.URL(base+path,params=params)
    auth=signed_headers(TOKEN,SECRET,tenant,subject,path=url.raw_path.partition(b'?')[0].decode(),query=url.query)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(peer,123)),base_url=base) as client:
            return await client.request(method,url,headers=auth if headers is None else headers,content=body)
    return asyncio.run(run())
