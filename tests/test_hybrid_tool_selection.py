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


def test_hybrid_selection_uses_keywords_without_embeddings(engine, monkeypatch):
    """No embedder must not mean the whole catalog: the feature exists to avoid that."""
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    chosen = engine.select_tool_names_for_request(
        "what local models can my machine run?", allowed, mode="hybrid")

    assert "check_local_hardware" in chosen
    assert "git_push" not in chosen
    assert len(chosen) < len(allowed) / 2
    assert engine._TOOL_SELECTION_PINNED & allowed <= chosen


def test_keyword_fallback_offers_only_the_pinned_tools_when_nothing_matches(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    chosen = engine.select_tool_names_for_request("hello", allowed, mode="hybrid")

    assert chosen == (engine._TOOL_SELECTION_PINNED | engine._TOOL_SELECTION_ALWAYS) & allowed


def test_rankers_with_no_shared_candidate_offer_the_union_not_the_catalog(engine, monkeypatch):
    class _Disagreeing(_Embedder):
        def embed_one(self, _query):
            return [0.0, 1.0]  # the "git" side, while the query's words are about local models

    monkeypatch.setattr(engine.memory, "embedder", lambda: _Disagreeing())
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    chosen = engine.select_tool_names_for_request(
        "what local models can my machine run?", allowed, mode="hybrid")

    assert "check_local_hardware" in chosen, "the keyword side"
    assert any(name.startswith("git_") for name in chosen), "the semantic side"
    assert len(chosen) < len(allowed)


def test_the_keyword_fallback_is_logged_once_per_reason(engine, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    monkeypatch.setattr(engine, "_TOOL_SELECTION_FALLBACK_LOGGED", set())
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    with caplog.at_level(logging.INFO, logger=engine._log.name):
        for _ in range(3):
            engine.select_tool_names_for_request("what local models can my machine run?",
                                                 allowed, mode="hybrid")

    notes = [r for r in caplog.records if "no embedder available" in r.getMessage()]
    assert len(notes) == 1


def test_full_mode_still_offers_the_whole_catalog(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS)

    assert engine.select_tool_names_for_request("hello", allowed, mode="full") == allowed


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

    # The guard must not give review_code exclusive control of a document task
    # or remove its ordinary tools: the pinned document and file tools stay.
    assert {"convert_document", "create_document", "read_text"} <= chosen
    assert "git_diff" in chosen, "the guard only acts on code-review wording"


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


def test_a_rambling_novice_still_reaches_the_reviewer(engine):
    """Proximity is the first thing a real user's phrasing loses.

    "i have some code in <120 characters of Windows path> ... my colleague
    said i should get it checked" states both halves of a review request and
    matched none of the proximity windows, so a live first-contact run
    hand-read eleven files and burned a turn on the unavailable shell instead
    of calling the reviewer once.
    """
    allowed = set(engine.TOOL_SPECS)
    rambling = ("ok so um i just installed this thing and im not really sure what it "
                "does. i have some code in C:/Users/x/AppData/Local/Temp/scratch/shopapi "
                "that i was working on yesterday, something about coupons i think? my "
                "colleague said i should get it checked before friday")

    guarded = engine._apply_tool_intent_guards(rambling, {"git_diff"}, allowed)

    assert "review_code" in guarded
    assert "git_diff" not in guarded, "the working tree is not the branch"


def test_the_guard_leaves_diff_and_document_requests_alone(engine):
    """Widening it for ramblers must not make it a net that catches everything.

    git_diff has its own job and document work is a separate subsystem that
    merely shares the word "review".
    """
    allowed = set(engine.TOOL_SPECS)
    untouched = [
        "show me the diff",
        "git diff please",
        "what have I changed so far",
        "extract the text from this scanned invoice",
        "review this attached PDF contract for me",
        "read agreement.pdf and summarise the payment terms",
        "write me a python script that sorts a list",
    ]
    for query in untouched:
        guarded = engine._apply_tool_intent_guards(query, {"git_diff"}, allowed)
        assert "git_diff" in guarded, query
