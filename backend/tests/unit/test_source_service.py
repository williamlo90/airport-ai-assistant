"""Trusted mapping, opt-in composition and API identity/projection boundaries."""

import asyncio
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import pytest
from airport_assistant.modules.identity.auth import Session,AuthError
from airport_assistant.modules.identity.access import AccessIdentity
from airport_assistant.modules.identity.membership import Membership
from airport_assistant.modules.source.models import SourceFailure,SourceCaller
from airport_assistant.modules.source.service import SourceReadService
from airport_assistant.composition import ApplicationResources


def identity(kind='customer',tenant=None):
    s=Session(uuid4(),tenant or uuid4(),None if kind=='anonymous' else uuid4(),kind,datetime.now(timezone.utc)+timedelta(hours=1),'private')
    return AccessIdentity(s,(Membership(uuid4(),uuid4(),1,{'cs'},set()),) if kind=='staff' else ())


@pytest.mark.parametrize('kind',['anonymous','staff'])
def test_customer_reads_do_not_promote_other_session_kinds(kind):
    async def scenario():
        actor=identity(kind);reader=Mock();reader.booking=AsyncMock();repo=Mock();repo.find_subject=AsyncMock(return_value='owner')
        service=SourceReadService(reader,repo,{actor.session.tenant_id:uuid4()})
        with pytest.raises(AuthError) as exc:await service.booking(actor,'PK-A-001')
        assert exc.value.status==401;repo.find_subject.assert_not_awaited();reader.booking.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('missing',['tenant','subject','invalid_subject'])
def test_missing_or_invalid_mapping_never_calls_provider(missing):
    async def scenario():
        actor=identity();reader=Mock();reader.booking=AsyncMock();repo=Mock();repo.find_subject=AsyncMock(return_value='illegal@example.com' if missing=='invalid_subject' else None)
        service=SourceReadService(reader,repo,{} if missing=='tenant' else {actor.session.tenant_id:uuid4()})
        with pytest.raises(SourceFailure) as exc:await service.booking(actor,'PK-A-001')
        assert exc.value.status==(503 if missing=='invalid_subject' else 404)
        reader.booking.assert_not_awaited()
    asyncio.run(scenario())


def test_mapping_is_server_owned_copied_and_reloaded_for_each_call():
    async def scenario():
        actor=identity();tenant=uuid4();mapping={actor.session.tenant_id:tenant}
        reader=Mock();reader.booking=AsyncMock(return_value='observation')
        repo=Mock();repo.find_subject=AsyncMock(side_effect=['owner','beneficiary'])
        service=SourceReadService(reader,repo,mapping);mapping.clear()
        for _ in range(2):assert await service.booking(actor,'PK-A-001')=='observation'
        assert reader.booking.await_args_list[0].args==(SourceCaller(tenant,'owner'),'PK-A-001')
        assert reader.booking.await_args_list[1].args==(SourceCaller(tenant,'beneficiary'),'PK-A-001')
        assert all(call.args==(actor.database_context,) for call in repo.find_subject.await_args_list)
    asyncio.run(scenario())


def test_resource_shutdown_closes_http_even_if_db_startup_fails():
    class DB:
        async def open(self):raise RuntimeError('unavailable')
        close=AsyncMock()
    async def scenario():
        db=DB();http=Mock();http.aclose=AsyncMock()
        with pytest.raises(RuntimeError):
            async with ApplicationResources(db,http).lifecycle():pass
        http.aclose.assert_awaited_once();db.close.assert_awaited_once()
    asyncio.run(scenario())



