"""A failover to a fallback_models entry must be visible: reported to the
capabilities registry (MODEL degraded), announced through on_retry, sized to
the fallback's own token limits, and cleared when the primary answers again."""

import pytest

from agent8088 import capabilities


class _Unreachable(Exception):
    status_code = 503


def _response(text="ok"):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": text})(),
        "finish_reason": "stop",
    })()]})()


@pytest.fixture
def failover(monkeypatch, engine):
    import agent8088.providers as providers
    monkeypatch.setattr(providers, "probe_model_context_window", lambda *a, **k: (None, None))
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 0)
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "primaryprov")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "primaryprov")
    monkeypatch.setattr(engine, "MODEL_NAME", "big-model")
    monkeypatch.setitem(engine.PROVIDERS, "primaryprov",
                        {"model": "big-model", "context_window": "200000",
                         "max_completion_tokens": "64000"})
    monkeypatch.setitem(engine.PROVIDERS, "smallprov",
                        {"model": "small-model", "context_window": "8192",
                         "max_completion_tokens": "2048"})
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [("smallprov", "small-model")])
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))
    state = {"primary_down": True, "calls": []}

    def _fake(client, messages, tools, **kw):
        state["calls"].append(kw)
        if kw.get("telemetry_attempt") == "primary" and state["primary_down"]:
            raise _Unreachable("service unavailable")
        return _response(kw.get("model_name"))

    monkeypatch.setattr(engine, "create_completion", _fake)
    return engine, state


def _call(engine, notices, max_tokens=None):
    return engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=0.1,
        system_prompt="", on_token=None, interrupt_check=lambda: None,
        trace=None, turn=1, max_tokens=max_tokens, on_retry=notices.append)


def test_failover_reports_model_degraded_and_notifies(failover):
    engine, state = failover
    notices = []
    result = _call(engine, notices)
    assert result.choices[0].message.content == "small-model"

    entry = capabilities.get(capabilities.MODEL)
    assert entry is not None and entry.state == capabilities.DEGRADED
    assert entry.active == "smallprov:small-model"
    assert entry.preferred == "primaryprov:big-model"
    assert "smallprov:small-model" in entry.impact
    assert "/model" in entry.fix and "/doctor" in entry.fix
    assert entry.reason  # the primary's error, explained
    assert any(n.startswith("primary primaryprov:big-model failed (")
               and "answering with smallprov:small-model" in n for n in notices)
    assert engine.LAST_MODEL_SERVED["fallback_for"] == "primaryprov:big-model"


def test_fallback_gets_its_own_max_tokens(failover):
    engine, state = failover
    _call(engine, [], max_tokens=64000)  # sized for the primary
    fallback_call = state["calls"][-1]
    assert fallback_call["telemetry_attempt"] == "fallback"
    assert fallback_call["max_tokens"] <= 2048


def test_primary_recovery_reports_ok(failover):
    engine, state = failover
    _call(engine, [])
    assert capabilities.get(capabilities.MODEL).state == capabilities.DEGRADED
    state["primary_down"] = False
    _call(engine, [])
    entry = capabilities.get(capabilities.MODEL)
    assert entry.state == capabilities.OK
    assert entry.active == "primaryprov:big-model"
    assert engine.LAST_MODEL_SERVED["fallback_for"] == ""


def test_healthy_primary_reports_nothing(failover):
    engine, state = failover
    state["primary_down"] = False
    _call(engine, [])
    assert capabilities.get(capabilities.MODEL) is None


def test_subagent_model_success_does_not_clear_main_failover(failover):
    engine, state = failover
    _call(engine, [])
    engine._note_model_served("otherprov", "other-model")
    assert capabilities.get(capabilities.MODEL).state == capabilities.DEGRADED


def test_the_failover_notice_is_shown_once_not_on_every_call(failover):
    # Found live: "primary … failed; answering with …" printed before every
    # model call of every turn while the primary stayed down.
    engine, _ = failover
    notices = []
    for _ in range(3):
        _call(engine, notices)
    assert sum("answering with smallprov:small-model" in n for n in notices) == 1
