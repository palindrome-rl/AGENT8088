"""After a finishing nudge fires, read-only check rounds are capped: the model is
told to answer after max_post_check_rounds, and the best answer so far is
returned two rounds later. A write resets the count; 0 disables the cap."""
import json

import pytest


@pytest.fixture(autouse=True)
def _base_cap(engine, monkeypatch):
    """These tests are about the base mechanics, where every quiet turn counts. The new-evidence hold has its own tests."""
    monkeypatch.setattr(engine, "POST_CHECK_NEW_EVIDENCE", False)


def _response(content):
    message = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message,
                                                     "finish_reason": "stop"})()]})()


def _calc(i):
    return f'✿FUNCTION✿: calculate ✿ARGS✿: {{"expression": "{i}+1"}}'


def _run_tests():
    return '✿FUNCTION✿: run_tests ✿ARGS✿: {}'


def _run(engine, monkeypatch, script, *, mutate_at=(), cap=5):
    """script(call_index, messages) -> reply text. Round i of calculate
    mutates state when i is in mutate_at."""
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", cap)
    calls = []

    def fake_model(messages, tools, **kwargs):
        calls.append([m.get("content") for m in messages])
        return _response(script(len(calls) - 1, messages))

    def fake_exec(name, arguments, depth=0):
        if any(f'"{i}+1"' in arguments for i in mutate_at):
            engine.note_mutation("test write")
        return "2"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", fake_exec)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "compute"}], max_turns=40,
                              trace=trace, dynamic_turns=False)
    return answer, calls, [t["type"] for t in trace]


def _keeps_checking(i, _messages):
    # call 0: a tool; call 1: a final-looking reply (deliverables nudge fires);
    # every later call: another read-only check, forever.
    return "done" if i == 1 else _calc(i)


def _wrap_up_asked(messages):
    return "no more tools will run" in str(messages[-1].get("content"))


def test_read_only_checks_hit_the_backstop_with_a_fresh_answer(engine, monkeypatch):
    def script(i, messages):
        return "fresh summary" if _wrap_up_asked(messages) else _keeps_checking(i, messages)

    answer, calls, kinds = _run(engine, monkeypatch, script)
    assert kinds.count("post_check_cap") == 1
    assert "post_check_backstop" in kinds
    # The backstop asks once more with no tools, so the answer reflects the
    # checks just run rather than the reply from before them.
    assert answer.startswith("fresh summary")
    # 1 tool + 1 reply + (5 + 2) check rounds + 1 wrap-up
    assert len(calls) == 10


def test_backstop_falls_back_to_the_last_reply_when_the_wrap_up_fails(engine, monkeypatch):
    answer, _calls, kinds = _run(engine, monkeypatch, _keeps_checking)
    # The wrap-up reply here is another tool call: no usable text, so the
    # last text reply is used.
    assert "post_check_backstop" in kinds
    assert answer.startswith("done")


def test_model_that_obeys_the_cap_nudge_answers_without_more_gates(engine, monkeypatch):
    def script(i, messages):
        if any(engine.POST_CHECK_CAP_NUDGE in str(m.get("content")) for m in messages):
            return "final answer"
        return _keeps_checking(i, messages)

    answer, _calls, kinds = _run(engine, monkeypatch, script)
    assert answer.startswith("final answer")
    assert "post_check_cap" in kinds and "post_check_backstop" not in kinds


def test_a_write_after_the_nudge_resets_the_count(engine, monkeypatch):
    # check round 3 (call index 4) writes; the cap then needs 5 more clean rounds.
    _answer, calls, kinds = _run(engine, monkeypatch, _keeps_checking, mutate_at=(4,))
    assert "post_check_backstop" in kinds
    assert len(calls) == 10 + 3


def test_backstop_keeps_the_unverified_change_note(engine, monkeypatch):
    # A write after the nudge leaves verification pending; the backstop must
    # still say so, as a normal finish would.
    def script(i, messages):
        return "fresh summary" if _wrap_up_asked(messages) else _keeps_checking(i, messages)

    answer, _calls, kinds = _run(engine, monkeypatch, script, mutate_at=(4,))
    assert "post_check_backstop" in kinds
    assert answer.startswith("fresh summary")
    assert "Verification note" in answer


def test_zero_disables_the_cap(engine, monkeypatch):
    def script(i, messages):
        return "final" if i >= 12 else _keeps_checking(i, messages)

    answer, _calls, kinds = _run(engine, monkeypatch, script, cap=0)
    assert answer.startswith("final")
    assert "post_check_cap" not in kinds and "post_check_backstop" not in kinds


