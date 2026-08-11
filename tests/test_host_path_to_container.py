"""A host path in a shell command is rewritten to what the container will see.

The mirror of test_container_path_mapping. The agent reads a file at an absolute
Windows path, then hands that same path to execute_shell — which runs in the
container, where C:\\Users\\... does not exist:

    cd "C:\\Users\\...\\artifacts" && python library.py
    -> sh: 1: cd: can't cd to C:...

Observed cost of not doing this: the agent tried the Windows path, then invented
/home/user/..., then /c/Users/... (Git Bash style), then `find / -name
library.py`, before discovering /workspace — 11 tool calls and 97% of the turn's
tokens spent locating a file it had just written.
"""
from pathlib import Path


WS = Path(r"C:\Users\me\project\artifacts")


def test_a_windows_path_becomes_the_mount_point(engine):
    command = f'ls -la "{WS}"'
    assert engine._to_container_path(command, WS) == 'ls -la "/workspace"'


def test_the_escaped_spelling_is_rewritten_too(engine):
    """A path that reached the model through JSON arrives double-backslashed."""
    command = 'ls -la "C:\\\\Users\\\\me\\\\project\\\\artifacts"'
    assert "/workspace" in engine._to_container_path(command, WS)
    assert "C:" not in engine._to_container_path(command, WS)


def test_a_forward_slash_spelling_is_rewritten(engine):
    command = "cat C:/Users/me/project/artifacts/library.py"
    assert engine._to_container_path(command, WS) == "cat /workspace/library.py"


def test_a_nested_file_keeps_its_tail(engine):
    command = f'python "{WS / "library.py"}"'
    assert engine._to_container_path(command, WS) == 'python "/workspace/library.py"'


def test_the_cd_that_failed_now_works(engine):
    """The exact command from the transcript."""
    command = f'cd "{WS}" && python library.py'
    assert engine._to_container_path(command, WS) == 'cd "/workspace" && python library.py'


def test_a_command_with_no_host_path_is_untouched(engine):
    for command in ("ls -la", "python3 library.py", "pwd && echo hi"):
        assert engine._to_container_path(command, WS) == command


def test_an_unrelated_absolute_path_is_left_alone(engine):
    """Only the workspace is mapped; other paths are the caller's problem."""
    command = "cat /etc/hostname"
    assert engine._to_container_path(command, WS) == command


def test_an_empty_workspace_changes_nothing(engine):
    assert engine._to_container_path("ls", Path("")) == "ls"
