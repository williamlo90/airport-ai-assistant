from uuid import uuid4
import pytest
from airport_assistant.infrastructure.database.jobs import PostgresScheduler


def test_scope_none_empty_and_uuid_are_distinct():
 q=PostgresScheduler(None);assert q.job_scope_ids is None
 q.job_scope_ids=();assert q.job_scope_ids==()
 id=uuid4();q.job_scope_ids=[id];assert q.job_scope_ids==(id,)
 q.job_scope_ids=None;assert q.job_scope_ids is None


@pytest.mark.parametrize('value',[[None],['not-uuid'],[uuid4()]*1001])
def test_scope_rejects_invalid_or_unbounded_input(value):
 with pytest.raises(ValueError):PostgresScheduler(None,job_scope_ids=value)