_GIT_ARGV = {"git_push": ["git", "push", "origin", "HEAD"], "git_create_pr": ["gh", "pr", "create"],
             "git_remote": ["git", "remote", "-v"]}


def _git(engine, monkeypatch, result):
    monkeypatch.setattr(engine, "_structured_tool_argv",
                        lambda name, args: _GIT_ARGV.get(name, ["git", name.split("_", 1)[1]]))
    monkeypatch.setattr(engine, "_exec_structured_tool", lambda name, args, timeout: result)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")


def test_structured_git_writes_count_as_state_changes(engine, monkeypatch):
    _git(engine, monkeypatch, "[main 1a2b3c4] fix\n 1 file changed")
    before = engine._MUTATION_SEQ
    engine.run_tool("git_status", {})
    assert engine._MUTATION_SEQ == before
    engine.run_tool("git_commit", {"message": "fix"})
    assert engine._MUTATION_SEQ > before


@pytest.mark.parametrize("name,args", [
    ("git_remote", {"action": "list"}), ("git_push", {}), ("git_create_pr", {"title": "t"}),
    ("git_log", {}), ("git_diff", {}),
])
def test_git_tools_that_leave_the_tree_alone_are_not_changes(engine, monkeypatch, name, args):
    _git(engine, monkeypatch, "ok")
    before = engine._MUTATION_SEQ
    engine.run_tool(name, args)
    assert engine._MUTATION_SEQ == before


@pytest.mark.parametrize("result", [
    "On branch main\nnothing to commit, working tree clean",
    "Error: git commit exited with status 1",
])
def test_a_failed_git_write_is_not_a_change(engine, monkeypatch, result):
    _git(engine, monkeypatch, result)
    before = engine._MUTATION_SEQ
    engine.run_tool("git_commit", {"message": "fix"})
    assert engine._MUTATION_SEQ == before


# --- shell checks are checks; shell writes are changes ------------------------

@pytest.mark.parametrize("command", [
    "pytest -q", "python -m pytest tests/", "python3 verify.py", "node check.js",
    "curl -s http://localhost:8000/health", "curl -sf localhost:8080/api | jq .",
    "cd /app && python3 test_outputs.py", "test -f /app/out.txt && echo ok",
    "ls -la /app && cat /app/out.txt", "python3 -c 'print(open(\"out.txt\").read())'",
    "go test ./...", "cargo test", "npm test", "make test", "diff a.txt b.txt",
    "sha256sum out.bin", "ps aux | grep server", "timeout 10 pytest -x", "FOO=1 python3 run_checks.py",
    "python3 verify.py 2>&1 | tail -20", "pytest -q >/dev/null 2>&1; echo $?",
])
def test_checks_are_not_state_changes(engine, command):
    assert not engine._shell_may_change(command)


@pytest.mark.parametrize("command", [
    "echo hi > out.txt", "cat a >> b", "sed -i s/a/b/ f.py", "rm -f out.txt", "mv a b",
    "cp a b", "mkdir -p out", "touch f", "pip install requests", "npm install",
    "git commit -am fix", "python3 -c 'open(\"f\",\"w\").write(\"x\")'",
    "curl -o page.html http://x", "curl -X POST http://localhost:8000/items -d '{}'",
    "tee out.txt < in", "make", "python3 fix.py && echo done > log", "chmod +x run.sh",
    "ls $(rm -rf x)", "some-unknown-tool --flag", "wget http://x/file",
])
def test_writes_and_unknown_commands_are_state_changes(engine, command):
    assert engine._shell_may_change(command)


def _run_shell(engine, monkeypatch, commands, cap=3):
    """The model runs `commands` as execute_shell checks after a finishing
    nudge; exec_tool bumps the mutation counter as run_tool does for any
    command outside the read-only list."""
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", cap)

    def script(i, messages):
        if _wrap_up_asked(messages):
            return "fresh summary"
        if i == 1:
            return "done"
        command = commands[(i - 2) % len(commands)] if i >= 2 else "ls"
        # Distinct each round: an identical repeat is short-circuited by the
        # loop guard and never runs, so it would not count as a round.
        command = f"{command} # round {i}"
        return f'✿FUNCTION✿: execute_shell ✿ARGS✿: {json.dumps({"command": command})}'

    calls = []

    def fake_model(messages, tools, **kwargs):
        calls.append(messages)
        return _response(script(len(calls) - 1, messages))

    def fake_exec(name, arguments, depth=0):
        command = json.loads(arguments).get("command", "")
        if not engine._readonly_shell(command):
            engine.note_mutation(command)
        return "ok"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", fake_exec)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "do it"}], max_turns=40,
                              trace=trace, dynamic_turns=False)
    return answer, calls, [t["type"] for t in trace]


