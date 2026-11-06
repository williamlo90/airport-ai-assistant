"""Source ACL, projections and availability without application DB/providers."""

from datetime import datetime
import json
import sqlite3
from uuid import uuid4
import pytest
from conftest import request,A,B,TOKEN,SECRET
from airport_reference.security import signed_headers,encode,signature,cursor_decode

BOOK='/source/v1/bookings/PK-A-001'
TX='/source/v1/transactions/TX-A-001'
REL='/source/v1/subjects/me/relations'


def update(source,sql,args=()):
    with sqlite3.connect(source[1]) as c:c.execute(sql,args)


def public_error(r):return {k:v for k,v in r.json()['error'].items() if k!='request_id'}


def test_booking_projection_does_not_inherit_payment_grants(source):
    r=request(source,BOOK)
    assert r.status_code==200
    data=r.json()
    assert data['allowed_scopes']==['booking:read'] and data['service_type']=='parking'
    assert data['policy_reference']=='parking-v2' and data['service_timezone']=='Asia/Jakarta'
    assert set(data)=={'reference','service_type','status','start_at','end_at','service_timezone','location','policy_reference','source_updated_at','observed_at','allowed_scopes'}
    assert data['source_updated_at']!=data['observed_at']
    lounge=request(source,'/source/v1/bookings/LG-A-001')
    assert lounge.status_code==200 and lounge.json()['service_type']=='lounge'


@pytest.mark.parametrize('subject',['owner','representative','beneficiary'])
def test_explicit_booking_relationships(source,subject):
    assert request(source,BOOK,subject=subject).status_code==200


def test_recorded_representative_cannot_read_payment_by_booking_relation(source):
    assert request(source,TX,subject='representative').status_code==404
    assert request(source,BOOK+'/transactions',subject='representative').status_code==404


def test_beneficiary_status_only_never_receives_money_or_refund_details(source):
    for path in [TX,BOOK+'/transactions']:
        result=request(source,path,subject='beneficiary')
        assert result.status_code==200
        items=result.json()['items'] if 'items' in result.json() else [result.json()]
        for item in items:
            assert item['allowed_scopes']==['payment_status:read']
            assert not set(item)&{'currency','amount_minor','currency_exponent','refund_status'}
    assert request(source,TX,subject='owner').json()['amount_minor']==150000


def test_payer_direct_access_does_not_disclose_booking_or_policies(source):
    result=request(source,TX,subject='payer')
    assert result.status_code==200 and result.json()['currency']=='IDR'
    assert 'booking_reference' not in result.json()
    assert request(source,BOOK,subject='payer').status_code==404
    assert request(source,BOOK+'/transactions',subject='payer').status_code==404
    assert request(source,'/source/v1/policies/parking-v2',subject='payer').status_code==404
    relations=request(source,REL,subject='payer').json()['items']
    assert all(x['resource_type']=='transaction' for x in relations)
    assert 'PK-A-001' not in json.dumps(relations)


def test_tenant_reference_collision_returns_only_signed_tenant_data(source):
    a=request(source,BOOK).json();b=request(source,BOOK,tenant=B).json()
    assert a['location']=='Terminal 2 Parking' and b['location']=='Airport B Private Location'
    assert request(source,TX,tenant=B).json()['currency']=='SGD'
    update(source,'DELETE FROM relations WHERE tenant_id=? AND subject_reference=?',(str(B),'owner'))
    r=request(source,BOOK,tenant=B)
    assert r.status_code==404 and 'Terminal 2 Parking' not in r.text


@pytest.mark.parametrize('path',[BOOK,TX,BOOK+'/transactions','/source/v1/policies/parking-v2'])
def test_hidden_and_nonexistent_are_identical_without_reference_metadata(source,path):
    hidden=request(source,path,subject='outsider')
    missing=request(source,path.replace('001','999').replace('v2','v99'),subject='outsider')
    assert hidden.status_code==missing.status_code==404
    assert public_error(hidden)==public_error(missing)
    assert TOKEN not in hidden.text and SECRET not in hidden.text
    assert hidden.json()['error']['request_id']==hidden.headers['x-request-id']
    assert hidden.headers['cache-control']=='no-store'


def test_policy_version_does_not_fall_forward_to_newest(source):
    policy=request(source,'/source/v1/policies/parking-v2')
    assert policy.status_code==200 and policy.json()['version']==2
    assert set(policy.json()['instructions'])=={'id','en'}
    assert request(source,'/source/v1/policies/parking-v3').status_code==404


def test_unknown_payment_status_remains_unknown(source):
    value=request(source,'/source/v1/transactions/TX-A-002').json()
    assert value['status']=='unknown' and value['source_status_label']=='provider_review'


def test_relations_are_for_signed_actor_only(source):
    result=request(source,REL,subject='representative')
    assert result.status_code==200
    assert all(x['relation']=='recorded_representative' for x in result.json()['items'])
    assert request(source,REL,subject='outsider').json()=={'items':[],'next_cursor':None}


