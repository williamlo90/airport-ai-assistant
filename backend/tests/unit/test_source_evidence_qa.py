"""Stored policy evidence must retain the booking binding and applicability."""
from datetime import timedelta
from uuid import uuid4

import pytest

from airport_assistant.modules.source.evidence import SourceEvidence, ObservedSource
from test_source_evidence import items


@pytest.mark.parametrize('field,value', [('service_type', 'lounge'), ('reference', 'unrelated-policy')])
def test_policy_only_restore_rejects_changed_policy_booking_binding(field, value):
    book, transaction, policy = items()
    evidence = SourceEvidence.capture(uuid4(), (ObservedSource(policy, book),))
    records = evidence.to_records()
    policy_record = next(record for record in records if record['kind'] == 'policy')
    policy_record['data'][field] = value
    with pytest.raises(ValueError):
        SourceEvidence.from_records(evidence.tenant_id, records, expected_labels=evidence.labels)


@pytest.mark.parametrize('language', ['en', 'id'])
def test_policy_review_displays_effective_date_for_human_consent(language):
    book, transaction, policy = items()
    # Distinct applicability time prevents a checked/updated timestamp from
    # accidentally satisfying the assertion.
    policy = policy.model_copy(update={'effective_from': policy.effective_from + timedelta(days=5)})
    evidence = SourceEvidence.capture(uuid4(), (ObservedSource(policy, book),))
    effective = policy.effective_from.isoformat().replace('+00:00', 'Z')
    assert effective in evidence.display(language)
