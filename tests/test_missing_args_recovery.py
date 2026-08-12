"""A call whose arguments never arrived is corrected, not returned as a result.

Observed: asking a plain factual question produced eight identical failures in
one turn —

    Searching the web...
    Error: web_search requires 'query'.      (x8)

No search was ever issued, so no backend was contacted and the fallback chain
never came into it. The error went back as a tool result and the model re-sent
the same argument-less call. In a sub-run the same text travelled onward as
evidence that the step had failed.

Every shape of the refusal counts: the generic "was called with no arguments"
and the per-tool "requires 'query'" / "'url'" / "'code'".
"""
from tests.conftest import ScriptedModel

NO_QUERY = "✿FUNCTION✿: web_search ✿ARGS✿: {}"
GOOD = '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "when was ronaldo nazario born"}'


def test_every_missing_argument_shape_is_recognised(engine):
    for message in (
        "Error: 'execute_shell' was called with no arguments. Send an ✿ARGS✿ block",
        "Error: web_search requires 'query'.",
        "Error: browser tool requires 'url'.",
        "Error: sandboxed execution requires 'code'. Pass the Python source",
    ):
        assert engine._is_missing_argument_error(message), message


def test_an_ordinary_failure_is_not_mistaken_for_one(engine):
    """Only the arguments-never-arrived case; a real failure stays a result."""
    for message in ("Error: [Errno 2] No such file or directory: 'x.txt'",
                    "Error: Path not allowed: C:\\x",
                    "Wrote 12 bytes to x.txt", ""):
        assert not engine._is_missing_argument_error(message), message


def test_the_model_is_corrected_and_the_search_then_runs(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_exec_search", lambda *a, **k: "Ronaldo, born 1976",
                        raising=False)
    monkeypatch.setattr(engine, "run_tool",
                        lambda name, args, **k: ("Error: web_search requires 'query'."
                                                 if not args.get("query")
                                                 else "Ronaldo, born 1976"))
    monkeypatch.setattr(engine, "create_completion",
                        ScriptedModel([NO_QUERY, GOOD, "He was born in 1976."]))
    messages = [{"role": "user", "content": "when was ronaldo nazario born?"}]

    answer = engine.run_agent(messages, max_turns=6)

    assert answer == "He was born in 1976."
    correction = [m for m in messages
                  if m["role"] == "user" and "requires 'query'" in m.get("content", "")]
    assert correction, "the model must be told which argument was missing"


def test_it_does_not_loop_forever(engine, monkeypatch):
    """Bounded, like the unknown-tool recovery it mirrors."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "run_tool",
                        lambda *a, **k: "Error: web_search requires 'query'.")
    calls = {"n": 0}

    def _always_bare(*_a, **_k):
        calls["n"] += 1
        return type("R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": NO_QUERY}),
            "finish_reason": "stop",
        })()]})

    monkeypatch.setattr(engine, "create_completion", _always_bare)
    engine.run_agent([{"role": "user", "content": "search something"}], max_turns=10)

    assert calls["n"] <= 6, f"should stop correcting, made {calls['n']} model calls"


def test_a_well_formed_call_is_unaffected(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "run_tool", lambda *a, **k: "Ronaldo, born 1976")
    monkeypatch.setattr(engine, "create_completion",
                        ScriptedModel([GOOD, "He was born in 1976."]))

    assert engine.run_agent([{"role": "user", "content": "when?"}],
                            max_turns=4) == "He was born in 1976."
