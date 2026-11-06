"""Strict native verdicts and actual owned-helper lifecycle on the DB event loop."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from airport_assistant.composition import database_loop_factory
from airport_assistant.config import Settings
from airport_assistant.infrastructure.security import amsi_scanner as module
from airport_assistant.infrastructure.security.amsi_scanner import AmsiScanner
from airport_assistant.modules.attachments.service import MAX_BYTES
from airport_assistant.modules.drafts.models import DraftError

BODY=b'opaque\x00binary\xffdata'


def packet(result=1,**extra):
    return json.dumps(dict(hresult=0,result=result,byte_size=len(BODY),sha256=hashlib.sha256(BODY).hexdigest(),**extra)).encode()


@pytest.mark.parametrize('result,clean',[(0,True),(1,True),(16384,False),(20479,False),(32768,False),(0xffffffff,False)])
def test_only_explicit_clean_and_block_ranges(result,clean):assert AmsiScanner._verdict(packet(result),BODY) is clean


@pytest.mark.parametrize('result',[2,16383,20480,32767,-1,True,None,'1'])
def test_unknown_provider_risk_never_becomes_clean(result):
    with pytest.raises(DraftError):AmsiScanner._verdict(packet(result),BODY)


@pytest.mark.parametrize('change',[{'hresult':1},{'hresult':-1},{'byte_size':1},{'sha256':'0'*64}])
def test_status_and_exact_scanned_buffer_binding(change):
    value=json.loads(packet());value.update(change)
    with pytest.raises(DraftError):AmsiScanner._verdict(json.dumps(value).encode(),BODY)


def test_duplicate_json_and_trailing_output_fail_closed():
    value=packet()
    for output in [value+b'garbage',b'{"hresult":7,"hresult":0,"result":1,"byte_size":1,"sha256":"x"}']:
        with pytest.raises(DraftError):AmsiScanner._verdict(output,BODY)


def test_local_only_configuration_and_minimal_environment(monkeypatch):
    assert Settings(env='development',attachment_scanner_backend='amsi-local',_env_file=None).attachment_scanner_port is None
    for args in [dict(env='production'),dict(env='test',attachment_scanner_port=3310)]:
        with pytest.raises(ValidationError):Settings(**args,attachment_scanner_backend='amsi-local',_env_file=None)
    monkeypatch.setenv('OPENAI_API_KEY','secret-sentinel');monkeypatch.setenv('DATABASE_URL','secret-sentinel')
    assert 'secret-sentinel' not in str(AmsiScanner._environment())


def controlled(monkeypatch,tmp_path,mode):
    script=tmp_path/'controlled_native_child.py'
    script.write_text('''import sys,time,json,hashlib
mode=sys.argv[1]
if mode=='hang_input':time.sleep(60)
data=sys.stdin.buffer.read()
if mode=='hang_output':time.sleep(60)
if mode=='partial':
 sys.stdout.write('{');sys.stdout.flush();time.sleep(60)
if mode=='oversize':print('x'*257);raise SystemExit()
print(json.dumps(dict(hresult=0,result=32768 if mode=='detected' else 1,byte_size=len(data),sha256=hashlib.sha256(data).hexdigest())))
if mode=='nonzero':raise SystemExit(3)
''',encoding='utf-8')
    original=module.subprocess.Popen;children=[]
    def spawn(args,**kwargs):
        assert Path(args[0]).is_absolute() and args[1]=='-I'
        assert Path(args[2]).is_absolute() and Path(args[2]).name=='amsi_helper.py'
        assert kwargs['shell'] is False and kwargs['close_fds'] is True
        assert 'text' not in kwargs and kwargs['stderr']==module.subprocess.DEVNULL
        assert 'OPENAI_API_KEY' not in kwargs['env'] and 'DATABASE_URL' not in kwargs['env']
        # Unix controlled tests can exercise the transport without Windows flags.
        if sys.platform!='win32':kwargs['creationflags']=0
        p=original([args[0],'-I',str(script),mode],**kwargs);children.append(p);return p
    monkeypatch.setattr(module.subprocess,'Popen',spawn)
    monkeypatch.setattr(module,'WINDOWS_SUPPORTED',True)
    return children


def run(coro):
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:return runner.run(coro)


@pytest.mark.parametrize('mode,expected',[('normal',True),('detected',False)])
def test_actual_binary_ipc_selector_loop_and_normal_reap(monkeypatch,tmp_path,mode,expected):
    children=controlled(monkeypatch,tmp_path,mode)
    assert run(AmsiScanner().clean(BODY)) is expected
    assert len(children)==1 and children[0].poll()==0
    assert children[0].stdin.closed and children[0].stdout.closed


@pytest.mark.parametrize('mode',['hang_input','hang_output','partial','oversize','nonzero'])
def test_bad_or_timeout_child_killed_reaped_and_latched_no_respawn(monkeypatch,tmp_path,mode):
    children=controlled(monkeypatch,tmp_path,mode)
    scanner=AmsiScanner(timeout=.3)
    async def scenario():
        with pytest.raises(DraftError):await scanner.clean(b'x'*MAX_BYTES if mode=='hang_input' else BODY)
        with pytest.raises(DraftError):await scanner.clean(BODY)
    run(scenario())
    assert len(children)==1 and children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed and scanner._unavailable


def test_busy_and_cancel_during_actual_ipc_do_not_spawn_another_child(monkeypatch,tmp_path):
    children=controlled(monkeypatch,tmp_path,'hang_output');scanner=AmsiScanner()
    async def scenario():
        pending=asyncio.create_task(scanner.clean(BODY))
        await asyncio.sleep(.05)
        assert len(children)==1
        with pytest.raises(DraftError):await scanner.clean(BODY)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):await pending
        with pytest.raises(DraftError):await scanner.clean(BODY)
    run(scenario())
    assert len(children)==1 and children[0].poll() is not None and scanner._unavailable
    assert children[0].stdin.closed and children[0].stdout.closed
