"""Independent retry-policy precedence checks; Lead executes, no real HTTP."""
import pytest

from agent_acceptance_v3 import transient


@pytest.mark.parametrize('record', [
    {'http_status': 401, 'network_category': 'read_timeout'},
    {'http_status': 403, 'network_category': 'read_timeout'},
    {'http_status': 503, 'network_category': 'invalid_provider_envelope'},
    {'http_status': 429, 'network_category': 'cancelled'},
    {'http_status': 503, 'provider_error_code': 'invalid_api_key'},
    {'http_status': 503, 'safe_error_code': 'AGENT_INVALID_RESPONSE'},
])
def test_permanent_failures_override_retryable_status_or_transport_category(record):
    assert transient(record) is False