@pytest.mark.parametrize('table',['subjects','tenants'])
def test_inactive_tenant_or_subject_is_hidden(source,table):
    update(source,'UPDATE subjects SET active=0 WHERE tenant_id=? AND reference=?',(str(A),'owner')) if table=='subjects' else update(source,'UPDATE tenants SET active=0 WHERE id=?',(str(A),))
    assert request(source,BOOK).status_code==404
    assert request(source,REL).status_code==404


def test_grant_revocation_is_effective_next_request(source):
    assert request(source,TX).status_code==200
    update(source,'DELETE FROM relations WHERE tenant_id=? AND subject_reference=? AND resource_reference=?',(str(A),'owner','TX-A-001'))
    assert request(source,TX).status_code==404
    assert all(x['reference']!='TX-A-001' for x in request(source,BOOK+'/transactions').json()['items'])


def test_authorized_filter_precedes_pagination_and_cursor_is_not_permission(source):
    first=request(source,BOOK+'/transactions',params={'limit':1}).json()
    assert len(first['items'])==1 and first['next_cursor']
    second=request(source,BOOK+'/transactions',params={'limit':1,'cursor':first['next_cursor']})
    assert second.status_code==200 and second.json()['items'][0]['reference']=='TX-A-002'
    update(source,'DELETE FROM relations WHERE tenant_id=? AND subject_reference=? AND resource_reference=?',(str(A),'owner','TX-A-002'))
    again=request(source,BOOK+'/transactions',params={'limit':1,'cursor':first['next_cursor']})
    assert again.status_code==200 and again.json()['items']==[] and again.json()['next_cursor'] is None


@pytest.mark.parametrize('change',['subject','tenant','endpoint','limit','signature'])
def test_cursor_cannot_change_actor_tenant_endpoint_or_page_contract(source,change):
    cursor=request(source,REL,params={'limit':1}).json()['next_cursor']
    kwargs={};path=REL;limit=1
    if change=='subject':kwargs['subject']='beneficiary'
    elif change=='tenant':kwargs['tenant']=B
    elif change=='endpoint':path=BOOK+'/transactions'
    elif change=='limit':limit=2
    else:cursor=cursor[:-1]+('a' if cursor[-1]!='a' else 'b')
    assert request(source,path,params={'limit':limit,'cursor':cursor},**kwargs).status_code==400


def test_reseed_generation_invalidates_cursor(source):
    cursor=request(source,REL,params={'limit':1}).json()['next_cursor']
    from airport_reference.seed import seed_database
    seed_database(source[1],replace=True)
    assert request(source,REL,params={'limit':1,'cursor':cursor}).status_code==400


@pytest.mark.parametrize('params',[{'limit':0},{'limit':101},{'tenant_id':str(B)},{'subject':'payer'},[('limit',1),('limit',2)]])
def test_query_claims_duplicates_and_limits_are_rejected(source,params):
    assert request(source,REL,params=params).status_code in {400,422}


@pytest.mark.parametrize('method',['POST','PUT','PATCH','DELETE'])
def test_no_resource_write_routes(source,method):
    before=source[1].read_bytes()
    assert request(source,BOOK,method=method).status_code==405
    assert source[1].read_bytes()==before


def test_unavailable_store_is_503_not_empty_or_notfound(source):
    source[1].unlink()
    result=request(source,BOOK)
    assert result.status_code==503 and result.json()['error']['code']=='SOURCE_UNAVAILABLE'


def test_health_and_production_docs(source):
    assert request(source,'/health/live',headers={}).json()=={'status':'ok','scope':'process'}
    app,path,settings=source
    from airport_reference.main import create_app
    prod=(create_app(settings.model_copy(update={'env':'production'})),path,settings)
    assert request(prod,'/openapi.json',headers={}).status_code==404
    assert request(prod,BOOK).status_code==403


def test_safe_error_log_and_request_id(source,monkeypatch,caplog):
    app,path,settings=source
    # Deliberate fixture failure: no provider, key or SQL text may reach logs/body.
    def fail(*args,**kwargs):raise RuntimeError(TOKEN+' '+SECRET+' provider-secret-sentinel')
    monkeypatch.setattr('airport_reference.service.SourceService.booking',fail)
    result=request(source,BOOK)
    assert result.status_code==500
    assert TOKEN not in result.text+caplog.text and SECRET not in result.text+caplog.text
    assert 'provider-secret-sentinel' not in result.text+caplog.text



def test_invalid_metadata_is_unavailable_not_an_empty_page(source):
    update(source,"DELETE FROM metadata WHERE key='generation'")
    result=request(source,REL)
    assert result.status_code==503 and result.json()['error']['code']=='SOURCE_UNAVAILABLE'
