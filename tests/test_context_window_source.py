"""Context windows are probed per (provider, model), their source is tracked,
a guessed default is reported as degraded, and the context meter uses
CHARS_PER_TOKEN calibrated by the server's real usage.prompt_tokens."""

import pytest

from agent8088 import capabilities


@pytest.fixture
def probing(monkeypatch, engine):
    import agent8088.providers as providers
    engine._PROBED_LIMITS.clear()
    engine._PROBE_OWNER.clear()
    engine._PROBED_SOURCE.clear()
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "kimi")
    monkeypatch.setitem(engine.PROVIDERS, "cloud", {"model": "kimi"})
    engine.APP_CONFIG.pop("context_window", None)
    engine.APP_CONFIG.pop("max_completion_tokens", None)
    monkeypatch.setattr(engine, "client", object())
    windows = {"kimi": (262144, 16384), "qwen": (131072, None), "mystery": (None, None)}
    calls = []

    def _probe(client, model_id, provider_name="", timeout=5):
        calls.append(model_id)
        return windows[model_id]

    monkeypatch.setattr(providers, "probe_model_context_window", _probe)
    return engine, calls


def test_switching_model_on_same_provider_reprobes_and_does_not_inherit(probing, monkeypatch):
    engine, calls = probing
    assert engine._active_model_token_limits()[0] == 262144
    monkeypatch.setattr(engine, "MODEL_NAME", "qwen")
    # The old model's probed value is still in PROVIDERS["cloud"]: it must not
    # read as a configured override for the new model.
    assert engine._active_model_token_limits()[0] == 131072
    assert calls == ["kimi", "qwen"]
    assert engine.context_window_source() == "probe"
    # And back again: served from the per-model cache, no new probe.
    monkeypatch.setattr(engine, "MODEL_NAME", "kimi")
    assert engine._active_model_token_limits()[0] == 262144
    assert calls == ["kimi", "qwen"]


def test_failed_probe_after_switch_does_not_keep_old_window(probing, monkeypatch):
    engine, _calls = probing
    engine._active_model_token_limits()
    monkeypatch.setattr(engine, "MODEL_NAME", "mystery")
    assert engine._active_model_token_limits()[0] == engine.CONTEXT_WINDOW
    assert engine.context_window_source() == "default"


def test_explicit_limits_for_other_model_are_ignored(probing):
    engine, _calls = probing
    engine._active_model_token_limits()  # probes kimi into PROVIDERS["cloud"]
    # Asking about a different model on the same provider (fallback sizing).
    assert engine._active_model_token_limits("cloud", "qwen")[0] == engine.CONTEXT_WINDOW


def test_config_value_is_source_config_and_not_probed(probing, monkeypatch):
    engine, calls = probing
    monkeypatch.setitem(engine.PROVIDERS, "cloud", {"model": "kimi", "context_window": "50000"})
    assert engine._active_model_token_limits()[0] == 50000
    assert engine.context_window_source() == "config"
    assert calls == []
    assert capabilities.get(capabilities.CONTEXT).state == capabilities.OK


def test_set_provider_limit_takes_ownership(probing, monkeypatch, tmp_path):
    engine, calls = probing
    monkeypatch.setattr(engine, "CONFIG_PATH", tmp_path / "config.txt")
    engine._active_model_token_limits()
    engine.set_provider_limit("cloud", "context_window", "262144")  # same number as probed
    monkeypatch.setattr(engine, "MODEL_NAME", "qwen")
    assert engine._active_model_token_limits()[0] == 262144  # now a config value
    assert engine.context_window_source() == "config"


def test_unknown_window_reports_context_degraded(probing, monkeypatch):
    engine, _calls = probing
    monkeypatch.setattr(engine, "MODEL_NAME", "mystery")
    engine._active_model_token_limits()
    entry = capabilities.get(capabilities.CONTEXT)
    assert entry.state == capabilities.DEGRADED
    assert f"assuming {engine.CONTEXT_WINDOW}" in entry.reason
    assert entry.fix == "set provider.cloud.context_window"
    # A model that probes fine clears it.
    monkeypatch.setattr(engine, "MODEL_NAME", "kimi")
    engine._active_model_token_limits()
    assert capabilities.get(capabilities.CONTEXT).state == capabilities.OK


def test_ollama_served_source(probing, monkeypatch):
    engine, _calls = probing
    monkeypatch.setattr(engine, "_ollama_served_cap", lambda p, m, ctx: 8192)
    assert engine._active_model_token_limits()[0] == 8192
    assert engine.context_window_source() == "ollama-served"


# --- context meter -----------------------------------------------------------

def test_estimate_uses_chars_per_token(engine, monkeypatch):
    monkeypatch.setattr(engine, "CHARS_PER_TOKEN", 2.0)
    engine.PROMPT_CALIBRATION.clear()
    messages = [{"role": "user", "content": "x" * 1000}]
    assert engine.estimate_prompt_tokens(messages, "") == 500


def test_estimate_anchors_on_real_prompt_tokens(engine, monkeypatch):
    monkeypatch.setattr(engine, "CHARS_PER_TOKEN", 4.0)
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "p")
    monkeypatch.setattr(engine, "MODEL_NAME", "m")
    messages = [{"role": "user", "content": "x" * 1000}]
    usage = type("U", (), {"prompt_tokens": 900})()
    engine._record_prompt_calibration(messages, "", type("R", (), {"usage": usage})(), "p", "m")
    # Same conversation: the real count, not 1000/4.
    assert engine.estimate_prompt_tokens(messages, "") == 900
    messages.append({"role": "assistant", "content": "y" * 400})
    assert engine.estimate_prompt_tokens(messages, "") == 900 + 100
    # Compaction shrank it: scale by the real ratio.
    del messages[:]
    messages.append({"role": "user", "content": "x" * 500})
    assert engine.estimate_prompt_tokens(messages, "") == 450
    # A different conversation (sub-agent) or model is not calibrated by it.
    assert engine.estimate_prompt_tokens([{"role": "user", "content": "x" * 1000}], "") == 250
    monkeypatch.setattr(engine, "MODEL_NAME", "other")
    assert engine.estimate_prompt_tokens(messages, "") == 125


def test_no_usage_leaves_calibration_alone(engine):
    engine.PROMPT_CALIBRATION.clear()
    engine._record_prompt_calibration([], "", type("R", (), {"usage": None})(), "p", "m")
    assert engine.PROMPT_CALIBRATION == {}
