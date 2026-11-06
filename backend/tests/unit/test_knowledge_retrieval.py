"""KNOW-02 deterministic chunking, provider bounds and fail-closed lifecycle."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from airport_assistant.modules.knowledge.retrieval import chunk_text, normalized, KnowledgeRetrieval, Search, proof
from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.integrations.embeddings.openai import OpenAIEmbeddings
from airport_assistant.knowledge_config import KnowledgeSettings


@pytest.mark.parametrize('size',[1,64,320,384,385,704,705,20000])
def test_chunk_offsets_coverage_and_overlap(size):
    body=('Airport information. '+chr(0x1f6eb))*1000
    body=body[:size]
    chunks=chunk_text(body)
    assert len(chunks)<=63
    assert chunks[-1].start+len(chunks[-1].body)==len(body)
    assert ''.join(c.body if i==0 else c.body[64:] for i,c in enumerate(chunks))==body
    for c in chunks:
        assert c.start==c.ordinal*320 and c.body==body[c.start:c.start+384]
    assert chunk_text(body)==chunks


@pytest.mark.parametrize('body',['','   ','abc\0def','a'*20001])
def test_invalid_body_rejected(body):
    with pytest.raises(DraftError):chunk_text(body)


@pytest.mark.parametrize('value',[[0]*1536,[float('nan')]*1536,[float('inf')]*1536,[True]*1536,[1]*4])
def test_vector_contract(value):
    with pytest.raises(DraftError):normalized(value)


def test_configuration_opt_in_and_no_evaluation_key_fallback(monkeypatch):
    monkeypatch.setenv('EVAL_OPENAI_API_KEY','not-production')
    assert not KnowledgeSettings(_env_file=None).enabled
    with pytest.raises(ValidationError):KnowledgeSettings(enabled=True,_env_file=None)
    assert 'test-key' not in repr(KnowledgeSettings(enabled=True,api_key='test-key',_env_file=None))


@pytest.mark.parametrize('mode',['ok','401','redirect','bad-model','bad-order','bad-vector','malformed'])
def test_embedding_adapter_contract_and_sanitized_failure(mode):
    calls=[]
    def handler(request):
        calls.append(request)
        assert str(request.url)=='https://api.openai.com/v1/embeddings'
        if mode in {'401','redirect'}:return httpx.Response(401 if mode=='401' else 307,headers={'Location':'https://evil.test'},json={'secret':'do-not-leak'})
        if mode=='malformed':return httpx.Response(200,content=b'not-json')
        return httpx.Response(200,json={'model':'other' if mode=='bad-model' else 'text-embedding-3-small',
            'data':[{'index':True if mode=='bad-order' else 0,'embedding':[0]*1536 if mode=='bad-vector' else [1]*1536}]})
    async def scenario():
        adapter=OpenAIEmbeddings('secret-sentinel',transport=httpx.MockTransport(handler))
        if mode=='ok':assert len((await adapter.embed(['Airport']))[0])==1536
        else:
            with pytest.raises(DraftError) as e:await adapter.embed(['Airport'])
            assert 'secret' not in str(e.value) and e.value.code=='EMBEDDING_UNAVAILABLE'
    asyncio.run(scenario())
    assert len(calls)==1


def test_search_has_no_client_tenant_or_acl_override():
    args=dict(query='parking',service_type='parking',language='en',as_of='2026-01-01T00:00:00Z')
    assert Search.model_validate(args).document_key is None
    for k,v in [('tenant_id',str(uuid4())),('allowed_roles',['admin']),('limit',True),('query',' '),('as_of','2026-01-01')]:
        with pytest.raises(ValidationError):Search.model_validate({**args,k:v})


class Life:
    def __init__(self):
        self.active=False;self.version_row=dict(revision=1,status='draft',basis_hash='basis',body='Policy '*100)
        self.current_proof=None;self.saved=0;self.hook=None
    @asynccontextmanager
    async def transaction(self,identity):
        self.active=True
        try:yield self
        finally:self.active=False
    async def version(self,tx,identity,vid,mid):return {},self.version_row.copy()
    def revision(self,v,expected):
        if v['revision']!=expected:raise DraftError(409,'VERSION_CONFLICT')
    def draft(self,v):
        if v['status']!='draft':raise DraftError(409,'KNOWLEDGE_NOT_DRAFT')
    async def index_proof(self,*args):return self.current_proof
    async def store_chunks(self,vid,basis,generation,chunks,vectors):
        self.saved+=1;self.current_proof=proof(uuid4(),vid,basis,generation)
    async def search_chunks(self,c,vector):assert self.active;return ['authorized']


@pytest.mark.parametrize('race',[False,True])
def test_prepare_revalidates_after_provider_and_is_idempotent(race):
    life=Life();calls=[]
    class Embed:
        async def embed(self,texts):
            assert not life.active;calls.append(texts)
            if race:life.version_row['revision']+=1
            return [[1]*1536 for _ in texts]
    async def scenario():
        service=KnowledgeRetrieval(life,Embed());command=dict(membership_id=str(uuid4()),expected_revision=1)
        if race:
            with pytest.raises(DraftError):await service.prepare(None,uuid4(),command)
            assert life.saved==0
        else:
            first=await service.prepare(None,uuid4(),command)
            assert await service.prepare(None,uuid4(),command)==first
            assert len(calls)==1 and life.saved==1
    asyncio.run(scenario())


def test_whitespace_chunk_accepted_and_slot_released_after_provider_failure():
    calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        if calls==1:return httpx.Response(500)
        return httpx.Response(200,json={'model':'text-embedding-3-small','data':[{'index':0,'embedding':[1]*1536}]})
    async def scenario():
        adapter=OpenAIEmbeddings('fake',transport=httpx.MockTransport(handler))
        with pytest.raises(DraftError):await adapter.embed([' '*384])
        assert len(await adapter.embed([' '*384]))==1
    asyncio.run(scenario())
    assert calls==2


def test_public_embedding_error_codes_are_preserved():
    from airport_assistant.api.errors import error_response
    for code in ('EMBEDDING_BUSY','EMBEDDING_UNAVAILABLE'):
        response=error_response(503,'request',auth_code=code)
        assert code.encode() in response.body


def test_cli_check_rejects_enabled_knowledge_without_auth(tmp_path,monkeypatch,capsys):
    import sys
    from airport_assistant.main import main
    file=tmp_path/'fixture.env'
    file.write_text('APP_ENV=test\nAPP_KNOWLEDGE_ENABLED=true\nAPP_KNOWLEDGE_API_KEY=fake\n',encoding='utf-8')
    monkeypatch.delenv('APP_AUTH_ENABLED',raising=False)
    monkeypatch.setattr(sys,'argv',['airport-assistant-api','--env-file',str(file),'--check-config'])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2 and 'Knowledge requires authentication' in capsys.readouterr().err


def test_composition_binds_enabled_retrieval_without_calling_provider():
    from airport_assistant.composition import build_services
    from airport_assistant.auth_config import AuthSettings
    auth=AuthSettings(SESSION_SECRET='s'*48,_env_file=None)
    disabled=build_services(object(),auth)
    assert disabled.knowledge.retrieval is None
    enabled=build_services(object(),auth,knowledge_settings=KnowledgeSettings(enabled=True,api_key='fake',_env_file=None))
    assert isinstance(enabled.knowledge.retrieval,KnowledgeRetrieval)
    assert isinstance(enabled.knowledge.retrieval.embeddings,OpenAIEmbeddings)


def test_overload_does_not_call_http_and_cancellation_releases_slot():
    async def scenario():
        entered=asyncio.Event();calls=[]
        async def handler(request):
            calls.append(request)
            if len(calls)==1:
                entered.set();await asyncio.Event().wait()
            return httpx.Response(200,json={'model':'text-embedding-3-small','data':[{'index':0,'embedding':[1]*1536}]})
        adapter=OpenAIEmbeddings('fake',transport=httpx.MockTransport(handler))
        task=asyncio.create_task(adapter.embed(['first']))
        await entered.wait()
        with pytest.raises(DraftError) as busy:await adapter.embed(['overload'])
        assert busy.value.code=='EMBEDDING_BUSY' and len(calls)==1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert await adapter.embed(['after cancellation'])
        assert len(calls)==2
    asyncio.run(scenario())
