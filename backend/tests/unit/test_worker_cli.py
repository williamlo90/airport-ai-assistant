"""Worker startup never borrows API credentials or invents a success handler."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from airport_assistant import worker
from airport_assistant.config import ConfigurationError
from airport_assistant.composition import load_worker_configuration
from airport_assistant.worker_config import WorkerSettings


def test_worker_missing_url_does_not_fall_back_to_api_credential(tmp_path,monkeypatch):
    monkeypatch.delenv('WORKER_DATABASE_URL',raising=False)
    path=tmp_path/'worker-test.env'
    path.write_text('DATABASE_URL=postgresql://sample:private-sentinel@ep-test-pooler.neon.tech/demo?sslmode=require\n',encoding='utf-8')
    with pytest.raises(ConfigurationError) as error:load_worker_configuration(path)
    assert 'WORKER_DATABASE_URL' in str(error.value) and 'private-sentinel' not in str(error.value)


def test_check_config_does_not_connect_or_reveal_url(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['worker','--no-env-file','--check-config'])
    monkeypatch.setenv('WORKER_DATABASE_URL','postgresql://sample:private-sentinel@ep-test-pooler.neon.tech/demo?sslmode=require')
    monkeypatch.setattr(worker,'build_worker_resources',lambda _:pytest.fail('Config-only must not connect.'))
    worker.main();assert capsys.readouterr().out=='Worker configuration valid.\n'


def test_once_without_handlers_verifies_connection_but_never_claims(capsys):
    database=AsyncMock();scheduler=AsyncMock()
    asyncio.run(worker.run(database,WorkerSettings(),{},scheduler=scheduler,once=True))
    database.open.assert_awaited_once();database.close.assert_awaited_once();scheduler.claim.assert_not_awaited()
    assert 'queued jobs untouched' in capsys.readouterr().out
    assert worker.build_handlers()=={}


def test_daemon_without_handlers_fails_instead_of_spinning_idle():
    database=AsyncMock();scheduler=AsyncMock()
    with pytest.raises(ValueError):asyncio.run(worker.run(database,WorkerSettings(),{},scheduler=scheduler))
    database.close.assert_awaited_once();scheduler.claim.assert_not_awaited()
