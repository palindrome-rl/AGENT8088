"""Running the program a run just changed is how a model checks it.

Seen in the glm-5.3-flash benchmark: after writing fib.py or fixing
buggy.py, the model ran `python fib.py` to check it. Running code can change
anything, so that run counted as a new change, re-armed "a changed result has
no fresh verification evidence", and the model re-ran the same check twice.
"""
NUDGE = "Changed work has no fresh verification evidence"


def _response(content):
    message = type("Message", (), {"content": content, "tool_calls": None})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    return type("Response", (), {"choices": [choice]})()


def _run(monkeypatch, engine, filename, command, shell_result):
    replies = iter([
        f'✿FUNCTION✿: write_file ✿ARGS✿: {{"filename":"{filename}","content":"x"}}',
        f'✿FUNCTION✿: execute_shell ✿ARGS✿: {{"command":"{command}"}}',
        "Done.", "Done.", "Done.",
    ])
    prompts, state = [], {}

    def completion(messages, *_a, **_k):
        prompts.extend(str(m.get("content") or "") for m in messages)
        return _response(next(replies))

    def tool(name, arguments, depth=0):
        engine.note_mutation(name)          # a write, and code that ran
        return f"Wrote 1 bytes to {filename}" if name == "write_file" else shell_result

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "exec_tool", tool)
    engine.run_agent(
        [{"role": "user", "content": "write it and run it"}], max_turns=6,
        tools_def=[], allowed_tools={"write_file", "execute_shell"}, trajectory_state=state,
    )
    return prompts, state


def test_running_the_changed_program_counts_as_checking_it(monkeypatch, engine):
    prompts, state = _run(monkeypatch, engine, "fib.py", "python3 fib.py", "832040")
    assert not any(NUDGE in p for p in prompts)
    assert state["verification"] == "inspected"


def test_running_it_by_path_also_counts(monkeypatch, engine):
    prompts, _state = _run(monkeypatch, engine, "src/fib.py",
                           "cd src && python3 ./fib.py --n 30", "832040")
    assert not any(NUDGE in p for p in prompts)


def test_running_some_other_program_still_needs_checking(monkeypatch, engine):
    prompts, _state = _run(monkeypatch, engine, "fib.py", "python3 other.py", "ok")
    assert any(NUDGE in p for p in prompts)


def test_a_failing_run_is_not_a_check(monkeypatch, engine):
    prompts, _state = _run(monkeypatch, engine, "fib.py", "python3 fib.py",
                           "Traceback (most recent call last):\nCommand exited with status 1.")
    assert any(NUDGE in p for p in prompts)


def test_only_changed_code_files_count(monkeypatch, engine):
    """A data file is checked by reading it, which is already not a change;
    `python3 process.py report.txt` runs other code over it."""
    prompts, _state = _run(monkeypatch, engine, "report.txt",
                           "python3 process.py report.txt", "ok")
    assert any(NUDGE in p for p in prompts)
