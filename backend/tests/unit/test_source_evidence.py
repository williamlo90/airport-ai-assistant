"""Typed immutable facts and label/consent bindings; no source network."""
from dataclasses import FrozenInstanceError
from datetime import timedelta
from uuid import uuid4

import pytest

from airport_assistant.modules.source.evidence import SourceEvidence, ObservedSource
from airport_assistant.modules.source.labels import SecurityLabels
from airport_assistant.modules.source.models import Booking, Policy
from test_source_client import booking, STAMP
from test_source_labels import observation, payment


def items():
    book = observation(Booking, booking())
    tx = payment(details=True, link=True)
    policy = observation(Policy, dict(reference=book.policy_reference, service_type=book.service_type,
        version=2, effective_from=STAMP, observed_at=STAMP, source_updated_at=STAMP,
        instructions={'en': 'Contact staff for a time change. No refund is approved here.',
                      'id': 'Hubungi petugas untuk perubahan waktu. Tidak ada refund yang disetujui di sini.'}))
    return book, tx, policy


def evidence():
    book, tx, policy = items()
    return SourceEvidence.capture(uuid4(), (ObservedSource(book), ObservedSource(tx), ObservedSource(policy, book)))


def test_capture_round_trip_recomputes_whole_dto_union_and_exact_immutable_basis():
    value = evidence()
    restored = SourceEvidence.from_records(value.tenant_id, value.to_records(), expected_labels=value.labels)
    assert restored == value
    assert len(value.labels.labels) == 2
    assert set(value.labels.labels[1].required_source_scopes) == {'payment_status:read', 'payment_details:read'}
    assert len(value.fact_hash) == len(value.basis_hash) == 64
    with pytest.raises(FrozenInstanceError):
        value.fact_hash = 'rewritten'
    records = value.to_records()
    records[0]['data']['status'] = 'cancelled'
    assert value.to_records()[0]['data']['status'] == 'confirmed'
    assert 'PK-A-001' not in repr(value)


def test_fact_hash_ignores_read_time_but_not_source_updates_or_real_facts():
    book, tx, policy = items()
    tenant = uuid4()
    initial = SourceEvidence.capture(tenant, (ObservedSource(book),))
    fresh = SourceEvidence.capture(tenant, (ObservedSource(book.model_copy(update={'observed_at': book.observed_at+timedelta(seconds=1)})),))
    updated = SourceEvidence.capture(tenant, (ObservedSource(book.model_copy(update={'source_updated_at': book.source_updated_at+timedelta(seconds=1)})),))
    changed = SourceEvidence.capture(tenant, (ObservedSource(book.model_copy(update={'status': 'cancelled'})),))
    assert initial.fact_hash == fresh.fact_hash and initial.basis_hash != fresh.basis_hash
    assert updated.fact_hash != initial.fact_hash != changed.fact_hash


def test_duplicate_equal_facts_have_one_latest_snapshot_and_stable_order():
    book, tx, policy = items()
    later = book.model_copy(update={'observed_at': book.observed_at+timedelta(seconds=2)})
    tenant = uuid4()
    first = SourceEvidence.capture(tenant, (ObservedSource(book), ObservedSource(later), ObservedSource(tx)))
    second = SourceEvidence.capture(tenant, (ObservedSource(tx), ObservedSource(later), ObservedSource(book)))
    assert first == second and len(first.to_records()) == 2


def test_conflicting_resource_snapshots_cannot_be_silently_overwritten():
    book, tx, policy = items()
    with pytest.raises(ValueError):
        SourceEvidence.capture(uuid4(), (ObservedSource(book), ObservedSource(book.model_copy(update={'status': 'cancelled'}))))
    with pytest.raises(ValueError):
        SourceEvidence.capture(uuid4(), (ObservedSource(book), ObservedSource(policy, book.model_copy(update={'status': 'cancelled'}))))


def test_policy_only_hash_and_restore_bind_the_full_observed_booking():
    book, tx, policy = items()
    tenant = uuid4()
    initial = SourceEvidence.capture(tenant,(ObservedSource(policy,book),))
    changed = SourceEvidence.capture(tenant,(ObservedSource(policy,book.model_copy(update={'status':'cancelled'})),))
    assert initial.fact_hash != changed.fact_hash and initial.basis_hash != changed.basis_hash
    record = initial.to_records()[0]
    assert record['booking']['policy_reference'] == policy.reference
    assert SourceEvidence.from_records(tenant,initial.to_records(),expected_labels=initial.labels) == initial


