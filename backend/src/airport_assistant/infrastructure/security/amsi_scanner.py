"""Local Windows AMSI via one owned, bounded helper; never a production default."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from airport_assistant.modules.attachments.service import MAX_BYTES
from airport_assistant.modules.drafts.models import DraftError

MAX_OUTPUT=256
WINDOWS_SUPPORTED=os.name=='nt'


class AmsiScanner:
    def __init__(self,*,timeout=20):
        if not 0<timeout<=20:raise ValueError('Bounded scan deadline required.')
        self._timeout=timeout;self._busy=False;self._unavailable=False

    @staticmethod
    def _environment():
        # Registered Windows antimalware providers need these runtime paths and
        # architecture values. Business/provider credentials are never inherited.
        allowed={'SYSTEMROOT','WINDIR','SYSTEMDRIVE','ALLUSERSPROFILE','APPDATA','PROGRAMDATA','PROGRAMFILES',
            'PROGRAMFILES(X86)','PROGRAMW6432','COMMONPROGRAMFILES','COMMONPROGRAMFILES(X86)','COMMONPROGRAMW6432',
            'PROCESSOR_ARCHITECTURE','PROCESSOR_ARCHITEW6432','PATH','COMSPEC','LOCALAPPDATA','USERPROFILE','TEMP','TMP'}
        return {k:v for k,v in os.environ.items() if k.upper() in allowed}

    @staticmethod
    def _exchange(process,body,deadline):
        # Only IPC runs in this single worker. The native scanner is isolated
        # in a killable child, compatible with Windows' psycopg Selector loop.
        try:
            offset=0
            while offset<len(body):
                if time.monotonic()>=deadline:raise TimeoutError()
                size=process.stdin.write(memoryview(body)[offset:offset+65536])
                if not size:raise OSError('Scanner input unavailable.')
                offset+=size
            process.stdin.close()
            output=process.stdout.read(MAX_OUTPUT+1)
            if len(output)>MAX_OUTPUT:raise ValueError('Invalid scanner output.')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            if process.wait(timeout=remaining)!=0:raise ValueError('Scanner did not complete.')
            return output
        finally:
            try:
                if process.poll() is None:process.kill()
                process.wait(timeout=5)
            finally:
                for pipe in (process.stdin,process.stdout):
                    with suppress(OSError,ValueError):pipe.close()

    @staticmethod
    def _verdict(output,body):
        try:
            def unique(pairs):
                value={}
                for key,item in pairs:
                    if key in value:raise ValueError()
                    value[key]=item
                return value
            value=json.loads(output,object_pairs_hook=unique)
            if type(value) is not dict or set(value)!={'hresult','result','byte_size','sha256'}:raise ValueError()
            if type(value['hresult']) is not int or value['hresult']!=0:raise ValueError()
            if type(value['byte_size']) is not int or value['byte_size']!=len(body):raise ValueError()
            if value['sha256']!=hashlib.sha256(body).hexdigest():raise ValueError()
            result=value['result']
            if type(result) is not int or not 0<=result<=0xffffffff:raise ValueError()
            if result in (0,1):return True
            if 16384<=result<=20479 or result>=32768:return False
            raise ValueError()
        except (ValueError,TypeError,UnicodeError):
            raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE') from None

    async def clean(self,body):
        if type(body) is not bytes or not 0<len(body)<=MAX_BYTES:raise DraftError(413,'PAYLOAD_TOO_LARGE')
        if not WINDOWS_SUPPORTED or self._busy or self._unavailable:raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE')
        # No queue: busy callers fail closed instead of accumulating payloads.
        # This singleton limit is per application process, not machine-wide.
        self._busy=True;process=None;work=None;pool=None
        deadline=time.monotonic()+self._timeout
        try:
            # Popen returns the owned handle before the first await, so a
            # cancelled request cannot leave a shielded spawn without an owner.
            process=subprocess.Popen([str(Path(sys.executable).resolve()),'-I',
                str(Path(__file__).with_name('amsi_helper.py').resolve())],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=False,close_fds=True,
                env=self._environment(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000))
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='attachment-scan-ipc')
            work=asyncio.get_running_loop().run_in_executor(pool,self._exchange,process,body,deadline)
            async with asyncio.timeout(remaining):
                output=await asyncio.shield(work)
            return self._verdict(output,body)
        except asyncio.CancelledError:
            self._unavailable=True
            raise
        except DraftError:
            self._unavailable=True
            raise
        except (OSError,TimeoutError,ValueError,subprocess.SubprocessError):
            self._unavailable=True
            raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE') from None
        finally:
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except OSError:self._unavailable=True
            if work is not None:
                try:
                    async with asyncio.timeout(5):await asyncio.shield(work)
                except (OSError,TimeoutError,ValueError,subprocess.SubprocessError,asyncio.CancelledError):self._unavailable=True
            elif process is not None:
                # Only possible if the spawn itself exhausted the deadline.
                # Wait off-loop using a single bounded IPC worker.
                pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='attachment-scan-cleanup')
                cleanup=asyncio.get_running_loop().run_in_executor(pool,process.wait,5)
                try:await asyncio.shield(cleanup)
                except (OSError,subprocess.SubprocessError,asyncio.CancelledError):self._unavailable=True
                finally:
                    for pipe in (process.stdin,process.stdout):
                        with suppress(OSError,ValueError):pipe.close()
            if pool is not None:pool.shutdown(wait=work is not None and work.done(),cancel_futures=False)
            # Keep the instance closed if an owned child/IPC worker is uncertain.
            self._busy=(process is not None and process.poll() is None) or (work is not None and not work.done())
            if self._busy:self._unavailable=True
