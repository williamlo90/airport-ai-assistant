"""Isolated forward-schema and SQL contracts; no app schema or real provider."""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from airport_assistant.composition import database_loop_factory
from airport_assistant.database_config import MigrationDatabaseSettings, RuntimeDatabaseSettings, load_database_settings
from airport_assistant.infrastructure.database.connections import RuntimeDatabase
from airport_assistant.infrastructure.database.migrations import create_migration_engine, migrate
from airport_assistant.infrastructure.database.provisioning import prepare_roles, prepare_auth_owner, enable_login, runtime_url
from airport_assistant.modules.identity.context import DatabaseContext
from airport_assistant.modules.source.labels import SecurityLabels, merge_labels
from airport_assistant.modules.source.evidence import SourceEvidence, ObservedSource
from airport_assistant.modules.source.models import Booking, Transaction, Policy

pytestmark = pytest.mark.skipif(os.environ.get('RUN_NEON_PRIVATE_SCHEMA_TESTS') != '1', reason='Opt-in isolated private schema SQL.')
HEAD = '0029_private_source_schema'
BOOKING = dict(source='reference', resource_type='booking', reference='B123', required_source_scopes=['booking:read'])
PAYMENT = dict(source='reference', resource_type='transaction', reference='P123', required_source_scopes=['payment_status:read'])
DETAILS = {**PAYMENT, 'required_source_scopes': ['payment_details:read', 'payment_status:read']}


@dataclass
class Target:
    schema: str
    roles: tuple
    engine: object = field(repr=False)
    direct: str = field(repr=False)
    url: str = field(repr=False)
    a: object
    b: object
    principal: object
    baseline: dict = field(repr=False)


def catalog(connection, schema):
    args = dict(schema=schema)
    queries = dict(
        tables="SELECT c.relname,c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relkind='r' ORDER BY c.relname",
        columns="SELECT c.relname,a.attname,a.attacl::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attname",
        policies="SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname=:schema ORDER BY tablename,policyname",
        functions="SELECT p.oid,p.proacl::text,p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:schema ORDER BY p.oid",
        constraints="SELECT c.relname,co.conname,pg_get_constraintdef(co.oid),co.convalidated FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema ORDER BY c.relname,co.conname")
    return {name: [tuple(row) for row in connection.execute(text(query), args).all()] for name, query in queries.items()}


def public_rows(connection, schema):
    result = {}
    for table in ('tenants', 'principals', 'sessions', 'conversations', 'messages', 'conversation_events', 'agent_runs', 'action_drafts', 'ai_generations'):
        result[table] = connection.exec_driver_sql(f'SELECT to_jsonb(t) FROM "{schema}".{table} t ORDER BY id' if table != 'conversation_events'
            else f'SELECT to_jsonb(t) FROM "{schema}".{table} t ORDER BY conversation_id,sequence').scalars().all()
    return result


