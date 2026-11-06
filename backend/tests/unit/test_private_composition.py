"""Private runtime activation is explicit and requires SQL admission before use."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from airport_assistant.auth_config import AuthSettings
from airport_assistant.agent_config import AgentSettings
from airport_assistant.composition import build_services
from airport_assistant.config import Settings,ConfigurationError
from airport_assistant.main import create_app


def compose(**kwargs):
    return build_services(database=SimpleNamespace(require_private_mail_history=Mock()),
        auth_settings=AuthSettings(SESSION_SECRET='synthetic-test-only-'*3),
        source_client=object(),tenant_map={},**kwargs)


def test_defaults_keep_private_capabilities_disabled():
    assert Settings(env='test').private_source_enabled is False
    services=compose()
    assert services.ai_publication.private_capability is None
    assert services.staff_messages._disclosure is None


@pytest.mark.parametrize('kwargs',[{}, {'database':object()}, {'private_enabled':'true'}])
def test_missing_dependencies_or_implicit_switch_fail_closed(kwargs):
    with pytest.raises(ValueError):build_services(**dict(kwargs,private_enabled=kwargs.get('private_enabled',True)))


def test_private_activation_connects_gate_and_checked_publication_to_actual_delivery():
    services=compose(private_enabled=True,agent_settings=AgentSettings(enabled=True,api_key='synthetic-not-a-real-key'))
    assert services.agent_workflow.engine.checkpoints.enabled is True
    assert services.agent_delivery.publication is services.ai_publication
    assert services.ai_publication.private_capability is not None
    assert services.ai_publication.private_capability.gate.source_access is services.private_source_access
    assert services.staff_messages._disclosure is not None
    assert services.staff_translations.private is not None
    assert services.delivery_status._history.private_history is True
    assert services.case_lifecycle._mail_history.private_history is True


def test_app_rejects_private_runtime_without_required_services():
    with pytest.raises(ConfigurationError):create_app(Settings(env='test',private_source_enabled=True))


def test_custom_schema_reaches_every_database_repository():
    database=SimpleNamespace(schema='isolated_test',require_private_mail_history=Mock())
    services=build_services(database=database,
        auth_settings=AuthSettings(SESSION_SECRET='synthetic-test-only-'*3),
        source_client=object(),tenant_map={},private_enabled=True,schema='isolated_test',
        agent_settings=AgentSettings(enabled=True,api_key='synthetic-not-a-real-key'))
    seen=set();repositories=[]
    def visit(value):
        if id(value) in seen:return
        seen.add(id(value))
        if type(value).__name__.startswith('Postgres'):
            repositories.append(value)
            assert getattr(value,'_schema',getattr(value,'schema',None))=='isolated_test',type(value).__name__
        if type(value).__module__.startswith('airport_assistant'):
            for item in vars(value).values():visit(item)
    visit(services)
    assert len(repositories)>=16
    database.require_private_mail_history.assert_called_once_with('isolated_test')


@pytest.mark.parametrize('schema,environment',[('app;drop','test'),('isolated_test','development')])
def test_non_test_or_invalid_schema_rejected_before_resources(schema,environment,monkeypatch):
    from airport_assistant.infrastructure.database.migrations import MigrationError
    construct=Mock(side_effect=AssertionError('Resources must not be constructed'))
    monkeypatch.setattr('airport_assistant.main.build_resources',construct)
    with pytest.raises((MigrationError,ConfigurationError)):
        create_app(Settings(env=environment),schema=schema)
    construct.assert_not_called()


def test_injected_database_schema_must_match():
    from airport_assistant.composition import ApplicationResources
    with pytest.raises(ConfigurationError):
        create_app(Settings(env='test'),schema='isolated_test',
                   resources=ApplicationResources(SimpleNamespace(schema='app')))


def test_nested_verification_routing_uses_selected_schema_without_provider_call():
    from airport_assistant.composition import build_verification_sender
    database=SimpleNamespace(schema='isolated_test')
    settings=SimpleNamespace(auth_enabled=True,auth_routes={})
    transport=object()
    sender=build_verification_sender(settings,database=database,transport=transport,schema='isolated_test')
    assert sender._routing._schema=='isolated_test'
    assert sender._routing._database is database
    assert sender._transport is transport
    assert database.auth_mail_required is True


def test_private_history_cannot_admit_another_runtime_schema():
    from airport_assistant.infrastructure.database.connections import RuntimeDatabase
    database=RuntimeDatabase('postgresql://synthetic:synthetic@localhost/test',schema='isolated_test')
    with pytest.raises(ValueError):database.require_private_mail_history('app')
    assert database._private_history_schema is None
    database.require_private_mail_history('isolated_test')
    assert database._private_history_schema=='isolated_test'


@pytest.mark.parametrize('schema',['app','isolated_test'])
def test_worker_retains_explicit_schema_through_base_runtime_initialization(schema):
    from airport_assistant.infrastructure.database.jobs import WorkerDatabase
    database=WorkerDatabase('postgresql://synthetic:synthetic@localhost/test',schema=schema)
    assert database.schema==schema
