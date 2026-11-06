"""A parent lookup must not hide newly classified conversation metadata."""
from copy import deepcopy
from dataclasses import replace

import pytest

from airport_assistant.modules.drafts.models import DraftError
from airport_assistant.modules.identity.auth import AuthError
from test_private_content import records
from test_private_conversations import setup, read


@pytest.mark.parametrize('method', ['get', 'customer_message', 'customer_history'])
def test_classification_change_during_parent_lookup_cannot_emit_public_candidate(method):
    identity, id, state, case, message, tx, access, service = setup(private=False, allowed=False)

    async def parent_context(case_id):
        state['conversation'] = replace(state['conversation'], version=2,
                                        resource_security_labels=tuple(records()))
        return deepcopy(case)

    tx.source_case_context.side_effect = parent_context
    with pytest.raises((AuthError, DraftError)):
        read(service, identity, id, message, method)
