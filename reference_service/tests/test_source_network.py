"""Normal TCP + signed local demo CLI, including safe redirect handling."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
from airport_reference.seed import seed_database
from airport_reference.security import signed_headers
from conftest import A,TOKEN,SECRET


def test_tcp_and_demo_use_normal_authentication_and_stop_server(tmp_path):
    db=tmp_path/'reference.sqlite3';seed_database(db)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    envfile=tmp_path/'.env'
    envfile.write_text(f"REF_ENV=test\nREF_PORT={port}\nREF_DATABASE_PATH='{db.as_posix()}'\nREF_SERVICE_TOKEN={TOKEN}\nREF_CONTEXT_SECRET={SECRET}\n",encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith('REF_') and k not in {'PYTHONPATH','VIRTUAL_ENV'}}
    logpath=tmp_path/'server.log'
    with logpath.open('w+',encoding='utf-8') as log:
        server=subprocess.Popen([sys.executable,'-m','airport_reference.main','--env-file',str(envfile)],env=env,stdout=log,stderr=log)
        try:
            with httpx.Client(trust_env=False,follow_redirects=False,timeout=2) as client:
                base=f'http://127.0.0.1:{port}'
                for _ in range(100):
                    assert server.poll() is None
                    try:
                        if client.get(base+'/health/live').status_code==200:break
                    except httpx.TransportError:time.sleep(.05)
                else:raise AssertionError('Reference API startup deadline exceeded.')
                path='/source/v1/bookings/PK-A-001'
                assert client.get(base+path).status_code==401
                proof=signed_headers(TOKEN,SECRET,A,'owner',path=path)
                result=client.get(base+path,headers=proof)
                assert result.status_code==200 and result.json()['reference']=='PK-A-001'
                sentinel=client.get(base+path+'?private=secret-query-sentinel',headers=proof)
                assert sentinel.status_code==401
            demo=subprocess.run([sys.executable,'-m','airport_reference.demo','transaction','TX-A-001','--subject','beneficiary','--env-file',str(envfile)],env=env,capture_output=True,text=True,timeout=10)
            assert demo.returncode==0 and 'HTTP 200' in demo.stdout
            assert 'amount_minor' not in demo.stdout and TOKEN not in demo.stdout+demo.stderr and SECRET not in demo.stdout+demo.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
        log.seek(0);text=log.read()
        assert TOKEN not in text and SECRET not in text and 'secret-query-sentinel' not in text
    assert server.poll() is not None


def test_demo_refuses_redirect_and_never_uses_proxy_or_external_host(tmp_path,monkeypatch,capsys):
    from airport_reference import demo
    from airport_reference.config import Settings
    monkeypatch.setattr(sys,'argv',['demo','booking','PK-A-001'])
    monkeypatch.setattr(demo,'load_settings',lambda *a:Settings(service_token=TOKEN,context_secret=SECRET))
    class Client:
        def __init__(self,**kwargs):assert kwargs==dict(trust_env=False,follow_redirects=False,timeout=5)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            assert url.startswith('http://127.0.0.1:8010/')
            return httpx.Response(302,headers={'Location':'https://evil.example.test'},json={'Authorization':TOKEN})
    monkeypatch.setattr(demo.httpx,'Client',Client)
    import pytest
    with pytest.raises(SystemExit):demo.main()
    output=capsys.readouterr().out
    assert 'redacted' in output and TOKEN not in output
