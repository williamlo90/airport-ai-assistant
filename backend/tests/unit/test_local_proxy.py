"""Local HTTPS proxy trust must be explicit and restricted to loopback."""
import sys

import pytest

from airport_assistant.config import Settings
from airport_assistant import main as entry


@pytest.mark.parametrize('local_proxy', [False, True])
def test_cli_proxy_is_opt_in_and_never_wildcard(monkeypatch, local_proxy):
    settings = Settings(env='development', host='0.0.0.0', port=9000, _env_file=None)
    monkeypatch.setattr(entry, 'load_settings', lambda _: settings)
    application = object()
    monkeypatch.setattr(entry, 'create_app', lambda *a, **kw: application)
    recorded = {}
    monkeypatch.setattr(entry.uvicorn, 'run', lambda app, **kw: recorded.update(kw))
    monkeypatch.setattr(sys, 'argv', ['api', '--no-env-file'] + (['--local-proxy'] if local_proxy else []))
    entry.main()
    assert recorded['proxy_headers'] is local_proxy
    assert recorded['forwarded_allow_ips'] == ('127.0.0.1' if local_proxy else '')
    assert recorded['host'] == ('127.0.0.1' if local_proxy else '0.0.0.0')
    assert recorded['port'] == (8001 if local_proxy else 9000)
    assert recorded['access_log'] is False


def test_proxy_flag_refuses_production(monkeypatch, capsys):
    monkeypatch.setattr(entry, 'load_settings', lambda _: Settings(env='production', _env_file=None))
    monkeypatch.setattr(sys, 'argv', ['api', '--no-env-file', '--local-proxy'])
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 2
    assert 'requires APP_ENV=development' in capsys.readouterr().err