@pytest.fixture(scope='module')
def target():
    env = Path(os.environ.get('DB_TEST_ENV_FILE', '.env'))
    direct = load_database_settings(MigrationDatabaseSettings, env)
    runtime = load_database_settings(RuntimeDatabaseSettings, env)
    suffix = uuid4().hex
    schema = 'src_sql_test_' + suffix
    base = tuple(prefix + suffix for prefix in ('src_runtime_', 'src_policy_', 'src_auth_'))
    hashed = hashlib.sha256(schema.encode()).hexdigest()[:24]
    derived = tuple(prefix + hashed for prefix in ('job_worker_', 'job_owner_', 'mail_owner_', 'reply_owner_', 'knowledge_lock_'))
    roles = base + derived
    engine = create_migration_engine(direct.url.get_secret_value())
    prepared = False
    try:
        with engine.begin() as c:
            prepare_roles(c, base[0], base[1]); prepare_auth_owner(c, base[2])
        prepared = True
        try:
            revision = migrate(direct.url.get_secret_value(), schema=schema,
                runtime_role=base[0], policy_role=base[1], auth_role=base[2], target_revision='0028_public_answers')
        except Exception as error:
            pytest.fail('Isolated private schema migration failed: ' + type(error).__name__, pytrace=False)
        assert revision == '0028_public_answers'
        password = secrets.token_urlsafe(32)
        with engine.begin() as c:
            enable_login(c, base[0], password)
            a, b = [c.execute(text(f'''INSERT INTO "{schema}".tenants(slug,display_name,timezone,supported_languages)
                VALUES(:slug,:slug,'UTC',ARRAY['en']) RETURNING id'''), dict(slug=slug)).scalar_one() for slug in ('a', 'b')]
            principal = c.execute(text(f'INSERT INTO "{schema}".principals(tenant_id) VALUES(:tenant) RETURNING id'), dict(tenant=a)).scalar_one()
            session, conversation, message, organization, queue = (uuid4() for _ in range(5))
            c.execute(text(f'''INSERT INTO "{schema}".sessions(tenant_id,id,principal_id,session_kind,token_hash,csrf_secret_hash,expires_at)
              VALUES(:tenant,:id,:principal,'customer',:token,:csrf,clock_timestamp()+interval '1 hour')'''),
              dict(tenant=a,id=session,principal=principal,token='a'*64,csrf='b'*64))
            for key,value in dict(tenant_id=a,principal_id=principal,actor_kind='customer',session_id=session).items():
                c.execute(text('SELECT set_config(:key,:value,true)'),dict(key='app.'+key,value=str(value)))
            c.execute(text(f'''INSERT INTO "{schema}".conversations(tenant_id,id,customer_principal_id,language,last_sequence)
              VALUES(:tenant,:id,:principal,'en',1)'''),dict(tenant=a,id=conversation,principal=principal))
            c.execute(text(f'''INSERT INTO "{schema}".messages(tenant_id,id,conversation_id,author_principal_id,author_type,body,language,client_message_id,sequence)
              VALUES(:tenant,:id,:conversation,:principal,'customer','Public row before upgrade','en',:request,1)'''),
              dict(tenant=a,id=message,conversation=conversation,principal=principal,request=uuid4()))
            c.execute(text(f'''INSERT INTO "{schema}".agent_runs(tenant_id,conversation_id,message_id,session_id,principal_id,request_key,request_hash,conversation_version,generation_epoch,language,mode,parameters)
              VALUES(:tenant,:conversation,:message,:session,:principal,:request,:hash,1,1,'en','assist',CAST(:parameters AS jsonb))'''),
              dict(tenant=a,conversation=conversation,message=message,session=session,principal=principal,request=uuid4(),hash='c'*64,
                   parameters=json.dumps(dict(booking_references=[],transaction_references=[],knowledge=None,contact_email=None,action_type=None))))
            c.execute(text(f'''INSERT INTO "{schema}".ai_generations(tenant_id,id,conversation_id,origin_session_id,principal_id,input_version,generation_epoch,language,nonce_hash)
              VALUES(:tenant,:id,:conversation,:session,:principal,1,1,'en',:hash)'''),dict(tenant=a,id=uuid4(),conversation=conversation,session=session,principal=principal,hash='d'*64))
            c.execute(text(f'INSERT INTO "{schema}".organizations(tenant_id,id,name) VALUES(:tenant,:id,\'Test organization\')'),dict(tenant=a,id=organization))
            c.execute(text(f'INSERT INTO "{schema}".service_queues(tenant_id,id,organization_id,name,service_type) VALUES(:tenant,:id,:org,\'Test queue\',\'general\')'),dict(tenant=a,id=queue,org=organization))
            c.execute(text(f'''INSERT INTO "{schema}".action_drafts(tenant_id,id,conversation_id,actor_principal_id,created_session_id,created_at,action_type,destination_queue_id,destination,payload,displayed_summary,language,payload_hash,expires_at)
              VALUES(:tenant,:id,:conversation,:principal,:session,clock_timestamp(),'create_case',:queue,CAST(:destination AS jsonb),CAST(:payload AS jsonb),'Public draft before upgrade','en',:hash,clock_timestamp()+interval '10 minutes')'''),
              dict(tenant=a,id=uuid4(),conversation=conversation,principal=principal,session=session,queue=queue,hash='e'*64,
                destination=json.dumps(dict(queue_id=str(queue),organization_id=str(organization),queue_name='Test queue',organization_name='Test organization',service_type='general')),
                payload=json.dumps(dict(category='information',service_type='general',summary='Public draft',customer_report='Public report',attachment_ids=[]))))
        with engine.connect() as c:
            baseline = catalog(c,schema)
            baseline['rows'] = public_rows(c,schema)
        assert migrate(direct.url.get_secret_value(),schema=schema,runtime_role=base[0],policy_role=base[1],auth_role=base[2],target_revision=HEAD) == HEAD
        yield Target(schema, roles, engine, direct.url.get_secret_value(),
                     runtime_url(runtime.url.get_secret_value(), base[0], password), a, b, principal, baseline)
    finally:
        assert schema == 'src_sql_test_' + suffix and all(role in roles for role in (*base, *derived))
        if prepared:
            with engine.begin() as c:
                c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                for role in roles:
                    assert role.endswith(suffix) or role.endswith(hashed)
                    exists = c.execute(text('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)'), dict(role=role)).scalar_one()
                    if exists:
                        c.exec_driver_sql(f'DROP ROLE "{role}"')
            with engine.connect() as c:
                remaining=c.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)
                    OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY(:roles))'''),dict(schema=schema,roles=list(roles))).scalar_one()
                assert not remaining
            report=Path(__file__).resolve().parents[3]/'local/team/private-schema-neon-cleanups'/f'{schema}.json'
            report.parent.mkdir(parents=True,exist_ok=True)
            report.write_text(json.dumps(dict(schema=schema,schema_removed=True,roles_removed=True,roles=len(roles))),encoding='utf-8')
        engine.dispose()


def scalar(connection, t, sql, **params):
    return connection.execute(text(sql.format(s='"' + t.schema + '"')), params).scalar_one()


def test_repeat_upgrade_keeps_rows_and_reports_uninstalled_private_contracts(target):
    t = target
    assert migrate(t.direct, schema=t.schema, runtime_role=t.roles[0], policy_role=t.roles[1], auth_role=t.roles[2],target_revision=HEAD) == HEAD
    with t.engine.connect() as c:
        assert scalar(c, t, 'SELECT count(*) FROM {s}.tenants') == 2
        status = scalar(c, t, 'SELECT {s}.private_source_schema_status()')
        assert status == dict(schema_version=1, classification_schema=True, graph_contract=False,
                              outbound_contract=False, inbound_contract=False, history_contract=False)


def test_actual_sql_canonical_union_matches_python_and_keeps_independent_scopes(target):
    t = target
    groups = [[PAYMENT, BOOKING], [DETAILS], []]
    expected = merge_labels(*(SecurityLabels.from_records(t.a, records) for records in groups if records)).to_records()
    with t.engine.connect() as c:
        actual = scalar(c, t, 'SELECT {s}.source_labels_union(ARRAY[CAST(:a AS jsonb),CAST(:b AS jsonb),CAST(:c AS jsonb)])',
                        a=json.dumps(groups[0]), b=json.dumps(groups[1]), c='[]')
        assert actual == expected
        assert scalar(c, t, 'SELECT {s}.source_labels_contains(CAST(:whole AS jsonb),CAST(:part AS jsonb))', whole=json.dumps(actual), part=json.dumps([PAYMENT])) is True
        assert scalar(c, t, 'SELECT {s}.source_labels_contains(CAST(:whole AS jsonb),CAST(:part AS jsonb))', whole=json.dumps([PAYMENT]), part=json.dumps([DETAILS])) is False
        assert scalar(c, t, 'SELECT {s}.source_labels_contains(CAST(:whole AS jsonb),CAST(:part AS jsonb))', whole=json.dumps([BOOKING]), part=json.dumps([PAYMENT])) is False


def test_malformed_noncanonical_or_oversized_labels_never_become_public(target):
    invalid = [None, {}, '[]', [None], [BOOKING, BOOKING], [PAYMENT, BOOKING],
        [{**BOOKING, 'resource_type': None}], [{**BOOKING, 'source': 'other'}],
        [{**BOOKING, 'reference': '../private'}], [{**BOOKING, 'required_source_scopes': []}],
        [{**BOOKING, 'required_source_scopes': ['payment_status:read']}],
        [{**PAYMENT, 'required_source_scopes': ['payment_status:read', 'payment_status:read']}],
        [{**PAYMENT, 'required_source_scopes': [1]}], [{**BOOKING, 'extra': 'untrusted'}],
        [{**BOOKING, 'reference': 'B' + str(i)} for i in range(65)]]
    with target.engine.connect() as c:
        values = c.execute(text(f'''SELECT "{target.schema}".source_labels_valid(item)
            FROM jsonb_array_elements(CAST(:cases AS jsonb)) item'''), dict(cases=json.dumps(invalid))).scalars().all()
        assert values == [False] * len(invalid)
        assert scalar(c, target, 'SELECT {s}.source_labels_valid(NULL)') is False
        assert scalar(c, target, "SELECT {s}.source_labels_valid('[]'::jsonb)") is True


def test_typed_source_evidence_and_policy_binding_require_the_whole_union(target):
    stamp='2026-11-06T00:00:00Z'
    observed=dict(source_updated_at=stamp,observed_at=stamp)
    book = Booking.model_validate_json(json.dumps(dict(observed,reference='B123',service_type='parking',status='confirmed',
        policy_reference='POL1',allowed_scopes=['booking:read'],start_at=stamp,end_at='2026-11-07T00:00:00Z',
        service_timezone='UTC',location='Terminal 1')))
    transaction = Transaction.model_validate_json(json.dumps(dict(observed,reference='P123',status='paid',booking_reference='B123',
        allowed_scopes=['payment_status:read','payment_details:read'],currency='IDR',amount_minor=10000,currency_exponent=0,refund_status='none')))
    policy=Policy.model_validate_json(json.dumps(dict(observed,reference='POL1',service_type='parking',version=1,effective_from=stamp,
        instructions=dict(en='Contact support.',id='Hubungi petugas.'))))
    evidence=SourceEvidence.capture(target.a,(ObservedSource(book),ObservedSource(transaction),ObservedSource(policy,book)))
    records=evidence.to_records();expected=evidence.labels.to_records()
    with target.engine.connect() as c:
        assert scalar(c, target, 'SELECT {s}.source_evidence_requirements(CAST(:records AS jsonb))', records=json.dumps(records)) == expected
        assert scalar(c, target, 'SELECT {s}.source_evidence_valid(CAST(:records AS jsonb),CAST(:labels AS jsonb))', records=json.dumps(records), labels=json.dumps(expected)) is True
        assert scalar(c, target, 'SELECT {s}.source_evidence_valid(CAST(:records AS jsonb),CAST(:labels AS jsonb))', records=json.dumps(records), labels=json.dumps([BOOKING])) is False
        policy_record=next(item for item in records if item['kind']=='policy')
        transaction_record=next(item for item in records if item['kind']=='transaction')
        wrong_policy = [{**policy_record, 'data': {**policy_record['data'],'reference':'OTHER'}}]
        assert scalar(c, target, 'SELECT {s}.source_evidence_valid(CAST(:records AS jsonb),CAST(:labels AS jsonb))', records=json.dumps(wrong_policy), labels=json.dumps(expected)) is False
        wrong_payment = [{**transaction_record, 'data': {**transaction_record['data'], 'allowed_scopes': ['payment_status:read']}}]
        assert scalar(c, target, 'SELECT {s}.source_evidence_valid(CAST(:records AS jsonb),CAST(:labels AS jsonb))', records=json.dumps(wrong_payment), labels=json.dumps(expected)) is False


def test_real_row_triggers_reject_declassification_and_snapshot_rewrite(target):
    # A disposable contract probe exercises the SAME trigger functions. This
    # is not represented as an enabled business case/message graph.
    with target.engine.begin() as c:
        c.exec_driver_sql(f'''CREATE TEMP TABLE source_contract_probe(resource_security_labels jsonb NOT NULL,
          source_observations jsonb NOT NULL,
          CHECK("{target.schema}".source_labels_valid(resource_security_labels)),
          CHECK("{target.schema}".source_evidence_valid(source_observations,resource_security_labels))) ON COMMIT DROP''')
        c.exec_driver_sql(f'''CREATE TRIGGER monotone BEFORE UPDATE OF resource_security_labels ON source_contract_probe
          FOR EACH ROW EXECUTE FUNCTION "{target.schema}".guard_source_labels_monotone()''')
        c.exec_driver_sql(f'''CREATE TRIGGER immutable_evidence BEFORE UPDATE OF source_observations ON source_contract_probe
          FOR EACH ROW EXECUTE FUNCTION "{target.schema}".guard_source_evidence_immutable()''')
        c.execute(text("INSERT INTO source_contract_probe VALUES(CAST(:labels AS jsonb),'[]'::jsonb)"), dict(labels=json.dumps([BOOKING, PAYMENT])))
        for query in ("UPDATE source_contract_probe SET resource_security_labels='[]'::jsonb",
                      "UPDATE source_contract_probe SET source_observations='[{}]'::jsonb"):
            with pytest.raises(IntegrityError):
                with c.begin_nested():
                    c.exec_driver_sql(query)
        c.execute(text('UPDATE source_contract_probe SET resource_security_labels=CAST(:labels AS jsonb)'), dict(labels=json.dumps([BOOKING, DETAILS])))
        assert c.exec_driver_sql('SELECT resource_security_labels FROM source_contract_probe').scalar_one() == [BOOKING, DETAILS]


def test_all_real_tables_keep_force_rls_and_closed_private_columns(target):
    with target.engine.connect() as c:
        unsafe = c.execute(text('''SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND c.relkind='r' AND c.relname<>'schema_migrations'
            AND(NOT c.relrowsecurity OR NOT c.relforcerowsecurity)'''), dict(schema=target.schema)).scalar_one()
        assert unsafe == 0
        holds = c.execute(text('''SELECT count(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND co.conname='private_labels_hold' AND co.convalidated'''), dict(schema=target.schema)).scalar_one()
        assert holds == 10
        assert scalar(c, target, "SELECT count(*) FROM pg_constraint co JOIN pg_namespace n ON n.oid=co.connamespace WHERE n.nspname=:schema AND co.conname='private_evidence_hold'", schema=target.schema) == 1
        for role in target.roles[:5]:
            assert c.execute(text('SELECT NOT(rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname=:role'), dict(role=role)).scalar_one()


def test_populated_forward_upgrade_preserves_rows_policies_and_existing_privileges(target):
    with target.engine.connect() as c:
        after = catalog(c,target.schema)
        for key in ('tables','policies'):
            assert after[key] == target.baseline[key]
        for key in ('columns','functions','constraints'):
            assert all(row in after[key] for row in target.baseline[key])
        rows = public_rows(c,target.schema)
        for table,before in target.baseline['rows'].items():
            assert len(rows[table]) == len(before)
            for original,current in zip(before,rows[table]):
                assert all(current[key] == value for key,value in original.items())
                for key in current.keys()-original.keys():
                    assert current[key] in ([],None)


def test_pure_helpers_have_exact_role_access_and_no_public_or_definer_privilege(target):
    names=['source_labels_union','source_labels_valid','source_labels_contains','source_evidence_requirements',
           'source_evidence_valid','guard_source_labels_monotone','guard_source_evidence_immutable','private_source_schema_status']
    with target.engine.connect() as c:
        metadata=c.execute(text('''SELECT p.oid,p.proname,p.prosecdef,p.proconfig,
          EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE')
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=:schema AND p.proname=ANY(:names)'''),dict(schema=target.schema,names=names)).all()
        assert len(metadata)==len(names)
        for oid,name,definer,config,public in metadata:
            assert not definer and not public and config==['search_path=pg_catalog']
            if not name.startswith('guard_'):
                for role in target.roles[:-1]:
                    assert c.execute(text("SELECT has_function_privilege(:role,:oid,'EXECUTE')"),dict(role=role,oid=oid)).scalar_one()


def test_real_restricted_login_keeps_tenant_isolation_and_can_only_probe_contracts(target):
    async def scenario():
        db = RuntimeDatabase(target.url, pool_max=1, acquire_timeout=5)
        try:
            await db.open()
            async with db.transaction() as c:
                assert (await(await c.execute(f'SELECT count(*) FROM "{target.schema}".tenants')).fetchone())[0] == 0
                status = (await(await c.execute(f'SELECT "{target.schema}".private_source_schema_status()')).fetchone())[0]
                assert status['graph_contract'] is False and status['outbound_contract'] is False
            async with db.transaction(DatabaseContext(target.a, target.principal, 'customer')) as c:
                ids = await(await c.execute(f'SELECT id FROM "{target.schema}".tenants')).fetchall()
                assert [item[0] for item in ids] == [target.a]
                assert (await(await c.execute(f'SELECT "{target.schema}".source_labels_valid(%s::jsonb)', (json.dumps([BOOKING]),))).fetchone())[0] is True
        finally:
            await db.close()
    with asyncio.Runner(loop_factory=database_loop_factory) as runner:
        runner.run(scenario())
