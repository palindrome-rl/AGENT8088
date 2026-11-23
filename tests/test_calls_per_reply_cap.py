"""One reply runs at most MAX_CALLS_PER_REPLY tool calls.

Seen in a benchmark run: the model's first reply held 483 tool calls (describe_capabilities, view_skill and
describe_tool, over and over). All 483 ran, the conversation grew past 300,000 tokens, compaction could not bring it
down, and the run ended on a model error before any work was done.
"""
from tests.test_completion_pipeline import Script, _call, eng, failing_then_ok  # noqa: F401


def _many(n: int) -> str:
    return "\n".join(_call("execute_shell", command=f"echo {i}") for i in range(n))


def test_a_reply_with_too_many_calls_runs_only_the_first_ones(eng, monkeypatch):
    ran = []
    base = failing_then_ok(["ok"])

    def tool(name, arguments, depth=0):
        ran.append(arguments)
        return base(name, arguments, depth)

    script = Script(eng, monkeypatch, [_many(eng.MAX_CALLS_PER_REPLY + 40), "Done."], tool=tool)
    trace = []
    eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=4, tools_def=[],
                  allowed_tools={"execute_shell"}, trace=trace)
    assert len(ran) == eng.MAX_CALLS_PER_REPLY
    assert '"echo 0"' in ran[0] and f'"echo {eng.MAX_CALLS_PER_REPLY - 1}"' in ran[-1]
    told = script.nudges("were not run")
    assert len(told) == 1 and "40" in told[0]
    dropped = [s for s in trace if s.get("type") == "calls_dropped"]
    assert dropped and dropped[0]["count"] == 40


def test_a_reply_within_the_cap_runs_every_call(eng, monkeypatch):
    ran = []
    base = failing_then_ok(["ok"])

    def tool(name, arguments, depth=0):
        ran.append(arguments)
        return base(name, arguments, depth)

    script = Script(eng, monkeypatch, [_many(5), "Done."], tool=tool)
    eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=4, tools_def=[],
                  allowed_tools={"execute_shell"})
    assert len(ran) == 5
    assert not script.nudges("were not run")


def test_the_cap_leaves_room_for_a_real_batch(engine):
    # An approved plan's batch of reads and writes is routinely a dozen calls; the cap is well above that.
    assert engine.MAX_CALLS_PER_REPLY >= 20
