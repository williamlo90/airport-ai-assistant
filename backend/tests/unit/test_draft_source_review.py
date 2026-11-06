"""Human consent binds typed source facts, timestamps and whole requirements."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.infrastructure.database.draft_repository import draft_row,DraftSQL
from airport_assistant.modules.drafts.models import (
    DraftError,seal,require_integrity,require_confirmation,canonical_hash,
    reviewed_summary,with_source_evidence,
)
from airport_assistant.modules.source.evidence import SourceEvidence,ObservedSource
from test_draft_rules import setup,report
from test_source_evidence import items


def reviewed():
    _,_,draft,*_=setup()
    book,tx,policy=items()
    evidence=SourceEvidence.capture(draft.tenant_id,(ObservedSource(book),ObservedSource(tx),ObservedSource(policy,book)))
    payload=report(booking_reference=book.reference,transaction_reference=tx.reference)
    draft=replace(draft,payload=payload)
    return seal(with_source_evidence(draft,evidence)),evidence


def sql_row(draft,observations=None):
    return (draft.id,draft.tenant_id,draft.conversation_id,draft.actor_principal_id,draft.actor_session_id,
        draft.created_session_id,draft.action_type,draft.payload.model_dump(mode='json'),
        draft.destination.model_dump(mode='json'),draft.destination.queue_id,draft.displayed_summary,
        draft.language,draft.version,draft.payload_hash,draft.created_at,draft.expires_at,draft.state,
        draft.case_id,list(draft.resource_security_labels),None,None,None,None,
        draft.source_evidence.to_records() if observations is None and draft.source_evidence else [] if observations is None else observations)


def test_positive_storage_roundtrip_and_preview_bind_actual_facts_and_timestamps():
    draft,evidence=reviewed();restored=draft_row(sql_row(draft))
    assert restored==draft and restored.source_evidence==evidence
    require_integrity(restored)
    for text in ('PK-A-001','TX-A-001','10000','Diperiksa:','Berlaku mulai:'):
        assert text in restored.displayed_summary
    assert 'required_source_scopes' not in restored.displayed_summary
    assert 'records_json' not in repr(restored)
    require_confirmation(restored,expected_version=1,payload_hash=draft.payload_hash,now=draft.created_at)


@pytest.mark.parametrize('change',['amount','time','policy','labels','tenant','display','remove_evidence'])
def test_old_confirmation_cannot_authorize_modified_evidence_or_classification(change):
    draft,evidence=reviewed()
    if change=='remove_evidence':changed=replace(draft,source_evidence=None)
    elif change=='display':changed=replace(draft,displayed_summary='Omitted source facts')
    elif change=='labels':changed=replace(draft,resource_security_labels=())
    elif change=='tenant':changed=replace(draft,tenant_id=uuid4())
    else:
        records=evidence.to_records()
        if change=='amount':next(r for r in records if r['kind']=='transaction')['data']['amount_minor']+=1
        elif change=='time':next(r for r in records if r['kind']=='transaction')['data']['observed_at']='2026-11-02T00:00:00Z'
        else:next(r for r in records if r['kind']=='policy')['data']['instructions']['id']='A different instruction.'
        newer=SourceEvidence.from_records(draft.tenant_id,records,expected_labels=evidence.labels)
        changed=replace(draft,source_evidence=newer)
    with pytest.raises(DraftError):
        require_confirmation(changed,expected_version=1,payload_hash=draft.payload_hash,now=draft.created_at)


@pytest.mark.parametrize('changes',[
    dict(booking_reference='OTHER'),dict(booking_reference=None),
    dict(transaction_reference='OTHER'),dict(transaction_reference=None),dict(service_type='lounge'),
])
def test_edit_cannot_reuse_the_snapshot_for_different_references_or_service(changes):
    draft,evidence=reviewed()
    payload=draft.payload.model_copy(update=changes)
    with pytest.raises(ValueError):
        reviewed_summary(draft.action_type,payload,draft.destination,'en',source_evidence=evidence)


def test_report_edit_and_language_change_preserve_same_immutable_snapshot_and_union():
    draft,evidence=reviewed()
    payload=draft.payload.model_copy(update={'summary':'Additional explanation','customer_report':'New customer wording'})
    summary=reviewed_summary(draft.action_type,payload,draft.destination,'en',source_evidence=evidence)
    edited=seal(replace(draft,payload=payload,language='en',version=2,displayed_summary=summary))
    assert edited.source_evidence==draft.source_evidence and edited.resource_security_labels==draft.resource_security_labels
    assert edited.payload_hash!=draft.payload_hash
    assert 'Checked:' in edited.displayed_summary and 'Effective from:' in edited.displayed_summary
    require_integrity(edited)


def test_replacing_an_existing_snapshot_requires_a_new_review_workflow():
    draft,evidence=reviewed()
    with pytest.raises(ValueError):with_source_evidence(draft,evidence)


def test_evidence_union_never_removes_an_existing_requirement():
    _,_,draft,*_=setup();book,tx,policy=items()
    extra=dict(source='reference',resource_type='booking',reference='OLD-BOOKING',required_source_scopes=['booking:read'])
    draft=replace(draft,payload=report(booking_reference=book.reference),resource_security_labels=(extra,))
    evidence=SourceEvidence.capture(draft.tenant_id,(ObservedSource(book),))
    result=seal(with_source_evidence(draft,evidence))
    assert len(result.resource_security_labels)==2
    assert extra in result.resource_security_labels
    require_integrity(result)


def test_foreign_tenant_or_fabricated_evidence_basis_cannot_be_sealed():
    draft,evidence=reviewed()
    for changed in (replace(evidence,tenant_id=uuid4()),replace(evidence,basis_hash='0'*64)):
        with pytest.raises(DraftError):seal(replace(draft,source_evidence=changed))


def test_whole_preview_overflow_is_rejected_without_truncating_source_or_user_report():
    draft,evidence=reviewed();records=evidence.to_records()
    policy=next(r for r in records if r['kind']=='policy')
    policy['data']['instructions']={'id':'x'*4000,'en':'x'*4000}
    large=SourceEvidence.from_records(draft.tenant_id,records,expected_labels=evidence.labels)
    payload=draft.payload.model_copy(update={'customer_report':'y'*4000})
    with pytest.raises(ValueError):
        reviewed_summary(draft.action_type,payload,draft.destination,'en',source_evidence=large)


@pytest.mark.parametrize('observations',[None,{},'[]',[{'kind':'forged'}]])
def test_bad_stored_evidence_never_becomes_an_unlabelled_valid_draft(observations):
    draft,evidence=reviewed();row=list(sql_row(draft));row[-1]=observations
    with pytest.raises(DraftError):draft_row(tuple(row))


def test_lost_storage_evidence_does_not_match_the_original_consent_hash():
    draft,evidence=reviewed();restored=draft_row(sql_row(draft,observations=[]))
    with pytest.raises(DraftError):require_integrity(restored)


def test_private_insert_transmits_immutable_observations_and_labels_together():
    draft,evidence=reviewed();cursor=SimpleNamespace(fetchone=AsyncMock(return_value=sql_row(draft)))
    connection=SimpleNamespace(execute=AsyncMock(return_value=cursor))
    context=SimpleNamespace(tenant_id=draft.tenant_id)
    value=asyncio.run(DraftSQL(connection,'app',context).create_draft(draft))
    assert value==draft
    query,values=connection.execute.await_args.args
    assert 'source_observations' in query.as_string() and 'resource_security_labels' in query.as_string()
    assert values[-1].obj==evidence.to_records() and values[-2].obj==list(draft.resource_security_labels)


def test_public_insert_and_historical_row_do_not_require_the_future_column():
    _,_,draft,*_=setup();cursor=SimpleNamespace(fetchone=AsyncMock(return_value=sql_row(draft)))
    connection=SimpleNamespace(execute=AsyncMock(return_value=cursor))
    value=asyncio.run(DraftSQL(connection,'app',None).create_draft(draft))
    assert value==draft and canonical_hash(value)==draft.payload_hash
    require_integrity(draft_row(sql_row(draft)[:19]))
    query,values=connection.execute.await_args.args
    insert_columns=query.as_string().split(' VALUES')[0]
    assert 'source_observations' not in insert_columns
