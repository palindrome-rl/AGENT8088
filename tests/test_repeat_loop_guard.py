"""A model stuck re-emitting one identical non-read call must still be stopped.

Item 3 of the Tier 1 list is "deduplicate identical tool calls" — but the
narrowing to read-only caching removed the guard that also served as the
loop breaker. Serving a repeat from cache left `executed` False, which is
what trips the stall detector; re-running the tool sets `executed` True and
the detector never fires.
"""
import json


def _tool_call_response(name, args):
    call = type("T", (), {
        "function": type("F", (), {"name": name, "arguments": json.dumps(args)})(),
    })()
    message = type("M", (), {"content": "", "tool_calls": [call]})()
    choice = type("C", (), {"message": message, "finish_reason": "tool_calls"})()
    return type("R", (), {"choices": [choice]})()


def test_a_repeated_identical_shell_call_is_stopped_not_rerun(monkeypatch, engine):
    """The model asks for the same command forever; the harness must break out."""
    runs = []

    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **k: _tool_call_response("execute_shell", {"command": "echo hi"}))

    def fake_run_tool(name, args, **kwargs):
        runs.append((name, tuple(sorted(args.items()))))
        return "hi"

    monkeypatch.setattr(engine, "run_tool", fake_run_tool)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    engine.run_agent([{"role": "user", "content": "run echo hi"}], max_turns=8)

    # The stall detector nudges once, then stops. Anything close to max_turns
    # means the same command was executed over and over.
    assert len(runs) <= 3, (
        f"identical call executed {len(runs)} times before the loop broke out")


def test_an_opaque_cli_repeat_reruns_but_still_does_not_count_as_progress(monkeypatch, engine):
    """Re-running observes external state; it must not also buy another round.

    Two rules that pull opposite ways: an opaque external CLI can legitimately
    return something new for identical arguments, so its repeat re-runs; but a
    repeat is never progress, or the stall detector never fires and the model
    polls until max_turns.
    """
    runs = []
    args = {"name": "gimp", "arguments": ["part", "info", "0"], "cwd": "."}

    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **k: _tool_call_response("cli_anything_run", args))
    monkeypatch.setattr(engine, "exec_tool",
                        lambda name, raw, **k: runs.append(name) or "state")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    engine.run_agent([{"role": "user", "content": "poll it"}], max_turns=8,
                     allowed_tools={"cli_anything_run"}, tools_def=[])

    assert runs, "the opaque CLI never ran; it must re-run, not be served from cache"
    assert len(runs) <= 3, f"repeat counted as progress; polled {len(runs)} times"
