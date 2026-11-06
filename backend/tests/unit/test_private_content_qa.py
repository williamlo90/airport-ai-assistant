"""Independent source-verdict boundary; Lead executes, no DB/provider calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.private_content import PrivateContentGate
from test_private_content import actor, projection


@pytest.mark.parametrize('verdict', ['false', {'allowed': True}])
def test_only_literal_true_source_verdict_can_release_private_candidate(verdict):
    identity = actor()
    loader = AsyncMock(return_value=projection(identity, uuid4()))
    access = SimpleNamespace(allows=AsyncMock(return_value=verdict))
    with pytest.raises(AuthError) as error:
        asyncio.run(PrivateContentGate(access).read(identity, loader))
    assert error.value.status == 404
    assert error.value.code == 'RESOURCE_NOT_FOUND'
    loader.assert_awaited_once()
    access.allows.assert_awaited_once()
