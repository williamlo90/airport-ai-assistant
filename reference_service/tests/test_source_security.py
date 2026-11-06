"""Caller assertions, replay boundaries, malformed headers and protocol binding."""

import json
import time
from conftest import request,A,B,TOKEN,SECRET
import pytest
from airport_reference.security import signed_headers,encode,decode,signature,cursor_decode
from starlette.datastructures import Headers

BOOK='/source/v1/bookings/PK-A-001'


def headers(path=BOOK,**kwargs):return signed_headers(TOKEN,SECRET,A,'owner',path=path,**kwargs)


def changed_claims(mutator):
    value=headers();body=json.loads(decode(value['X-Source-Context']))
    mutator(body)
    context=encode(json.dumps(body).encode())
    value['X-Source-Context']=context
    value['X-Source-Signature']=signature(SECRET,'GET',BOOK.encode(),b'',context)
    return value


@pytest.mark.parametrize('fault',['missing','token_only','context_only','wrong_token','wrong_signature','oversized','noncanonical','invalid_json'])
def test_service_and_context_both_required(source,fault):
    value=headers()
    if fault=='missing':value={}
    elif fault=='token_only':value={'Authorization':value['Authorization']}
    elif fault=='context_only':value.pop('Authorization')
    elif fault=='wrong_token':value['Authorization']='Bearer invalid'
    elif fault=='wrong_signature':value['X-Source-Signature']='0'*64
    elif fault=='oversized':value['X-Source-Context']='A'*2049
    else:
        context=value['X-Source-Context']+'=' if fault=='noncanonical' else encode(b'not-json')
        value['X-Source-Context']=context;value['X-Source-Signature']=signature(SECRET,'GET',BOOK.encode(),b'',context)
    response=request(source,BOOK,headers=value)
    assert response.status_code==401
    assert TOKEN not in response.text and SECRET not in response.text and value.get('X-Source-Context','not-present') not in response.text


@pytest.mark.parametrize('name',['Authorization','X-Source-Context','X-Source-Signature','Host'])
def test_duplicate_proof_or_host_rejected(source,name):
    value=list(headers().items())
    if name=='Host':value += [('Host','127.0.0.1:8010'),('Host','127.0.0.1:8010')]
    else:value.append((name,dict(value)[name]))
    assert request(source,BOOK,headers=value).status_code==401


@pytest.mark.parametrize('claim,value',[('aud','other-service'),('v',2),('subject','owner@example.com'),('tenant_id','not-a-uuid'),('iat',True),('extra','admin')])
def test_malformed_or_unexpected_claims_fail_even_with_valid_mac(source,claim,value):
    proof=changed_claims(lambda data:data.update({claim:value}))
    assert request(source,BOOK,headers=proof).status_code==401


@pytest.mark.parametrize('case',['expired','future','ttl_too_long','negative_ttl'])
def test_time_window_is_bounded(source,case):
    now=int(time.time())
    def mutate(data):
        if case=='expired':data.update(iat=now-60,exp=now)
        elif case=='future':data.update(iat=now+30,exp=now+60)
        elif case=='ttl_too_long':data.update(iat=now,exp=now+61)
        else:data.update(iat=now,exp=now-1)
    assert request(source,BOOK,headers=changed_claims(mutate)).status_code==401


def test_duplicate_claim_keys_are_not_accepted(source):
    proof=headers();raw=decode(proof['X-Source-Context'])
    context=encode(raw[:-1]+b',"subject":"payer"}')
    proof['X-Source-Context']=context;proof['X-Source-Signature']=signature(SECRET,'GET',BOOK.encode(),b'',context)
    assert request(source,BOOK,headers=proof).status_code==401


@pytest.mark.parametrize('alteration',['path','query','tenant','subject','encoded_path'])
def test_signature_cannot_move_between_paths_queries_or_actors(source,alteration):
    proof=headers();path=BOOK;params=None
    if alteration=='path':path='/source/v1/bookings/LG-A-001'
    elif alteration=='query':params={'limit':1}
    elif alteration=='encoded_path':path='/source/v1/bookings/%50K-A-001'
    else:
        body=json.loads(decode(proof['X-Source-Context']));body['tenant_id' if alteration=='tenant' else 'subject']=str(B) if alteration=='tenant' else 'payer'
        proof['X-Source-Context']=encode(json.dumps(body).encode())
    assert request(source,path,params=params,headers=proof).status_code==401


def test_query_order_and_duplicate_query_are_exactly_signed(source):
    path='/source/v1/subjects/me/relations'
    proof=headers(path,query=b'limit=1&cursor=fake')
    assert request(source,path,params=[('cursor','fake'),('limit','1')],headers=proof).status_code==401
    assert request(source,path,params=[('limit','1'),('limit','1')],headers=headers(path,query=b'limit=1')).status_code==401


def test_read_replay_within_ttl_rechecks_current_grants(source):
    proof=headers()
    assert request(source,BOOK,headers=proof).status_code==200
    assert request(source,BOOK,headers=proof).status_code==200
    import sqlite3
    c=sqlite3.connect(source[1])
    try:
        with c:c.execute('DELETE FROM relations WHERE tenant_id=? AND subject_reference=?',(str(A),'owner'))
    finally:c.close()
    assert request(source,BOOK,headers=proof).status_code==404


def test_http_only_for_loopback_development(source):
    assert request(source,BOOK,base='http://example.test',peer='203.0.113.5').status_code==403
    assert request(source,BOOK,base='http://localhost',peer='203.0.113.5').status_code==403
    assert request(source,BOOK,base='https://example.test',peer='203.0.113.5').status_code==200


def test_get_body_and_actual_stream_limit(source):
    assert request(source,BOOK,body=b'x').status_code==400
    assert request(source,BOOK,body=b'x'*1025).status_code==413


def test_cursor_expiry_and_domain_separation(source):
    from airport_reference.security import cursor_encode
    from airport_reference.security import SourceError
    cursor=cursor_encode(SECRET,{'actor':'owner'},'last',now=100)
    with pytest.raises(SourceError):cursor_decode(SECRET,{'actor':'owner'},cursor,now=1000)
    with pytest.raises(SourceError):cursor_decode(TOKEN,{'actor':'owner'},cursor,now=101)


@pytest.mark.parametrize('name',['X-Tenant-ID','X-Subject-ID','X-Principal-ID','X-Source-Subject'])
def test_public_identity_headers_cannot_override_signed_context(source,name):
    proof=headers();proof[name]='payer'
    assert request(source,BOOK,headers=proof).status_code==400
