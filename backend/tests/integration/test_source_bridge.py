"""Real Neon customer cookies + independent normal TCP reference service.

Reference runs in its own Python environment. No reference imports in backend.
Email delivery is controlled only; this does not prove live MAIL-05 delivery.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import time
from uuid import UUID,uuid4
import httpx
import pytest
from sqlalchemy import text
from test_staff_auth import target,harness,run,change
from airport_assistant.main import create_app
from airport_assistant.config import Settings
from airport_assistant.auth_config import AuthSettings
from airport_assistant.database_config import RuntimeDatabaseSettings
from airport_assistant.source_config import SourceSettings
from airport_assistant.integrations.source.client import SourceHttpClient
from airport_assistant.infrastructure.database.source_subjects import PostgresSourceSubjects
from airport_assistant.modules.source.service import SourceReadService
from airport_assistant.modules.source.grants import CurrentSourceGrants
from airport_assistant.modules.source.labels import PrivateSourceAccess
from airport_assistant.api.auth import SESSION_COOKIE

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1' or os.environ.get('RUN_SOURCE_NETWORK_TESTS')!='1',reason='Set RUN_NEON_AUTH_TESTS=1 and RUN_SOURCE_NETWORK_TESTS=1 for real source bridge.')
A=UUID('11111111-1111-4111-8111-111111111111');B=UUID('22222222-2222-4222-8222-222222222222')


@pytest.fixture(scope='module')
def reference(tmp_path_factory):
    python=Path(os.environ.get('REFERENCE_TEST_PYTHON','reference_service/.venv/Scripts/python.exe')).resolve()
    if not python.is_file():pytest.fail('Install the independent reference_service environment before opted-in tests.')
    directory=tmp_path_factory.mktemp('source-bridge');db=directory/'reference.sqlite3'
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    token=secrets.token_urlsafe(48);key=secrets.token_urlsafe(48)
    envfile=directory/'.env'
    envfile.write_text(f"REF_ENV=test\nREF_PORT={port}\nREF_DATABASE_PATH='{db.as_posix()}'\nREF_SERVICE_TOKEN={token}\nREF_CONTEXT_SECRET={key}\n",encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith('REF_') and k not in {'PYTHONPATH','VIRTUAL_ENV'}}
    subprocess.run([str(python),'-m','airport_reference.setup','--env-file',str(envfile)],env=env,capture_output=True,text=True,check=True,timeout=15)
    log=(directory/'source.log').open('w+',encoding='utf-8')
    server=subprocess.Popen([str(python),'-m','airport_reference.main','--env-file',str(envfile)],env=env,stdout=log,stderr=log)
    try:
        with httpx.Client(trust_env=False,timeout=1) as c:
            for _ in range(100):
                assert server.poll() is None
                try:
                    if c.get(f'http://127.0.0.1:{port}/health/live').status_code==200:break
                except httpx.TransportError:time.sleep(.05)
            else:raise AssertionError('Source startup deadline exceeded.')
        yield dict(base=f'http://127.0.0.1:{port}',token=token,key=key,db=db)
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
        log.seek(0);output=log.read();log.close()
        assert token not in output and key not in output


@pytest.fixture(autouse=True)
def clear_mappings(target,reference):
    change(target,'UPDATE {s}.principals SET source_subject_ref=NULL')
    with sqlite3.connect(reference['db']) as connection:
        relations=connection.execute('SELECT * FROM relations').fetchall()
    try:
        yield
    finally:
        change(target,'UPDATE {s}.principals SET source_subject_ref=NULL')
        # Revocation tests mutate only this temporary source fixture. Restore
        # its relations so subsequent scope matrices cannot inherit revocation.
        with sqlite3.connect(reference['db']) as connection:
            connection.execute('DELETE FROM relations')
            if relations:
                connection.executemany('INSERT INTO relations VALUES ('+','.join('?' for _ in relations[0])+')',relations)


class Delivery:
    message=None
    async def send(self,message):self.message=message


@asynccontextmanager
async def bridge(target,reference,*,base=None):
    sender=Delivery()
    async with harness(target,sender=sender) as h:
        config=SourceSettings(SOURCE_API_BASE_URL=base or reference['base'],SOURCE_API_TOKEN=reference['token'],context_secret=reference['key'],
                              tenant_map={target['a']:A,target['b']:B},allow_loopback_http=True)
        client=SourceHttpClient(config.base_url,config.token.get_secret_value(),config.context_secret.get_secret_value())
        service=SourceReadService(client,PostgresSourceSubjects(h.db,target['schema']),config.tenant_map)
        grants=CurrentSourceGrants(service,client)
        h.app=create_app(Settings(env='test',database_enabled=True,auth_enabled=True,source_enabled=True),
                         database_settings=RuntimeDatabaseSettings(DATABASE_URL=target['url']),auth_settings=AuthSettings(SESSION_SECRET=target['key']),
                         source_settings=config,services=replace(h.app.state.services,source=service,
                             source_grants=grants,private_source_access=PrivateSourceAccess(grants)))
        try:yield h,sender
        finally:
            await client.aclose()
            await h.app.state.resources.source.aclose()


async def verify(h,sender,client,subject=None):
    await h.boot(client)
    accepted=await client.post('/api/v1/auth/challenges',headers=h.headers(client),json=dict(contact='bridge'+uuid4().hex+'@example.com',purpose='customer_login'))
    assert accepted.status_code==202
    result=await client.post('/api/v1/auth/challenges/'+accepted.json()['challenge_id']+'/verify',headers=h.headers(client),json=dict(code=sender.message.code))
    assert result.status_code==200
    principal=UUID(result.json()['principal_id'])
    if subject is not None:change(h.target,'UPDATE {s}.principals SET source_subject_ref=:subject WHERE id=:id',subject=subject,id=principal)
    return principal


@pytest.mark.parametrize('subject,booking_status,payment_status',[('owner',200,200),('representative',200,404),('beneficiary',200,200),('payer',404,200),('outsider',404,404)])
def test_verified_customer_read_pipeline_preserves_source_scopes(target,reference,subject,booking_status,payment_status):
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as c:
                await verify(h,sender,c,subject)
                booking=await c.get('/api/v1/bookings/PK-A-001')
                payment=await c.get('/api/v1/transactions/TX-A-001')
                assert booking.status_code==booking_status and payment.status_code==payment_status
                if payment.status_code==200:
                    value=payment.json()
                    if subject=='beneficiary':assert not set(value)&{'amount_minor','currency','currency_exponent','refund_status'}
                    if subject=='payer':assert 'booking_reference' not in value
                assert reference['token'] not in booking.text+payment.text and reference['key'] not in booking.text+payment.text
                assert booking.headers['cache-control']=='no-store'
    run(scenario())


def test_distinct_application_and_source_tenants_and_reference_collisions(target,reference):
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as ca,h.client('b.example.test') as cb:
                await verify(h,sender,ca,'owner');await verify(h,sender,cb,'owner')
                a=await ca.get('/api/v1/bookings/PK-A-001');b=await cb.get('/api/v1/bookings/PK-A-001')
                assert a.status_code==b.status_code==200
                assert a.json()['location']=='Terminal 2 Parking' and b.json()['location']=='Airport B Private Location'
                assert target['a']!=A and target['b']!=B
                token=ca.cookies.get(SESSION_COOKIE)
                assert (await cb.get('/api/v1/bookings/PK-A-001',headers={'Cookie':SESSION_COOKIE+'='+token})).status_code==401
    run(scenario())


def test_missing_mapping_logout_and_public_identity_claims_are_fail_closed(target,reference):
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as c:
                assert (await c.get('/api/v1/bookings/PK-A-001')).status_code==401
                await h.boot(c);assert (await c.get('/api/v1/bookings/PK-A-001')).status_code==401
                principal=await verify(h,sender,c)
                assert (await c.get('/api/v1/bookings/PK-A-001')).status_code==404
                change(target,'UPDATE {s}.principals SET source_subject_ref=:subject WHERE id=:id',subject='owner',id=principal)
                assert (await c.get('/api/v1/bookings/PK-A-001',params={'tenant_id':str(B)})).status_code==400
                assert (await c.get('/api/v1/bookings/PK-A-001',headers={'X-Source-Context':'claim'})).status_code==400
                assert (await c.get('/api/v1/bookings/PK-A-001')).status_code==200
                assert (await c.post('/api/v1/auth/logout',headers=h.headers(c))).status_code==204
                assert (await c.get('/api/v1/bookings/PK-A-001')).status_code==401
    run(scenario())


def test_policy_relations_pagination_and_no_raw_public_endpoints(target,reference):
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as c:
                await verify(h,sender,c,'owner')
                actor=await h.app.state.services.access.resolve(target['a'],c.cookies.get(SESSION_COOKIE))
                assert (await h.app.state.services.source.policy(actor,'parking-v2')).version==2
                relations=await h.app.state.services.source.relations(actor,limit=1)
                assert len(relations.items)==1 and relations.next_cursor
                page=await c.get('/api/v1/bookings/PK-A-001/transactions',params={'limit':1})
                assert page.status_code==200 and page.json()['next_cursor']
                second=await c.get('/api/v1/bookings/PK-A-001/transactions',params={'limit':1,'cursor':page.json()['next_cursor']})
                assert second.status_code==200 and second.json()['items'][0]['status']=='unknown'
                assert (await c.get('/api/v1/policies/parking-v2')).status_code==404
                assert (await c.get('/api/v1/subjects/me/relations')).status_code==404
    run(scenario())


def test_source_unavailable_is_503_not_a_payment_result(target,reference):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    async def scenario():
        async with bridge(target,reference,base=f'http://127.0.0.1:{port}') as (h,sender):
            async with h.client() as c:
                await verify(h,sender,c,'owner')
                r=await c.get('/api/v1/transactions/TX-A-001')
                assert r.status_code==503 and r.json()['error']['code']=='SOURCE_UNAVAILABLE'
                assert 'status' not in r.json() and 'failed' not in r.text
    run(scenario())


def test_source_grant_revocation_is_not_cached(target,reference):
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as c:
                await verify(h,sender,c,'beneficiary')
                assert (await c.get('/api/v1/transactions/TX-A-001')).status_code==200
                connection=sqlite3.connect(reference['db'])
                try:
                    with connection:connection.execute('DELETE FROM relations WHERE tenant_id=? AND subject_reference=? AND resource_reference=?',(str(A),'beneficiary','TX-A-001'))
                    assert (await c.get('/api/v1/transactions/TX-A-001')).status_code==404
                finally:connection.close()
    run(scenario())


@pytest.mark.parametrize('subject,booking_allowed,status_allowed,details_allowed',[
    ('owner',True,True,True),('representative',True,False,False),
    ('beneficiary',True,True,False),('payer',False,True,True),('outsider',False,False,False),
])
def test_current_grant_provider_independent_resource_scopes(target,reference,subject,booking_allowed,status_allowed,details_allowed):
    from airport_assistant.modules.source.grants import CurrentSourceGrants
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as c:
                principal=await verify(h,sender,c,subject)
                grants=h.app.state.services.source_grants
                for kind,ref,scopes,expected in [
                    ('booking','PK-A-001',{'booking:read'},booking_allowed),
                    ('transaction','TX-A-001',{'payment_status:read'},status_allowed),
                    ('transaction','TX-A-001',{'payment_details:read'},details_allowed),
                ]:
                    assert await grants.has_grants(tenant_id=target['a'],principal_id=principal,
                        resource_type=kind,resource_reference=ref,required_scopes=frozenset(scopes)) is expected
                # Identical source references in another app tenant do not authorize this principal.
                assert not await grants.has_grants(tenant_id=target['b'],principal_id=principal,
                    resource_type='transaction',resource_reference='TX-A-001',required_scopes=frozenset({'payment_status:read'}))
    run(scenario())


def test_private_labels_survive_repository_round_trip_and_revoke_after_snapshot(target,reference):
    from airport_assistant.modules.source.grants import CurrentSourceGrants
    from airport_assistant.modules.source.labels import SecurityLabels,PrivateSourceAccess,labels_for_observation,merge_labels
    async def scenario():
        async with bridge(target,reference) as (h,sender):
            async with h.client() as owner,h.client() as beneficiary:
                principal=await verify(h,sender,owner,'owner')
                other=await verify(h,sender,beneficiary,'beneficiary')
                actor=await h.app.state.services.access.resolve(target['a'],owner.cookies.get(SESSION_COOKIE))
                source=h.app.state.services.source
                snapshot=merge_labels(labels_for_observation(target['a'],await source.booking(actor,'PK-A-001')),
                                     labels_for_observation(target['a'],await source.transaction(actor,'TX-A-001')))
                # Repository fixture stores canonical JSON; no case table exists at SRC-03A.
                stored=json.dumps(snapshot.to_records())
                restored=SecurityLabels.from_records(target['a'],json.loads(stored))
                access=h.app.state.services.private_source_access
                assert await access.allows(tenant_id=target['a'],principal_id=principal,labels=restored)
                assert not await access.allows(tenant_id=target['a'],principal_id=other,labels=restored)
                with sqlite3.connect(reference['db']) as connection:
                    connection.execute('DELETE FROM relations WHERE tenant_id=? AND subject_reference=? AND resource_reference=?',(str(A),'owner','TX-A-001'))
                assert not await access.allows(tenant_id=target['a'],principal_id=principal,labels=restored)
    run(scenario())


def test_private_grant_gate_propagates_real_network_unavailability(target,reference):
    from airport_assistant.modules.source.grants import CurrentSourceGrants
    from airport_assistant.modules.source.labels import PrivateSourceAccess,SecurityLabels
    from airport_assistant.modules.source.models import SourceFailure
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    async def scenario():
        async with bridge(target,reference,base=f'http://127.0.0.1:{port}') as (h,sender):
            async with h.client() as c:
                principal=await verify(h,sender,c,'owner')
                labels=SecurityLabels.from_records(target['a'],[dict(source='reference',resource_type='transaction',reference='TX-A-001',required_source_scopes=['payment_status:read'])])
                with pytest.raises(SourceFailure) as exc:
                    await h.app.state.services.private_source_access.allows(
                        tenant_id=target['a'],principal_id=principal,labels=labels)
                assert exc.value.status==503
    run(scenario())
