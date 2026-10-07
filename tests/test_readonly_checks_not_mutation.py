"""A read-only shell check must not count as changing the workspace.

Seen live with glm-5.3-flash: asked to sum a CSV, the model ran
`pwd && ls -la`, then `cat data.csv && echo '---' && awk ...`. Both were
read-only, but `&&` made them look like changes, so each one re-armed "changed
work has no fresh verification evidence" and the model re-checked a finished
answer three more times.
"""
import os

import pytest

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX commands")


def _host_shell(engine, tmp_path, monkeypatch):
    engine.TOOL_SPECS["execute_shell"]["host"] = "1"
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    (tmp_path / "data.csv").write_text("alpha,1\nbeta,2\ngamma,3\n")


def _mutated(engine, command):
    before = engine._MUTATION_SEQ
    engine.run_tool("execute_shell", {"command": command})
    return engine._MUTATION_SEQ != before


@posix_only
@pytest.mark.parametrize("command", [
    "pwd && ls -la",
    "cat data.csv; wc -l data.csv",
    "cut -d, -f2 data.csv | paste -sd+ - | bc",
    "cat data.csv && echo '---' && md5 data.csv 2>/dev/null || md5sum data.csv",
    "awk -F',' '{sum+=$2} END {print sum}' data.csv",       # seen live: the sum itself
    "cat data.csv && awk '{print}' data.csv",
    "awk -F, 'NR>0 {s+=$2} END {print s}' data.csv",       # seen live: > is a comparison
    "awk -F, '$2 > 500 && $2 < 900 {n++} END {print n}' data.csv",
])
def test_chained_read_only_checks_do_not_count_as_changes(engine, tmp_path, monkeypatch, command):
    _host_shell(engine, tmp_path, monkeypatch)
    assert not _mutated(engine, command)


@posix_only
@pytest.mark.parametrize("command", [
    "echo hi > out.txt",
    "ls && touch new.txt",
    "awk 'BEGIN {system(\"touch x\")}'",          # awk running a command
    "awk '{print > \"copy.txt\"}' data.csv",       # awk writing a file
    "awk -f prog.awk data.csv",                      # a program it cannot inspect
    "awk '{print | \"sh\"}' data.csv",              # awk piping to a command
    "awk '{printf \"%s\", $1 >> \"log.txt\"}' data.csv",
    "sort -o data.csv data.csv",
    "echo $(touch sneaky.txt)",
])
def test_anything_that_can_write_still_counts(engine, tmp_path, monkeypatch, command):
    _host_shell(engine, tmp_path, monkeypatch)
    assert _mutated(engine, command)


def test_permission_check_is_unchanged(engine):
    """Approval prompts keep using the strict check: a chain still needs one."""
    assert not engine._readonly_shell("pwd && ls -la")
    assert not engine._readonly_shell("echo hi")
