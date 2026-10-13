"""Audit issues 31, 32, 35: no-status stream errors retry, each retry changes the request,
and a rejected setting costs only that setting."""
from types import SimpleNamespace as N

import pytest


def _ok():
    return N(choices=[N(message=N(content="ok", tool_calls=None), finish_reason="stop")], usage=None)


def test_stream_error_without_a_status_is_retryable_unless_it_is_the_clients_fault(engine):
    assert engine._retryable_model_error(engine.ProviderStreamError("server hiccup"))
    assert not engine._retryable_model_error(engine.ProviderStreamError("bad request", 400))
    assert engine._retryable_model_error(engine.ProviderStreamError("overloaded", 503))


def test_each_repeated_stall_changes_the_request(engine, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [])
    monkeypatch.setattr(engine, "_interruptible_sleep", lambda *a, **k: None)
    sent = []
    outcomes = [engine.ProviderStreamError("idle timeout", 504)] * 3 + [_ok()]

    def fake_create(client, messages, tools, **kw):
        sent.append((kw.get("thinking"), kw.get("max_tokens"), kw.get("on_token") is not None))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(engine, "create_completion", fake_create)
    engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=.1, system_prompt="s",
        on_token=lambda *a: None, interrupt_check=lambda: False, trace=None, turn=1,
        max_tokens=8192, client_override=N(), provider_override="p", model_override="m")
    assert sent[0] == (None, 8192, True)
    assert sent[1] == ("length_retry", 8192, True)      # 1st change: thinking down
    assert sent[2][1] == 4096                            # 2nd change: lower output limit
    assert sent[3][2] is False                           # 3rd change: no streaming


def test_only_the_named_extra_field_is_identified(engine):
    body = {"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False},
            "provider": {"order": ["x"]}}
    err = RuntimeError("400: Unknown parameter: reasoning_effort")
    assert engine._named_extra_keys(err, body) == {"reasoning_effort"}
    err = RuntimeError("unsupported field enable_thinking")
    assert engine._named_extra_keys(err, body) == {"chat_template_kwargs"}
    assert engine._named_extra_keys(RuntimeError("400 bad"), body) == set()


def test_rejected_field_is_dropped_but_routing_survives(engine, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(engine, "_provider_extra_body",
                        lambda name: {"reasoning_effort": "low", "provider": {"order": ["x"]}})
    bodies = []

    class Completions:
        def create(self, **kw):
            bodies.append(kw.get("extra_body"))
            if len(bodies) == 1:
                err = RuntimeError("Unknown parameter: reasoning_effort")
                err.status_code = 400
                raise err
            return _ok()

    client = N(chat=N(completions=Completions()))
    engine._create_completion(client, [{"role": "user", "content": "hi"}], [], model_name="m",
                              provider_name="p")
    assert bodies[0] == {"reasoning_effort": "low", "provider": {"order": ["x"]}}
    assert bodies[1] == {"provider": {"order": ["x"]}}


# --- audit issue 49: a real idle timer -----------------------------------------------------------

def test_silent_stream_becomes_a_retryable_error(engine):
    import threading
    release = threading.Event()

    class Stalled:
        closed = False

        def __iter__(self):
            yield "first"
            release.wait(5)          # silence until the guard closes us
            raise OSError("closed")

        def close(self):
            self.closed = True
            release.set()

    stream = Stalled()
    guard = engine._IdleGuard(stream, 0.2)
    got = []
    with pytest.raises(engine.ProviderStreamError) as caught:
        for chunk in guard:
            got.append(chunk)
    assert got == ["first"] and stream.closed
    assert caught.value.status_code == 504 and engine._retryable_model_error(caught.value)


def test_healthy_stream_passes_through_untouched(engine):
    guard = engine._IdleGuard(iter([1, 2, 3]), 5)
    assert list(guard) == [1, 2, 3] and not guard.fired