def test_duplicate_policy_binding_timestamps_are_deterministic_in_either_order():
    book, tx, policy = items()
    later = book.model_copy(update={'observed_at':book.observed_at+timedelta(seconds=1)})
    tenant = uuid4()
    first = SourceEvidence.capture(tenant,(ObservedSource(policy,book),ObservedSource(policy,later)))
    second = SourceEvidence.capture(tenant,(ObservedSource(policy,later),ObservedSource(policy,book)))
    assert first == second


@pytest.mark.parametrize('binding', ['missing', 'wrong_policy', 'wrong_service'])
def test_policy_requires_the_exact_observed_booking_binding(binding):
    book, tx, policy = items()
    if binding == 'missing':
        item = ObservedSource(policy)
    elif binding == 'wrong_policy':
        item = ObservedSource(policy, book.model_copy(update={'policy_reference': 'parking-v3'}))
    else:
        item = ObservedSource(policy, book.model_copy(update={'service_type': 'lounge'}))
    with pytest.raises(ValueError):
        SourceEvidence.capture(uuid4(), (item,))


def test_stored_snapshot_cannot_drop_payment_details_or_booking_link_requirement():
    value = evidence()
    weak = SecurityLabels.from_records(value.tenant_id, [dict(source='reference', resource_type='transaction',
        reference='TX-A-001', required_source_scopes=['payment_status:read'])])
    with pytest.raises(ValueError):
        SourceEvidence.from_records(value.tenant_id, value.to_records(), expected_labels=weak)
    with pytest.raises(ValueError):
        SourceEvidence.from_records(uuid4(), value.to_records(), expected_labels=value.labels)


@pytest.mark.parametrize('change', ['unknown_key', 'version_bool', 'version_unknown', 'client_labels', 'extra_authority', 'nul', 'surrogate'])
def test_malformed_or_client_authority_fields_are_rejected_without_echoing_source_data(change):
    value = evidence()
    records = value.to_records()
    record = records[0]
    if change == 'unknown_key':record['unknown'] = 'not allowed'
    elif change == 'version_bool':record['schema_version'] = True
    elif change == 'version_unknown':record['schema_version'] = 2
    elif change == 'client_labels':record['resource_security_labels'] = []
    elif change == 'extra_authority':record['data']['tenant_id'] = str(uuid4())
    else:record['data']['location'] = '\0' if change == 'nul' else '\ud800'
    with pytest.raises(ValueError) as error:
        SourceEvidence.from_records(value.tenant_id, records, expected_labels=value.labels)
    assert str(error.value) == 'Invalid stored source evidence.'
    assert 'PK-A-001' not in str(error.value)


def test_review_text_has_fact_values_and_dates_but_no_access_control_implementation_fields():
    value = evidence()
    text = value.display('en')
    assert 'Booking: PK-A-001' in text and 'Transaction: TX-A-001' in text
    assert 'Amount: IDR 10000' in text and '2026-11-10 07:00 Asia/Jakarta' in text
    assert 'No refund is approved here.' in text
    assert 'allowed_scopes' not in text and 'required_source_scopes' not in text
    assert 'Informasi sumber' in value.display('id')


def test_missing_amount_is_not_zero_and_literal_zero_remains_zero():
    tenant = uuid4()
    minimal = SourceEvidence.capture(tenant, (ObservedSource(payment()),))
    assert 'Amount:' not in minimal.display('en')
    full = payment(details=True).model_copy(update={'amount_minor': 0, 'status': 'unknown'})
    text = SourceEvidence.capture(tenant, (ObservedSource(full),)).display('en')
    assert 'Amount: IDR 0' in text and 'Status: unknown' in text


@pytest.mark.parametrize('observations', [(), tuple(ObservedSource(payment()) for _ in range(9)), ({'data': 'client'},)])
def test_snapshot_count_and_typed_boundary_are_bounded(observations):
    with pytest.raises(ValueError):
        SourceEvidence.capture(uuid4(), observations)