def test_shell_checks_hit_the_cap(engine, monkeypatch):
    # The PR's own tests only used calculate; real checks are shell commands,
    # which used to reset the count every round so the cap never fired.
    checks = ["pytest -q", "python3 verify.py", "curl -s localhost:8000/health"]
    _answer, _calls, kinds = _run_shell(engine, monkeypatch, checks)
    assert "post_check_cap" in kinds and "post_check_backstop" in kinds


def test_shell_writes_still_reset_the_cap(engine, monkeypatch):
    commands = ["pytest -q", "sed -i s/bug/fix/ app.py"] * 10
    _answer, _calls, kinds = _run_shell(engine, monkeypatch, commands)
    assert "post_check_cap" not in kinds and "post_check_backstop" not in kinds


def test_replan_nudge_does_not_start_the_cap(engine, monkeypatch):
    # Two failed actions bring the re-plan nudge: the model is not finishing,
    # it is changing approach, so reading for a while must not be cut short.
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", 2)
    calls = []

    def fake_model(messages, tools, **kwargs):
        calls.append(messages)
        i = len(calls) - 1
        if i == 2:
            return _response("I cannot do this.")       # final-looking -> re-plan nudge
        if i >= 12:
            return _response("finished after reading")
        return _response(_calc(i))

    def fake_exec(name, arguments, depth=0):
        return "Error: failed" if len(calls) <= 2 else "2"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", fake_exec)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "compute"}], max_turns=40,
                              trace=trace, dynamic_turns=False)
    kinds = [t["type"] for t in trace]
    assert answer.startswith("finished after reading")
    assert "post_check_cap" not in kinds and "post_check_backstop" not in kinds


@pytest.mark.parametrize("changed,verdict,second_write,should_cap", [
    (True, "1 passed", False, True),
    (True, "1 passed", True, True),
    (False, "1 passed", False, False),
    (True, "1 failed", False, False),
    (True, "inspected file", False, False),
])
def test_verified_work_is_bounded_without_a_final_answer_attempt(
        engine, monkeypatch, changed, verdict, second_write, should_cap):
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", 2)
    calls = []

    # The check rounds call run_tests: since issue 1 (V-1) a test verdict is
    # only believed from a real runner, so printing "1 passed" from any other
    # tool no longer counts as evidence and would never arm the cap.
    def model(messages, tools, **kwargs):
        calls.append(messages)
        if _wrap_up_asked(messages):
            return _response("fresh summary")
        i = len(calls) - 1
        if i >= 12:
            return _response("finished")
        return _response(_run_tests() if i in (1, 3) else _calc(i))

    def execute(name, arguments, depth=0):
        i = len(calls) - 1
        if changed and (i == 0 or (second_write and i == 2)):
            engine.note_mutation("write output")
            return "wrote output"
        return verdict if i in (1, 3) else "inspected file"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "exec_tool", execute)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "complete work"}],
                              max_turns=20, trace=trace, dynamic_turns=False)
    kinds = [event["type"] for event in trace]
    assert ("post_check_backstop" in kinds) == should_cap
    if should_cap:
        assert answer.startswith("fresh summary")
        assert len(calls) == (8 if second_write else 6)


def test_failed_check_gets_a_failure_nudge_and_an_incomplete_result(engine, monkeypatch):
    # Every check here reports a failing verdict: the cap must not say "checks
    # are complete", and the run must end flagged incomplete, not as success.
    from agent8088 import trajectory as T
    real = T.TrajectoryState.after_tool

    def failing(self, operation, result, **kw):
        kw["verdict"] = "failed"
        return real(self, operation, result, **kw)

    monkeypatch.setattr(T.TrajectoryState, "after_tool", failing)
    seen = []

    def script(i, messages):
        seen.extend(str(m.get("content")) for m in messages)
        return "tests still fail" if _wrap_up_asked(messages) else _keeps_checking(i, messages)

    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    answer, _calls, kinds = _run(engine, monkeypatch, script)
    assert "post_check_backstop" in kinds
    assert not any(engine.POST_CHECK_CAP_NUDGE in m for m in seen)
    assert any(engine.POST_CHECK_FAILED_NUDGE in m for m in seen)
    assert engine.CHECKS_FAILED_NOTE in answer


def test_cli_maps_incomplete_final_answer_to_checks_failed():
    from agent8088 import cli
    assert cli._run_end_reason([{"type": "final_answer", "incomplete": True}]) == "checks_failed"
    assert cli._run_end_reason([{"type": "final_answer"}]) == "answered"
