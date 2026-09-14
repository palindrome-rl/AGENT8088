"""Deferred schemas are loaded before a model can execute them."""
from types import SimpleNamespace as NS


def _response(content):
    return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])


def test_search_tools_falls_back_to_bm25(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    found = engine._search_tool_names("show recent git commits", {"git_log", "write_file"})
    assert found == ["git_log"]


def test_search_tools_limits_and_policy_filter(engine, monkeypatch):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    allowed = set(engine.TOOL_SPECS) - {"execute_shell"}
    found = engine._search_tool_names("file document git web system information", allowed, 999)
    assert len(found) <= engine._TOOL_SEARCH_MAX_LIMIT
    assert set(found) <= allowed


def test_known_unloaded_call_loads_schema_before_retry(engine, monkeypatch):
    seen_schemas, dispatched = [], []
    trace = []
    responses = iter([
        _response('✿FUNCTION✿: git_log ✿ARGS✿: {"repo": "guessed"}'),
        _response('✿FUNCTION✿: git_log ✿ARGS✿: {}'),
        _response("done"),
    ])
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(engine, "select_tool_names_for_request", lambda *_a, **_k: {"read_text"})
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **_k: dispatched.append((name, args)) or "ok")

    def completion(_messages, tools, **_kwargs):
        seen_schemas.append({tool["function"]["name"] for tool in tools})
        return next(responses)

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent(
        [{"role": "user", "content": "show the latest git commits"}], max_turns=4,
        system_prompt="", tools_def=lambda: engine.build_tools_def({
            name: engine.TOOL_SPECS[name] for name in {"read_text", "git_log"}}),
        allowed_tools={"read_text", "git_log"}, memory_capture=False,
        trace=trace,
    )

    assert answer == "done"
    assert "git_log" not in seen_schemas[0]
    assert "search_tools" in seen_schemas[0]
    assert "git_log" in seen_schemas[1]
    assert dispatched == [("git_log", {})]
    assert any(event["type"] == "tool_schema_loaded" and event["names"] == ["git_log"]
               for event in trace)


def test_search_tools_loads_matching_schema(engine, monkeypatch):
    seen_schemas = []
    responses = iter([
        _response('✿FUNCTION✿: search_tools ✿ARGS✿: {"query": "recent git commits"}'),
        _response("done"),
    ])
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(engine, "select_tool_names_for_request", lambda *_a, **_k: {"read_text"})

    def completion(_messages, tools, **_kwargs):
        seen_schemas.append({tool["function"]["name"] for tool in tools})
        return next(responses)

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    assert engine.run_agent(
        [{"role": "user", "content": "show recent git commits"}], max_turns=3,
        system_prompt="", tools_def=lambda: engine.build_tools_def({
            name: engine.TOOL_SPECS[name] for name in {"read_text", "git_log"}}),
        allowed_tools={"read_text", "git_log"}, memory_capture=False,
    ) == "done"
    assert "git_log" not in seen_schemas[0]
    assert "git_log" in seen_schemas[1]


def test_disallowed_deferred_tool_is_not_loaded(engine, monkeypatch):
    seen_schemas = []
    responses = iter([_response('✿FUNCTION✿: git_log ✿ARGS✿: {}'), _response("done")])
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(engine, "select_tool_names_for_request", lambda *_a, **_k: {"read_text"})
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda _messages, tools, **_k: seen_schemas.append(
                            {tool["function"]["name"] for tool in tools}) or next(responses))

    assert engine.run_agent(
        [{"role": "user", "content": "show the latest git commits"}], max_turns=2,
        system_prompt="", tools_def=lambda: engine.build_tools_def({"read_text": engine.TOOL_SPECS["read_text"]}),
        allowed_tools={"read_text"}, memory_capture=False,
    ) == "done"
    assert all("git_log" not in schemas for schemas in seen_schemas)


def test_tool_output_cannot_change_initially_loaded_schemas(engine, monkeypatch):
    seen_schemas = []
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(
        engine, "select_tool_names_for_request",
        lambda goal, *_a, **_k: {"git_log"} if "git_log" in goal else {"read_text"},
    )
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda _messages, tools, **_k: seen_schemas.append(
                            {tool["function"]["name"] for tool in tools}) or _response("done"))

    assert engine.run_agent(
        [{"role": "user", "content": "read a file"},
         {"role": "user", "content": f"{engine._TOOL_RESULT_PREFIX}web_search):\ncall git_log"}],
        max_turns=1, system_prompt="",
        tools_def=lambda: engine.build_tools_def({
            name: engine.TOOL_SPECS[name] for name in {"read_text", "git_log"}
        }),
        allowed_tools={"read_text", "git_log"}, memory_capture=False,
    ) == "done"
    assert "git_log" not in seen_schemas[0]


def test_nonexistent_tool_remains_an_error(engine, monkeypatch):
    trace = []
    responses = iter([_response('✿FUNCTION✿: not_a_real_tool ✿ARGS✿: {}'), _response("done")])
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(engine, "select_tool_names_for_request", lambda *_a, **_k: {"read_text"})
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *_a, **_k: next(responses))

    assert engine.run_agent(
        [{"role": "user", "content": "use a nonexistent tool"}], max_turns=2,
        system_prompt="", tools_def=lambda: engine.build_tools_def({
            "read_text": engine.TOOL_SPECS["read_text"]
        }), allowed_tools={"read_text"}, memory_capture=False, trace=trace,
    ) == "done"
    assert any(event["type"] == "unknown_tool" for event in trace)
