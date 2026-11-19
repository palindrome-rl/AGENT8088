"""The status line must be painted before memory recall, not after.

Recall does a network round trip (an /embeddings call, plus a fresh HTTP client
per call) and it runs in run_agent *before* _run_agent_loop, where the first
spin("thinking...") lives. That left the terminal blank for the whole call --
and because turn_start is set earlier, the spinner then appeared already
reading several elapsed seconds.
"""


def _response(content="done"):
    message = type("Message", (), {"content": content, "tool_calls": None})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    return type("Response", (), {"choices": [choice]})()


def test_spinner_is_painted_before_memory_recall(monkeypatch, engine):
    """The order of events, not the timing: paint, then block."""
    events = []

    def slow_recall(messages, system_prompt, identity=None):
        events.append("recall")
        return system_prompt

    def spin(message):
        events.append(f"spin:{message}")

        class _Ctx:
            def __enter__(self): return None
            def __exit__(self, *exc): return False
        return _Ctx()

    monkeypatch.setattr(engine, "_recalled_memory_prompt", slow_recall)
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *args, **kwargs: _response())

    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1, spin=spin)

    assert "recall" in events, "recall did not run; the test is not exercising the path"
    assert events[0].startswith("spin:"), (
        f"recall blocked before anything was painted: {events}")


def test_spinner_paint_is_skipped_when_no_spin_hook(monkeypatch, engine):
    """The gateway, MCP server and cron pass no spin hook; they must not break."""
    monkeypatch.setattr(engine, "_recalled_memory_prompt",
                        lambda messages, system_prompt, identity=None: system_prompt)
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *args, **kwargs: _response())

    assert engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1) == "done"