def test_local_source_setup_preserves_other_secrets_and_never_provisions_customer(tmp_path,monkeypatch,capsys):
    import sys
    from dotenv import dotenv_values
    from airport_assistant import source_setup
    tenant=uuid4();env=tmp_path/'.env';ref=tmp_path/'reference.env'
    env.write_text("APP_ENV=test\nAPP_AUTH_ENABLED=true\nAPP_DATABASE_ENABLED=true\nDATABASE_URL=preserve-runtime-secret\nSESSION_SECRET=preserve-session-secret\nOTHER=keep-me\n",encoding='utf-8')
    token='fake-reference-token-'+'t'*32;key='fake-reference-context-'+'c'*32
    ref.write_text(f'REF_SERVICE_TOKEN={token}\nREF_CONTEXT_SECRET={key}\nREF_PORT=8010\n',encoding='utf-8')
    class DB:
        def __init__(self,*a):pass
        async def open(self):pass
        async def close(self):pass
    class Repo:
        def __init__(self,*a):pass
        async def tenant_for_host(self,host):assert host=='localhost';return tenant
    runtime=Mock();runtime.url.get_secret_value.return_value='private-dsn-sentinel'
    monkeypatch.setattr(source_setup,'load_database_settings',lambda *a:runtime)
    monkeypatch.setattr(source_setup,'RuntimeDatabase',DB)
    monkeypatch.setattr(source_setup,'PostgresAuthRepository',Repo)
    monkeypatch.setattr(sys,'argv',['source-setup','--env-file',str(env),'--reference-env',str(ref)])
    source_setup.main();values=dotenv_values(env)
    assert values['DATABASE_URL']=='preserve-runtime-secret' and values['SESSION_SECRET']=='preserve-session-secret' and values['OTHER']=='keep-me'
    assert values['SOURCE_API_TOKEN']==token and values['SOURCE_CONTEXT_SECRET']==key and values['APP_SOURCE_ENABLED']=='true'
    source_setup.main();assert values==dotenv_values(env)
    output=capsys.readouterr().out
    assert all(secret not in output for secret in [token,key,'preserve-runtime-secret','preserve-session-secret','private-dsn-sentinel'])


def test_source_config_diagnostics_hide_values(tmp_path):
    from airport_assistant.source_config import load_source_settings
    from airport_assistant.config import ConfigurationError
    env=tmp_path/'.env';env.write_text('SOURCE_API_BASE_URL=https://secret:bad@source.test\nSOURCE_API_TOKEN=private-value-sentinel\n')
    with pytest.raises(ConfigurationError) as exc:load_source_settings(env)
    assert 'private-value-sentinel' not in str(exc.value) and 'secret:bad' not in str(exc.value)



@pytest.mark.parametrize('scheme',['http','HTTP','HtTp'])
def test_production_rejects_http_regardless_of_case_before_resource_creation(monkeypatch,scheme):
    from airport_assistant import main
    from airport_assistant.source_config import SourceSettings
    from airport_assistant.config import Settings,ConfigurationError
    from airport_assistant.database_config import RuntimeDatabaseSettings
    from airport_assistant.auth_config import AuthSettings
    source=SourceSettings(SOURCE_API_BASE_URL=scheme+'://127.0.0.1:8010',SOURCE_API_TOKEN='fake-service-token-'+'t'*32,
                          context_secret='fake-context-key-'+'c'*32,tenant_map={uuid4():uuid4()},allow_loopback_http=True)
    assert source.base_url.startswith('http:')
    monkeypatch.setattr(main,'build_resources',lambda *a:pytest.fail('Production HTTP must fail before creating resources'))
    with pytest.raises(ConfigurationError):main.create_app(Settings(env='production',database_enabled=True,auth_enabled=True,source_enabled=True),
        database_settings=RuntimeDatabaseSettings(DATABASE_URL='postgresql://test:fake@ep-test-pooler.region.aws.neon.tech/db?sslmode=require'),
        auth_settings=AuthSettings(SESSION_SECRET='k'*48),source_settings=source)



@pytest.mark.parametrize('mode',['production','remote'])
def test_operator_diagnostic_never_sends_fixture_claims_to_production_or_remote(monkeypatch,capsys,mode):
    import sys
    from types import SimpleNamespace
    from airport_assistant import source_check
    from airport_assistant.source_config import SourceSettings
    source=SourceSettings(SOURCE_API_BASE_URL='https://source.test' if mode=='remote' else 'http://127.0.0.1:8010',
                          SOURCE_API_TOKEN='fake-token-'+'a'*40,context_secret='fake-key-'+'b'*40,
                          tenant_map={uuid4():uuid4()},allow_loopback_http=True)
    monkeypatch.setattr(sys,'argv',['source-check','booking','PK-A-001'])
    monkeypatch.setattr(source_check,'load_settings',lambda *a:SimpleNamespace(env='production' if mode=='production' else 'test'))
    monkeypatch.setattr(source_check,'load_source_settings',lambda *a:source)
    monkeypatch.setattr(source_check,'SourceHttpClient',lambda *a,**k:pytest.fail('Diagnostic must reject before network client'))
    with pytest.raises(SystemExit) as error:source_check.main()
    assert error.value.code==1 and 'redacted' in capsys.readouterr().out
