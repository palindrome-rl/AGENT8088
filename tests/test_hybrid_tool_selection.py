"""Hybrid tool selection stays before the model call and fails open."""
import json

from types import SimpleNamespace as NS


class _Embedder:
    def embed(self, texts):
        return [[1.0, 0.0] if "local" in text.lower() or "hardware" in text.lower()
                else [0.0, 1.0] if "git" in text.lower() else [0.0, 0.0]
                for text in texts]

    def embed_one(self, _query):
        return [1.0, 0.0]


def test_hybrid_selection_puts_local_hardware_in_direct_schemas(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: _Embedder())
    engine._TOOL_INDEX_CACHE.clear()
    chosen = engine.select_tool_names_for_request(
        "what local models can my machine run?", set(engine.TOOL_SPECS), mode="hybrid")
    assert "check_local_hardware" in chosen
    assert "git_push" not in chosen
    assert "describe_tool" in chosen
    full = len(json.dumps(engine.build_tools_def(engine.TOOL_SPECS)))
    compact = len(json.dumps(engine.build_tools_def(
        {name: engine.TOOL_SPECS[name] for name in chosen})))
    assert compact < full / 2


def test_hybrid_selection_fails_open_without_embeddings(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)
    assert engine.select_tool_names_for_request(
        "what local models can my machine run?", allowed, mode="hybrid") == allowed


def test_auto_mode_only_enables_measured_models(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: _Embedder())
    monkeypatch.setattr(engine, "TOOL_SELECTION_MODELS",
                        frozenset({"custom:ornith-1.0-35b"}))
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)
    selected = engine.select_tool_names_for_request(
        "what local models can my machine run?", allowed, mode="auto",
        provider="custom", model="ornith-1.0-35b")
    assert "check_local_hardware" in selected
    assert len(selected) < len(allowed)
    assert engine.select_tool_names_for_request(
        "what local models can my machine run?", allowed, mode="auto",
        provider="custom", model="unknown") == allowed


def test_run_agent_filters_native_schemas_before_first_completion(engine, monkeypatch):
    specs = {
        "check_local_hardware": engine.TOOL_SPECS["check_local_hardware"],
        "git_push": engine.TOOL_SPECS["git_push"],
    }
    captured = []

    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(engine, "select_tool_names_for_request",
                        lambda *_args, **_kwargs: {"check_local_hardware"})

    def completion(_messages, tools, **_kwargs):
        captured.extend(tool["function"]["name"] for tool in tools)
        return NS(choices=[NS(message=NS(content="done"), finish_reason="stop")])

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    assert engine.run_agent(
        [{"role": "user", "content": "what local models can my machine run?"}],
        max_turns=1, system_prompt="", tools_def=lambda: engine.build_tools_def(specs),
        allowed_tools=set(specs), memory_capture=False) == "done"
    assert captured == ["check_local_hardware"]
