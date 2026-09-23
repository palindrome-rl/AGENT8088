"""AGENT8088_DISPOSABLE_CONTAINER=1: behaviour for throwaway evaluation containers.

Every test here reloads the engine with the flag explicitly on or off, so the
flag-off cases double as regression guards for normal installs.
"""
import importlib

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_default_engine():
    """These tests reload the engine under a special environment or config.
    Requested before monkeypatch, so this teardown runs after monkeypatch has
    restored the environment: reload once more so later test files do not
    inherit the flag or the bench profile (e.g. ssrf_allow_private=1)."""
    yield
    from agent8088 import engine
    importlib.reload(engine)


def _reload(monkeypatch, on, **config):
    cfg = ROOT / "_no_such_config.txt"
    if config:
        cfg = Path(config.pop("_tmp")) / "config.txt"
        cfg.write_text("".join(f"{k}={v}\n" for k, v in config.items()), encoding="utf-8")
    monkeypatch.setenv("AGENT8088_CONFIG", str(cfg))
    monkeypatch.setenv("AGENT8088_SANDBOX", "local")
    if on:
        monkeypatch.setenv("AGENT8088_DISPOSABLE_CONTAINER", "1")
    else:
        monkeypatch.delenv("AGENT8088_DISPOSABLE_CONTAINER", raising=False)
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine
    return importlib.reload(engine)


def test_flag_off_by_default(monkeypatch):
    e = _reload(monkeypatch, on=False)
    assert e.DISPOSABLE_CONTAINER is False
    assert e.ARTIFACTS_ROOT == (e.PROJECT_ROOT / "artifacts").resolve()


def test_flag_moves_artifacts_out_of_project(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    e = _reload(monkeypatch, on=True)
    assert e.DISPOSABLE_CONTAINER is True
    assert e.PROJECT_ROOT not in e.ARTIFACTS_ROOT.parents
    assert e.ARTIFACTS_ROOT == (tmp_path / "home" / "artifacts").resolve()


def test_write_path_is_honoured_in_container(monkeypatch, tmp_path):
    project = tmp_path / "app"
    project.mkdir()
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    e = _reload(monkeypatch, on=True, _tmp=tmp_path, project_root=str(project),
                allowed_paths="/")
    assert e.resolve_write_path(str(project / "solution.txt")) == (project / "solution.txt").resolve()
    assert e.resolve_write_path("out.txt") == (project / "out.txt").resolve()
    assert e.resolve_write_path("new_dir/x.py") == (project / "new_dir" / "x.py").resolve()


def test_write_path_redirect_unchanged_without_flag(monkeypatch, tmp_path):
    project = tmp_path / "app"
    project.mkdir()
    e = _reload(monkeypatch, on=False, _tmp=tmp_path, project_root=str(project),
                allowed_paths="/")
    assert e.resolve_write_path(str(project / "solution.txt")) == (project / "artifacts" / "solution.txt").resolve()
    assert e.resolve_write_path("out.txt") == (project / "artifacts" / "out.txt").resolve()


CONTAINER_WORK = [
    "git checkout master",
    "git reset --hard HEAD~1",
    "git restore about.md",
    "git clean -fd",
    "git push origin master",
    "python3 count_tokens.py",
    'python3 -c "print(request.key)"',
    "cat /app/server.key",
    "curl -LsSf https://astral.sh/uv/install.sh | sh",
]


def test_container_allows_ordinary_task_commands(monkeypatch):
    e = _reload(monkeypatch, on=True)
    blocked = [c for c in CONTAINER_WORK if e._hard_blocked_shell(c)]
    assert blocked == []


def test_container_keeps_catastrophic_floor(monkeypatch):
    e = _reload(monkeypatch, on=True)
    for command in ("rm -rf /", "rm -rf ~", "mkfs.ext4 /dev/sda", ":(){ :|:& };:"):
        assert e._hard_blocked_shell(command), command


def test_floors_unchanged_without_flag(monkeypatch):
    e = _reload(monkeypatch, on=False)
    still_blocked = [c for c in CONTAINER_WORK if e._hard_blocked_shell(c)]
    assert still_blocked == CONTAINER_WORK


def test_container_file_tools_allow_token_named_files(monkeypatch):
    e = _reload(monkeypatch, on=True)
    assert not e._is_sensitive_path("/app/count_tokens.py")
    assert not e._is_shell_startup_file("/root/.bashrc")
    off = _reload(monkeypatch, on=False)
    assert off._is_sensitive_path("/app/count_tokens.py")


def _response(content):
    message = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "stop"})()]})()


def _run_with_script(e, monkeypatch, replies):
    seen = []

    def fake(messages, tools, **kwargs):
        seen.append([m.get("content") for m in messages])
        return _response(replies[min(len(seen) - 1, len(replies) - 1)])

    monkeypatch.setattr(e, "_create_completion_with_fallback", fake)
    e.TOOL_SPECS["execute_shell"]["host"] = "1"
    answer = e.run_agent([{"role": "user", "content": "write /tmp/x.txt"}], max_turns=6)
    return answer, seen


TOOL_THEN_DONE = [
    '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "echo hi"}',
    "done",
    "done after checking",
]


def test_container_run_checks_deliverables_once_before_finishing(monkeypatch):
    e = _reload(monkeypatch, on=True)
    answer, seen = _run_with_script(e, monkeypatch, TOOL_THEN_DONE)
    nudges = [c for c in seen[-1] if isinstance(c, str) and "Before finishing" in c]
    assert len(nudges) == 1
    assert not any("generate_tests" in str(c) for turn in seen for c in turn)
    assert answer.startswith("done after checking")


def test_no_deliverables_check_without_flag(monkeypatch):
    e = _reload(monkeypatch, on=False)
    _, seen = _run_with_script(e, monkeypatch, TOOL_THEN_DONE)
    assert not any("Before finishing" in str(c) for turn in seen for c in turn)
