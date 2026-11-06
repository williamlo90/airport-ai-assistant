"""Independent namespace isolation regression; no HTTP, env or runner execution."""
import agent_acceptance_v3 as legacy
import agent_selection as evaluator
import translation_acceptance as selected


def test_configuring_translation_does_not_redirect_legacy_acceptance_output(monkeypatch):
    for name in ('DATA', 'CONFIG', 'OUT', 'source_hashes', 'baseline'):
        monkeypatch.setattr(evaluator, name, getattr(evaluator, name))
    monkeypatch.setattr(legacy, 'OUT', legacy.OUT)
    original_output = legacy.OUT

    selected.configure()
    assert evaluator.OUT == selected.OUT
    assert legacy.OUT == original_output

    legacy.configure()
    assert evaluator.OUT == original_output
    assert evaluator.DATA == legacy.DATA
    assert evaluator.CONFIG == legacy.CONFIG
