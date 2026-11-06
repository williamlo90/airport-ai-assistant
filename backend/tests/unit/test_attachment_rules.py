"""FILE-01 domain and actual loopback clamd protocol, without a real engine."""
import asyncio
import struct
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from test_draft_rules import report,setup
from airport_assistant.modules.attachments.service import validate_file,MAX_BYTES,AttachmentService
from airport_assistant.modules.drafts.models import DraftError,displayed_summary,seal,require_integrity
from airport_assistant.infrastructure.security.file_scanner import ClamAVScanner


@pytest.mark.parametrize('body,mime',[(b'\x89PNG\r\n\x1a\nX','image/png'),(b'\xff\xd8\xffX','image/jpeg'),(b'%PDF-1.7\n%%EOF','application/pdf')])
def test_signatures_allow_only_evidence_types(body,mime):validate_file(body,mime)


@pytest.mark.parametrize('body,mime,status',[(b'', 'image/png',413),(b'x'*(MAX_BYTES+1),'application/pdf',413),(b'PK\x03\x04','application/zip',415),(b'<script/>','image/png',415),(b'%PDF-1.7','image/png',415)])
def test_size_and_spoofed_types_rejected(body,mime,status):
    with pytest.raises(DraftError) as e:validate_file(body,mime)
    assert e.value.status==status


def test_attachment_selection_and_preview_hash():
    actor,conv,draft,*_=setup();ids=[uuid4() for _ in range(3)]
    payload=report(attachment_ids=ids)
    from dataclasses import replace
    updated=seal(replace(draft,payload=payload,displayed_summary=displayed_summary('create_case',payload,draft.destination,'en')))
    assert updated.payload_hash!=draft.payload_hash and all(str(id) in updated.displayed_summary for id in ids)
    require_integrity(updated)
    for values in ([ids[0],ids[0]],[*ids,uuid4()]):
        with pytest.raises(ValidationError):report(attachment_ids=values)


@pytest.mark.parametrize('result,expected',[(b'stream: OK\0',True),(b'stream: Test.Signature FOUND\0',False),(b'stream: size ERROR\0',None),(b'garbage\0',None),(b'x'*1100+b'\0',None),(b'',None)])
def test_clamd_protocol_over_real_loopback(result,expected):
    async def scenario():
        received=[]
        async def serve(reader,writer):
            try:
                assert await reader.readuntil(b'\0')==b'zINSTREAM\0'
                while True:
                    size=struct.unpack('!I',await reader.readexactly(4))[0]
                    if not size:break
                    received.append(await reader.readexactly(size))
                writer.write(result);await writer.drain()
            finally:writer.close()
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        async with server:
            scanner=ClamAVScanner(server.sockets[0].getsockname()[1])
            body=b'%PDF-'+b'x'*150000
            if expected is None:
                with pytest.raises(DraftError) as e:await scanner.clean(body)
                assert e.value.code=='ATTACHMENT_SCAN_UNAVAILABLE'
            else:assert await scanner.clean(body) is expected
            assert b''.join(received)==body
    asyncio.run(scenario())


def test_missing_scanner_never_stores_file():
    from unittest.mock import Mock
    actor,conv,draft,tx,outcomes,drafts=setup()
    tx.attachment=AsyncMock(return_value=None);tx.attachment_count=AsyncMock(return_value=0);tx.insert_attachment=AsyncMock()
    async def scenario():
        service=AttachmentService(drafts._repository)
        with pytest.raises(DraftError) as e:await service.upload(actor,draft.id,1,uuid4(),b'%PDF-1.7','application/pdf')
        assert e.value.code=='ATTACHMENT_SCAN_UNAVAILABLE'
        tx.insert_attachment.assert_not_awaited()
    asyncio.run(scenario())
