def _response(content="done"):
    message = type("Message", (), {"content": content, "tool_calls": None})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    return type("Response", (), {"choices": [choice]})()


def test_compact_messages_preserves_recent_messages_and_active_model(monkeypatch, engine):
    messages = [{"role": "user", "content": f"message {index}"} for index in range(8)]
    recent = list(messages[-3:])
    selected_client = object()
    seen = {}

    def fake_completion(client, request, tools, **kwargs):
        seen.update(client=client, request=request, tools=tools, kwargs=kwargs)
        return _response("summary")

    monkeypatch.setattr(engine, "create_completion", fake_completion)

    assert engine.compact_messages(
        messages, keep=3, completion_client=selected_client,
        provider_name="provider-x", model_name="model-y",
    )
    assert messages == [
        {"role": "system", "content": "Conversation summary:\nsummary"},
        *recent,
    ]
    assert seen["client"] is selected_client
    assert seen["kwargs"]["provider_name"] == "provider-x"
    assert seen["kwargs"]["model_name"] == "model-y"


def test_manual_compaction_resolves_auto_to_a_concrete_model(monkeypatch, engine):
    messages = [{"role": "user", "content": f"message {index}"} for index in range(8)]
    seen = {}
    selected_client = object()
    monkeypatch.setattr(engine, "MODEL_NAME", "auto:fast")
    monkeypatch.setattr(engine, "_auto_chain", [("provider-x", "model-y")])
    monkeypatch.setattr(engine, "_last_auto_rung", None)
    monkeypatch.setattr(engine, "get_client", lambda provider: (selected_client, "model-y"))
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *args: (32768, 8192))

    def fake_completion(client, request, tools, **kwargs):
        seen.update(client=client, kwargs=kwargs)
        return _response("summary")

    monkeypatch.setattr(engine, "create_completion", fake_completion)

    assert engine.compact_messages(messages, keep=3)
    assert seen["client"] is selected_client
    assert seen["kwargs"]["provider_name"] == "provider-x"
    assert seen["kwargs"]["model_name"] == "model-y"
    assert seen["kwargs"]["model_name"] != "auto:fast"


def test_agent_auto_compacts_before_the_next_completion(monkeypatch, engine):
    messages = [{"role": "user", "content": "x"} for _ in range(8)]
    selected_client = object()
    compact_calls = []
    completion_snapshots = []

    monkeypatch.setattr(engine, "COMPACTION_THRESHOLD_PCT", 75)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *args: (100, 20))
    monkeypatch.setattr(engine, "_estimate_context_chars", lambda *args: 400)

    def fake_compact(active_messages, **kwargs):
        compact_calls.append(kwargs)
        active_messages[:] = [{"role": "system", "content": "compacted"}, *active_messages[-6:]]
        return True

    def fake_completion(active_messages, tools, **kwargs):
        completion_snapshots.append(list(active_messages))
        return _response()

    monkeypatch.setattr(engine, "compact_messages", fake_compact)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_completion)

    result = engine.run_agent(
        messages, max_turns=1, client=selected_client,
        provider_name="provider-x", model_name="model-y",
    )

    assert result == "done"
    assert len(compact_calls) == 1
    # 0.2.3: the turn's budget is passed so the summary call's tokens are
    # counted with the turn (BUG_REPORT.md #11).
    budget = compact_calls[0].pop("budget")
    assert isinstance(budget, engine._TurnBudget)
    assert compact_calls[0] == {
        "completion_client": selected_client,
        "provider_name": "provider-x",
        "model_name": "model-y",
    }
    assert completion_snapshots[0][0]["content"] == "compacted"


def test_auto_compaction_failure_does_not_abort_the_turn(monkeypatch, engine):
    messages = [{"role": "user", "content": "x"} for _ in range(8)]
    monkeypatch.setattr(engine, "COMPACTION_THRESHOLD_PCT", 75)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *args: (100, 20))
    monkeypatch.setattr(engine, "_estimate_context_chars", lambda *args: 400)
    monkeypatch.setattr(engine, "compact_messages", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(engine, "_create_completion_with_fallback", lambda *args, **kwargs: _response())

    assert engine.run_agent(messages, max_turns=1) == "done"


def _tool_result(text):
    return {"role": "user", "content": f"Tool result (execute_shell):\n{text}"}


def test_single_task_instruction_survives_compaction(monkeypatch, engine):
    """A one-shot run has exactly one human turn: the task. Summarising it away
    leaves the agent working from a paraphrase of its own specification."""
    instruction = {"role": "user", "content": "Write /app/out.txt with exactly: 42\n(no newline)"}
    messages = [instruction]
    for i in range(10):
        messages += [{"role": "assistant", "content": f"step {i}"}, _tool_result(f"output {i}")]
    monkeypatch.setattr(engine, "create_completion", lambda *a, **k: _response("summary"))

    assert engine.compact_messages(messages, keep=4)
    assert messages[0] == {"role": "system", "content": "Conversation summary:\nsummary"}
    assert messages[1] == instruction
    assert messages.count(instruction) == 1
    assert len(messages) == 2 + 4


def test_summary_call_gets_room_to_finish(monkeypatch, engine):
    """The summary call used create_completion's 2,000-token default, which a
    reasoning model can spend entirely on thinking, returning no summary."""
    seen = {}

    def fake_completion(client, request, tools, **kwargs):
        seen.update(kwargs)
        return _response("summary")

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a: (32768, 8192))
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    assert engine.compact_messages(messages, keep=3)
    assert seen["max_tokens"] > 2000
    assert seen["max_tokens"] <= 8192
