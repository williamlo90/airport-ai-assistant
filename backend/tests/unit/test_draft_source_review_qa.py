"""Hiding the evidence field must also hide its rendered private facts."""
from dataclasses import replace

from airport_assistant.modules.drafts.models import reviewed_summary, seal
from airport_assistant.modules.source.evidence import SourceEvidence
from test_draft_source_review import reviewed


def test_draft_repr_does_not_reveal_source_facts_through_the_human_preview():
    draft, evidence = reviewed()
    records = evidence.to_records()
    marker = 'PRIVATE_POLICY_TEXT_FOR_REPR_REGRESSION'
    policy = next(record for record in records if record['kind'] == 'policy')
    policy['data']['instructions']['id'] = marker
    newer = SourceEvidence.from_records(draft.tenant_id, records, expected_labels=evidence.labels)
    summary = reviewed_summary(draft.action_type, draft.payload, draft.destination,
                               draft.language, draft.case_id, source_evidence=newer)
    checked = seal(replace(draft, source_evidence=newer, displayed_summary=summary))
    assert marker in checked.displayed_summary
    assert marker not in repr(checked)
