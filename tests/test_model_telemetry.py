"""Local, metadata-only model-call telemetry."""
import json
import os

import pytest


def _response(content="ok", *, prompt_tokens=7, completion_tokens=3):
    usage = type("Usage", (), {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    })()
    return type("Response", (), {
        "usage": usage,
        "choices": [type("Choice", (), {
            "message": type("Message", (), {"content": content})(),
            "finish_reason": "stop",
        })()],
    })()


def _client(result=None, error=None):
    class Completions:
        @staticmethod
        def create(**_kwargs):
            if error:
                raise error
            return result

    return type("Client", (), {
        "chat": type("Chat", (), {"completions": Completions()})(),
    })()


def _entries(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_disabled_model_telemetry_writes_nothing(engine, monkeypatch, tmp_path):
    path = tmp_path / "model-telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", path)

    engine.create_completion(_client(_response()), [{"role": "user", "content": "private"}], [])

    assert not path.exists()


def test_model_telemetry_is_private_metadata_only(engine, monkeypatch, tmp_path):
    path = tmp_path / "model-telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", path)
    monkeypatch.setattr(engine, "COST_PER_1K_INPUT", 1.0)
    monkeypatch.setattr(engine, "COST_PER_1K_OUTPUT", 2.0)

    engine.create_completion(
        _client(_response("assistant private content")),
        [{"role": "user", "content": "user private content"}], [],
        model_name="safe-model", provider_name="safe-provider",
    )

    [entry] = _entries(path)
    assert entry == {
        "ts": entry["ts"], "event": "model_call", "provider": "safe-provider",
        "model": "safe-model", "attempt": "direct", "outcome": "success",
        "latency_ms": entry["latency_ms"], "max_tokens": 2000,
        "token_source": "provider", "input_tokens": 7, "output_tokens": 3,
        "cost_usd": 0.013, "finish_reason": "stop", "error_type": None,
        "error_status": None,
    }
    assert "private content" not in path.read_text(encoding="utf-8")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_model_telemetry_records_sanitized_failures(engine, monkeypatch, tmp_path):
    class ProviderError(RuntimeError):
        status_code = 503

    path = tmp_path / "model-telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", path)

    with pytest.raises(ProviderError):
        engine.create_completion(
            _client(error=ProviderError("secret response body")), [], [],
            model_name="safe-model", provider_name="safe-provider",
        )

    [entry] = _entries(path)
    assert entry["outcome"] == "error"
    assert entry["error_type"] == "ProviderError"
    assert entry["error_status"] == 503
    assert "secret response body" not in path.read_text(encoding="utf-8")


def test_model_telemetry_estimates_streamed_output_tokens(engine, monkeypatch, tmp_path):
    path = tmp_path / "model-telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", path)
    delta = type("Delta", (), {"content": "streamed text", "reasoning_content": None,
                                "tool_calls": []})()
    chunk = type("Chunk", (), {"choices": [type("Choice", (), {"delta": delta})()]})()

    class Completions:
        @staticmethod
        def create(**kwargs):
            assert kwargs["stream"] is True
            return iter([chunk])

    client = type("Client", (), {
        "chat": type("Chat", (), {"completions": Completions()})(),
    })()
    response = engine.create_completion(client, [], [], on_token=lambda *_: None)

    assert response.choices[0].message.content == "streamed text"
    [entry] = _entries(path)
    assert entry["token_source"] == "output_estimate"
    assert entry["output_tokens"] == len("streamed text") // 4


def test_model_telemetry_marks_primary_and_fallback_attempts(engine, monkeypatch, tmp_path):
    class RetryableError(RuntimeError):
        status_code = 503

    path = tmp_path / "model-telemetry.jsonl"
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", path)
    monkeypatch.setattr(engine, "client", _client(error=RetryableError("retry")))
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "primary")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "primary")
    monkeypatch.setattr(engine, "MODEL_NAME", "primary-model")
    monkeypatch.setattr(engine, "APP_CONFIG", {"fallback_models": "fallback:fallback-model"})
    monkeypatch.setattr(engine, "PROVIDERS", {"fallback": {"model": "fallback-model"}})
    monkeypatch.setattr(engine, "get_client", lambda _name: (_client(_response()), "fallback-model"))

    response = engine._create_completion_with_fallback(
        [{"role": "user", "content": "hello"}], [], temperature=0.1,
        system_prompt="system", on_token=None, interrupt_check=None, trace=[], turn=1,
    )

    assert response.choices[0].message.content == "ok"
    entries = _entries(path)
    assert [(entry["attempt"], entry["outcome"]) for entry in entries] == [
        ("primary", "error"), ("fallback", "success"),
    ]
