"""Seed validation, atomic publication, readonly runtime and safe local setup."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import pytest
from airport_reference.config import ConfigurationError,load_settings
from airport_reference.seed import fixture_path,seed_database
from airport_reference.store import Store


@pytest.mark.parametrize('fault',['amount','currency','time','timezone','foreign_booking','cross_tenant_grant','duplicate_reference','scope','duplicate_scope','extra'])
def test_invalid_seed_never_replaces_valid_database(source,tmp_path,fault):
    data=json.loads(fixture_path().read_bytes());before=source[1].read_bytes()
    if fault=='amount':data['transactions'][0]['amount_minor']=1.5
    elif fault=='currency':data['transactions'][0]['currency_exponent']=2
    elif fault=='time':data['bookings'][0]['end_at']='2020-01-01T00:00:00Z'
    elif fault=='timezone':data['bookings'][0]['service_timezone']='Invalid/Zone'
    elif fault=='foreign_booking':data['transactions'][0]['booking_reference']='MISSING'
    elif fault=='cross_tenant_grant':data['relations'][0]['tenant_id']='33333333-3333-4333-8333-333333333333'
    elif fault=='duplicate_reference':data['bookings'].append(data['bookings'][0])
    elif fault=='scope':data['relations'][0]['scopes']=['payment_details:read']
    elif fault=='duplicate_scope':data['relations'][0]['scopes']=['booking:read','booking:read']
    else:data['bookings'][0]['secret']='must-not-be-accepted'
    bad=tmp_path/'bad.json';bad.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(Exception):seed_database(source[1],replace=True,fixture=bad)
    assert source[1].read_bytes()==before and not list(tmp_path.glob('reference-seed-*'))


def test_seed_refuses_implicit_reset_or_unrelated_database(source,tmp_path):
    with pytest.raises(ValueError):seed_database(source[1])
    other=tmp_path/'other.sqlite3'
    with closing(sqlite3.connect(other)) as c:c.execute('CREATE TABLE unrelated(id INTEGER)')
    before=other.read_bytes()
    with pytest.raises(ValueError):seed_database(other,replace=True)
    assert before==other.read_bytes()


def test_runtime_readonly_foreign_keys_and_separate_dataset(source):
    with Store(source[1]).read() as c:
        assert c.execute('PRAGMA query_only').fetchone()[0]==1
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT count(*) FROM bookings').fetchone()[0]==4
        assert c.execute('SELECT count(*) FROM transactions').fetchone()[0]==6
        with pytest.raises(sqlite3.OperationalError):c.execute('DELETE FROM bookings')


def test_new_file_is_not_published_after_failed_insert(tmp_path):
    data=json.loads(fixture_path().read_bytes());data['subjects'][0]['tenant_id']='33333333-3333-4333-8333-333333333333'
    fixture=tmp_path/'bad.json';fixture.write_text(json.dumps(data))
    path=tmp_path/'new.sqlite3'
    with pytest.raises(sqlite3.IntegrityError):seed_database(path,fixture=fixture)
    assert not path.exists() and not list(tmp_path.glob('reference-seed-*'))


def test_local_setup_generates_secrets_internally_and_is_idempotent(tmp_path,monkeypatch,capsys):
    from airport_reference import setup
    monkeypatch.chdir(tmp_path)
    env=tmp_path/'.env'
    monkeypatch.setattr(sys,'argv',['setup','--env-file',str(env)])
    setup.main();first=load_settings(env)
    output=capsys.readouterr().out
    assert first.service_token.get_secret_value() not in output and first.context_secret.get_secret_value() not in output
    before=first.database_path.read_bytes()
    setup.main();second=load_settings(env)
    assert first.service_token==second.service_token and first.context_secret==second.context_secret and before==second.database_path.read_bytes()


def test_invalid_config_does_not_echo_secret(tmp_path):
    env=tmp_path/'.env';env.write_text('REF_SERVICE_TOKEN=secret-sentinel\nREF_CONTEXT_SECRET=other\n')
    with pytest.raises(ConfigurationError) as error:load_settings(env)
    assert 'secret-sentinel' not in str(error.value)


def test_reference_has_no_backend_package_imports():
    import ast
    root=Path(__file__).resolve().parents[1]/'src'
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node,ast.Import):assert not any(a.name.startswith('airport_assistant') for a in node.names)
            if isinstance(node,ast.ImportFrom):assert not (node.module or '').startswith('airport_assistant')



def test_new_seed_does_not_clobber_target_created_after_precheck(tmp_path,monkeypatch):
    from airport_reference import seed
    path=tmp_path/'raced.sqlite3';original=seed.publish_new
    def race(temporary,target):
        target.write_bytes(b'unrelated-late-file')
        original(temporary,target)
    monkeypatch.setattr(seed,'publish_new',race)
    with pytest.raises(OSError):seed.seed_database(path)
    assert path.read_bytes()==b'unrelated-late-file'
    assert not list(tmp_path.glob('reference-seed-*')) and not list(tmp_path.glob('*.seed.lock'))


def test_cooperating_seeder_cannot_publish_during_existing_seed_lock(tmp_path):
    from airport_reference.seed import publication_lock
    path=tmp_path/'reference.sqlite3'
    with publication_lock(path):
        with pytest.raises(FileExistsError):seed_database(path)
    assert not path.exists() and not list(tmp_path.glob('*.seed.lock'))



def test_posix_publication_link_is_no_clobber(tmp_path,monkeypatch):
    import os
    from types import SimpleNamespace
    from airport_reference import seed
    original=tmp_path/'temp.sqlite3';target=tmp_path/'target.sqlite3'
    original.write_bytes(b'first')
    monkeypatch.setattr(seed,'os',SimpleNamespace(name='posix',link=os.link))
    seed.publish_new(original,target)
    assert target.read_bytes()==b'first'
    with pytest.raises(FileExistsError):seed.publish_new(original,target)
    assert target.read_bytes()==b'first'
