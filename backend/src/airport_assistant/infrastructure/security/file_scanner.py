"""ClamAV INSTREAM over loopback only, bounded concurrency/time/response.

Protocol: https://docs.clamav.net/manual/Usage/ClamdProtocol.html
The daemon must use current signatures, ScanPDF and AlertExceedsMax enabled;
StreamMaxLength/MaxScanSize/MaxFileSize must cover the 5 MiB upload cap.
"""
import asyncio
import struct
from airport_assistant.modules.drafts.models import DraftError


class ClamAVScanner:
    def __init__(self,port:int,*,timeout=20):
        if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid scanner port.')
        self._port,self._timeout,self._slots=port,timeout,asyncio.Semaphore(2)

    async def clean(self,body):
        writer=None
        try:
            async with asyncio.timeout(self._timeout):
                async with self._slots:
                    reader,writer=await asyncio.open_connection('127.0.0.1',self._port,limit=1024)
                    writer.write(b'zINSTREAM\0')
                    for offset in range(0,len(body),65536):
                        chunk=body[offset:offset+65536]
                        writer.write(struct.pack('!I',len(chunk))+chunk)
                        await writer.drain()
                    writer.write(b'\0\0\0\0');await writer.drain()
                    result=await reader.readuntil(b'\0')
                    if result==b'stream: OK\0':return True
                    if result.startswith(b'stream: ') and result.endswith(b' FOUND\0'):return False
                    raise ValueError('Invalid scanner response.')
        except (OSError,TimeoutError,ValueError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            raise DraftError(503,'ATTACHMENT_SCAN_UNAVAILABLE') from None
        finally:
            if writer is not None:writer.close()
