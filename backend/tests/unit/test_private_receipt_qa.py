"""Recovery uses the actual SQL receipt shape, including its consent version."""
import asyncio

import pytest

from airport_assistant.modules.drafts.models import DraftError
from test_private_drafts_receipts import receipt_setup


def sql_receipt(draft, result):
    return dict(draft_id=draft.id, state='committed', case_id=result.case_id,
                result_ref=result.result_ref, version=draft.version, expires_at=None)


def test_recovery_accepts_actual_sql_receipt_mapping():
    identity, draft, tx, parent, access, service, result = receipt_setup()
    receipt = sql_receipt(draft, result)
    tx.receipt.return_value = receipt
    assert asyncio.run(service.status(identity, draft.id)) == receipt
    access.allows.assert_awaited_once()


def test_recovery_rejects_changed_consent_version_after_source_check():
    identity, draft, tx, parent, access, service, result = receipt_setup()
    receipt = sql_receipt(draft, result)
    tx.receipt.side_effect = [receipt, dict(receipt, version=receipt['version'] + 1)]
    with pytest.raises(DraftError) as error:
        asyncio.run(service.status(identity, draft.id))
    assert error.value.code == 'SOURCE_CONTEXT_CHANGED'
