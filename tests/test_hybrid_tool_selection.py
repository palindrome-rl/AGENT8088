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


def test_novice_branch_review_does_not_expose_worktree_diff(engine, monkeypatch):
    """The two tools inspect different scopes; offering both caused a live
    model to review the branch and then contaminate that result with git diff.
    The guard must also work when embeddings fail and selection fails open.
    """
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)
    chosen = engine.select_tool_names_for_request(
        "I barely know git. Please look over this branch and tell me if it is safe to ship.",
        allowed, mode="hybrid")

    assert "review_code" in chosen
    assert "git_diff" not in chosen


def test_plain_diff_request_still_exposes_git_diff(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    chosen = engine.select_tool_names_for_request(
        "Show me my current uncommitted git diff.", allowed, mode="hybrid")

    assert "git_diff" in chosen


def test_document_review_does_not_get_misrouted_to_code_review(engine, monkeypatch):
    """"Review" alone also describes contracts, PDFs and uploaded reports."""
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    chosen = engine.select_tool_names_for_request(
        "I am new here. Please review this attached PDF contract and explain it simply.",
        allowed, mode="hybrid")

    # Selection fails open here, so the guard must not give review_code
    # exclusive control of a document task or remove its ordinary tools.
    assert chosen == allowed
    assert "document_read" in chosen
    assert "git_diff" in chosen


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


def test_tool_selection_setting_updates_runtime_and_config(engine, monkeypatch, tmp_path):
    config_path = tmp_path / "config.txt"
    monkeypatch.setattr(engine, "CONFIG_PATH", config_path)
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    assert engine.set_tool_selection("full") == "full"
    assert engine.TOOL_SELECTION == "full"
    assert config_path.read_text() == "tool_selection=full\n"


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
    assert captured == ["check_local_hardware", "search_tools"]
