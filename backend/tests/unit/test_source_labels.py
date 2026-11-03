"""SRC-03A: requirements survive derivation; grants are independent and fresh."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_source_client import booking, transaction
from airport_assistant.modules.source.models import Booking, Transaction, Policy, SourceFailure
from airport_assistant.modules.source.service import SourceReadService
from airport_assistant.modules.source.grants import CurrentSourceGrants
from airport_assistant.modules.source.labels import (
    ResourceSecurityLabel, SecurityLabels, PrivateSourceAccess,
    labels_for_observation, labels_for_policy, merge_labels,
)


def observation(model, data):
    return model.model_validate_json(json.dumps(data))


def payment(details=False, link=False):
    value = transaction()
    if details:
        value.update(allowed_scopes=['payment_status:read','payment_details:read'],
                     amount_minor=10000,currency='IDR',currency_exponent=0,refund_status='none')
    if link:
        value['booking_reference']='PK-A-001'
    return observation(Transaction,value)


def dependencies():
    tenant, principal, source_tenant = uuid4(),uuid4(),uuid4()
    reader=Mock(booking=AsyncMock(return_value=observation(Booking,booking())),
                transaction=AsyncMock(return_value=payment()))
    repo=Mock(find_subject=AsyncMock(return_value='beneficiary'))
    service=SourceReadService(reader,repo,{tenant:source_tenant})
    return tenant,principal,reader,repo,CurrentSourceGrants(service,reader)


def test_labels_follow_whole_observation_and_explicit_booking_link():
    tenant=uuid4()
    minimal=labels_for_observation(tenant,payment())
    assert minimal.to_records()==[dict(source='reference',resource_type='transaction',reference='TX-A-001',required_source_scopes=['payment_status:read'])]
    full=labels_for_observation(tenant,payment(details=True,link=True))
    assert len(full.labels)==2
    assert set(full.labels[0].required_source_scopes)=={'payment_status:read','payment_details:read'}
    assert full.labels[1].resource_type=='booking'
    merged=merge_labels(minimal,full,minimal)
    assert len(merged.labels)==2
    assert SecurityLabels.from_records(tenant,merged.to_records())==merged
    assert merge_labels(full,minimal)==merged
    assert merge_labels(merged,full)==merged


def test_policy_uses_exact_observed_booking_not_latest_policy():
    tenant=uuid4();book=observation(Booking,booking())
    data=dict(reference='parking-v2',service_type='parking',version=2,effective_from=booking()['observed_at'],
              source_updated_at=booking()['observed_at'],observed_at=booking()['observed_at'],instructions={'id':'Aturan','en':'Rules'})
    policy=observation(Policy,data)
    assert labels_for_policy(tenant,book,policy)==labels_for_observation(tenant,book)
    with pytest.raises(ValueError):labels_for_policy(tenant,book,observation(Policy,{**data,'reference':'parking-v3'}))
    with pytest.raises(ValueError):labels_for_policy(tenant,book,observation(Policy,{**data,'service_type':'lounge'}))


def test_serialization_is_canonical_before_and_after_repository_round_trip():
    tenant=uuid4();original=labels_for_observation(tenant,payment(details=True,link=True))
    records=original.to_records()
    assert SecurityLabels.from_records(tenant,records).to_records()==records
    reordered=SecurityLabels(tenant,tuple(ResourceSecurityLabel.model_validate(
        {**label.model_dump(), 'required_source_scopes': tuple(reversed(label.required_source_scopes))})
        for label in reversed(original.labels)))
    assert reordered.to_records()==records
    assert [label['resource_type'] for label in records]==['booking','transaction']


@pytest.mark.parametrize('change',[
    {'source':'other'}, {'resource_type':'policy'}, {'reference':'../private'},
    {'required_source_scopes':[]}, {'required_source_scopes':['booking:read']},
    {'required_source_scopes':['payment_status:read','payment_status:read']},
    {'required_source_scopes':['case:read']}, {'admin':True},
])
def test_untrusted_or_malformed_stored_labels_fail_closed(change):
    record=labels_for_observation(uuid4(),payment()).to_records()[0]
    with pytest.raises((ValueError,ValidationError)):
        SecurityLabels.from_records(uuid4(),[{**record,**change}])


@pytest.mark.parametrize('records',[[],None,{},[{}],[{'required_source_scopes':'payment_status:read'}]])
def test_missing_labels_cannot_authorize_private_source_content(records):
    with pytest.raises(ValueError):SecurityLabels.from_records(uuid4(),records)


def test_tenant_union_and_size_are_bounded():
    tenant=uuid4();one=labels_for_observation(tenant,payment())
    with pytest.raises(ValueError):merge_labels(one,labels_for_observation(uuid4(),payment()))
    with pytest.raises(ValueError):SecurityLabels(tenant,())
    with pytest.raises(ValueError):SecurityLabels(tenant,(one.labels[0],one.labels[0]))
    records=[{**one.to_records()[0],'reference':f'TX-{n}'} for n in range(65)]
    with pytest.raises(ValueError):SecurityLabels.from_records(tenant,records)


@pytest.mark.parametrize('kind,scopes',[
    ('booking',frozenset()),('booking',frozenset({'payment_status:read'})),
    ('transaction',frozenset({'booking:read'})),('policy',frozenset({'booking:read'})),
    ('transaction',{'payment_status:read'}),
])
def test_invalid_grant_requirements_never_call_source(kind,scopes):
    async def scenario():
        tenant,principal,reader,repo,grants=dependencies()
        with pytest.raises(ValueError):await grants.has_grants(tenant_id=tenant,principal_id=principal,
            resource_type=kind,resource_reference='TX-A-001',required_scopes=scopes)
        repo.find_subject.assert_not_awaited();reader.transaction.assert_not_awaited()
    asyncio.run(scenario())


def test_grants_reload_subject_and_source_no_snapshot_or_booking_inheritance():
    async def scenario():
        tenant,principal,reader,repo,grants=dependencies()
        args=dict(tenant_id=tenant,principal_id=principal,resource_type='transaction',resource_reference='TX-A-001')
        assert await grants.has_grants(**args,required_scopes=frozenset({'payment_status:read'}))
        assert not await grants.has_grants(**args,required_scopes=frozenset({'payment_details:read'}))
        repo.find_subject.return_value='outsider'
        reader.transaction.side_effect=SourceFailure(404)
        assert not await grants.has_grants(**args,required_scopes=frozenset({'payment_status:read'}))
        assert reader.transaction.await_count==3 and repo.find_subject.await_count==3
        assert reader.transaction.await_args.args[0].subject=='outsider'
        reader.booking.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('status',[400,502,503])
def test_source_failure_never_becomes_success_or_empty_result(status):
    async def scenario():
        tenant,principal,reader,repo,grants=dependencies()
        labels=labels_for_observation(tenant,payment())
        reader.transaction.side_effect=SourceFailure(status)
        with pytest.raises(SourceFailure) as exc:
            await PrivateSourceAccess(grants).allows(tenant_id=tenant,principal_id=principal,labels=labels)
        assert exc.value.status==status
    asyncio.run(scenario())


def test_aggregate_requires_all_grants_and_rechecks_after_snapshot():
    async def scenario():
        tenant,principal,reader,repo,grants=dependencies()
        aggregate=merge_labels(labels_for_observation(tenant,observation(Booking,booking())),
                               labels_for_observation(tenant,payment(details=True)))
        access=PrivateSourceAccess(grants)
        # Booking exists but status-only recipient cannot read aggregate amounts.
        assert not await access.allows(tenant_id=tenant,principal_id=principal,labels=aggregate)
        reader.transaction.return_value=payment(details=True)
        assert await access.allows(tenant_id=tenant,principal_id=principal,labels=aggregate)
        reader.transaction.side_effect=SourceFailure(404)
        assert not await access.allows(tenant_id=tenant,principal_id=principal,labels=aggregate)
        count=repo.find_subject.await_count
        assert not await access.allows(tenant_id=uuid4(),principal_id=principal,labels=aggregate)
        assert repo.find_subject.await_count==count
    asyncio.run(scenario())


def test_missing_subject_denies_and_bad_provider_reference_fails_closed():
    async def scenario():
        tenant,principal,reader,repo,grants=dependencies()
        args=dict(tenant_id=tenant,principal_id=principal,resource_type='transaction',resource_reference='TX-A-001',required_scopes=frozenset({'payment_status:read'}))
        repo.find_subject.return_value=None
        assert not await grants.has_grants(**args)
        reader.transaction.assert_not_awaited()
        repo.find_subject.return_value='owner'
        reader.transaction.return_value=observation(Transaction,{**transaction(),'reference':'TX-OTHER'})
        with pytest.raises(SourceFailure):await grants.has_grants(**args)
    asyncio.run(scenario())


@pytest.mark.parametrize('result',[None,1,'true',[],{}])
def test_aggregate_gate_requires_explicit_boolean_grant(result):
    async def scenario():
        tenant=uuid4();grants=Mock(has_grants=AsyncMock(return_value=result))
        assert not await PrivateSourceAccess(grants).allows(tenant_id=tenant,principal_id=uuid4(),
            labels=labels_for_observation(tenant,payment()))
    asyncio.run(scenario())


def test_composition_binds_real_grant_and_private_gate_only_when_enabled():
    from airport_assistant.composition import build_services
    from airport_assistant.auth_config import AuthSettings
    assert build_services().source_grants is None
    assert build_services().private_source_access is None
    services=build_services(database=Mock(),auth_settings=AuthSettings(SESSION_SECRET='f'*48),
                            source_client=Mock(),tenant_map={uuid4():uuid4()})
    assert isinstance(services.source_grants,CurrentSourceGrants)
    assert isinstance(services.private_source_access,PrivateSourceAccess)
