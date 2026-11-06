"""Opt-in actual Windows antimalware provider, synthetic buffers only."""
import asyncio
import json
import os
from pathlib import Path
import time

import pytest

from airport_assistant.composition import database_loop_factory
from airport_assistant.infrastructure.security.amsi_scanner import AmsiScanner
from airport_assistant.modules.attachments.service import MAX_BYTES,validate_file

pytestmark=pytest.mark.skipif(os.environ.get('RUN_WINDOWS_AMSI_TESTS')!='1' or os.name!='nt',reason='Opt-in real Windows AMSI scans.')
FIXTURES=Path(__file__).resolve().parents[1]/'fixtures'/'attachments'


def pdf():
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>',b'<< /Length 0 >>\nstream\n\nendstream']
    data=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(data);data+=b'xref\n0 5\n0000000000 65535 f \n'
    for offset in offsets:data+=f'{offset:010d} 00000 n \n'.encode()
    return data+b'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


def standard_av_test():
    # Standard harmless EICAR fixture; in memory, never executed/persisted.
    result=b'X5O!P%@AP[4'+bytes([92])+b'PZX54(P^)7CC)7}'+b'$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
    assert len(result)==68
    return result


def accepted_header_detection_fixture():
    # Official Microsoft benign AMSI buffer-test marker. The file-level
    # standard fixture requires its signature at byte zero, so embedding that
    # different fixture after a PDF header is not an equivalent qualification.
    # Never print/persist this marker or execute its contents.
    marker=b'AMSI Test '+b'Sample: '+b'7e72c3ce-861b-'+b'4339-8740-0ac1484c1386'
    return b'%PDF-1.4\n%'+marker+b'\n%%EOF\n'


@pytest.mark.parametrize('kind',['png','jpeg','pdf','near_cap','standard_detection','accepted_header_detection'])
def test_actual_native_buffer_verdict(kind):
    data,mime={
        'png':lambda:((FIXTURES/'clean.png').read_bytes(),'image/png'),
        'jpeg':lambda:((FIXTURES/'clean.jpg').read_bytes(),'image/jpeg'),
        'pdf':lambda:(pdf(),'application/pdf'),
        'near_cap':lambda:((FIXTURES/'clean.png').read_bytes().ljust(MAX_BYTES,b'\0'),'image/png'),
        'standard_detection':lambda:(standard_av_test(),None),
        'accepted_header_detection':lambda:(accepted_header_detection_fixture(),'application/pdf'),
    }[kind]()
    if mime is not None:validate_file(data,mime)
    observed=[];scanner=AmsiScanner();original=scanner._verdict
    def capture(output,body):
        observed.append(json.loads(output))
        print('Native scan metadata:',json.dumps(observed[-1]))
        return original(output,body)
    scanner._verdict=capture
    started=time.monotonic()
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:clean=runner.run(scanner.clean(data))
    assert clean is (kind not in {'standard_detection','accepted_header_detection'})
    assert len(observed)==1 and not scanner._busy and not scanner._unavailable
    evidence=Path(__file__).resolve().parents[3]/'local/team/amsi-native-results'
    evidence.mkdir(parents=True,exist_ok=True)
    (evidence/(kind+'.json')).write_text(json.dumps(dict(kind=kind,clean=clean,elapsed_ms=round((time.monotonic()-started)*1000),
        metadata=observed[0],selector_loop=True,real_provider=True,payload_files=0)),encoding='utf-8')
