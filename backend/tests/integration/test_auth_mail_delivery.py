"""Real Neon login/routing proof with controlled HTTP mail; not live provider evidence."""
import os,re
from pathlib import Path
from uuid import uuid4,UUID
import pytest
from test_email_replies import target as reply_target,inbox_target,delivery_target,mail_target,worker_target,publication_target,handoff_target,events_target,files_target,lifecycle_target,commit_target,adoption_target,draft_target,conversation_target,auth_target,inbox_app
from test_staff_auth import run,row,change
from airport_assistant.database_config import load_database_settings,MigrationDatabaseSettings
from airport_assistant.infrastructure.database.migrations import migrate
from airport_assistant.infrastructure.database.verification_mail import PostgresVerificationRouting
from airport_assistant.modules.mail.verification import VerificationMail
from airport_assistant.modules.mail.service import SendResult
from airport_assistant.mail_config import AuthMailRoute

pytestmark=pytest.mark.skipif(os.environ.get('RUN_NEON_AUTH_TESTS')!='1',reason='Opt-in MAIL-05 controlled transport/real Neon.')


@pytest.fixture(scope='module')
def target(reply_target):
    t=reply_target;direct=load_database_settings(MigrationDatabaseSettings,Path(os.environ.get('DB_TEST_ENV_FILE','.env')))
    for _ in range(2):assert migrate(direct.url.get_secret_value(),schema=t['schema'],runtime_role=t['role'],policy_role=t['policy'],auth_role=t['owner'],target_revision='0019_auth_mail')=='0019_auth_mail'
    return t


@pytest.mark.parametrize('outcome',['accepted','delivery_unknown','failed'])
def test_auth_mail_uses_current_account_and_original_browser_proof(target,outcome):
    async def scenario():
        async with inbox_app(target) as (h,_):
            route=AuthMailRoute(account_id=target['mail_account'],account_ref='test-account',sender='support@airport.example',reply_to='reply@airport.example')
            routing=PostgresVerificationRouting(h.db,target['schema']);sent=[]
            class Transport:
                async def send(self,envelope):sent.append(envelope);return SendResult(outcome,'controlled-receipt' if outcome=='accepted' else None)
            h.auth._sender=VerificationMail({target['a']:route},Transport(),routing)
            assert await routing.allowed(target['a'],route)
            assert not await routing.allowed(target['b'],route)
            async with h.client() as client,h.client() as foreign:
                await h.boot(client);await h.boot(foreign)
                response=await client.post('/api/v1/auth/challenges',headers=h.headers(client),json=dict(contact='authmail'+uuid4().hex+'@example.com',purpose='customer_login'))
                assert response.status_code==(503 if outcome=='failed' else 202) and len(sent)==1
                envelope=sent[0];id=str(envelope.id);code=re.search(r'\b[0-9]{8}\b',envelope.text)[0]
                assert code not in response.text
                assert (await foreign.post('/api/v1/auth/challenges/'+id+'/verify',headers=h.headers(foreign),json=dict(code=code))).status_code in (401,422)
                result=await client.post('/api/v1/auth/challenges/'+id+'/verify',headers=h.headers(client),json=dict(code=code))
                assert result.status_code==(422 if outcome=='failed' else 200)
                assert len(sent)==1
            change(target,'UPDATE {s}.email_accounts SET active=false WHERE id=:id',id=route.account_id)
            try:assert not await routing.allowed(target['a'],route)
            finally:change(target,'UPDATE {s}.email_accounts SET active=true WHERE id=:id',id=route.account_id)
    run(scenario())
