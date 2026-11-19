"""Shell commands keep their output; a missing program only adds a note.

The old path replaced the whole output with "'<first word>' is not available"
whenever "No such file or directory" or "status 127" appeared anywhere. Seen in
the Terminal-Bench runs: `mkdir -p /tmp/t2 && ... && ./prog; cat -A data/...`
came back as "'mkdir' is not available", and the data the model printed was lost.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell")


def test_ordinary_errors_keep_their_output_and_get_no_note(engine):
    cases = [
        "src\ndata\nls: cannot access 'x.txt': No such file or directory\nCommand exited with status 2.",
        "gcc -O2 main.c\nmain.c:3:10: fatal error: foo.h: No such file or directory\nCommand exited with status 2.",
        "step 1 ok\nFileNotFoundError: [Errno 2] No such file or directory: 'out.csv'\nCommand exited with status 1.",
        "bash: line 1: ./prog: No such file or directory\nEXIT=127\n---\nU001John Doe$",
    ]
    for out in cases:
        assert engine._shell_missing_program_note(out) == out


def test_a_missing_program_is_named_from_the_shell_and_output_is_kept(engine):
    out = "bash: line 1: git: command not found\nbash: line 1: git: command not found\nCommand exited with status 127."
    shown = engine._shell_missing_program_note(out)
    assert shown.startswith(out)
    assert "'git' is not installed" in shown and shown.count("'git'") == 1
    dash = engine._shell_missing_program_note("/bin/sh: 1: xxd: not found\nCommand exited with status 127.")
    assert "'xxd' is not installed" in dash


def test_real_commands_through_the_executor(engine):
    run = lambda cmd: engine._shell_missing_program_note(engine._exec_process(cmd, timeout=20, shell=True))
    # The cobol case: mkdir works, ./prog is missing, cat's output must survive.
    shown = run("mkdir -p /tmp/a8t && cd /tmp/a8t && printf 'U001' > f.dat && ./prog; echo EXIT=$?; cat -vet f.dat")
    assert "EXIT=127" in shown and "U001" in shown and "not installed" not in shown
    assert "'mkdir'" not in shown and "'cd'" not in shown
    # A genuinely missing program: the real error stays, plus a note naming it.
    shown = run("cd /tmp && definitely-not-a-program-a8 --version")
    assert "definitely-not-a-program-a8: command not found" in shown
    assert "'definitely-not-a-program-a8' is not installed" in shown and "'cd'" not in shown
