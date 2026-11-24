"""Secret environment variables stay out of sandboxed commands, and a refusal says what to do next.

A sandboxed command inherited the environment Agent8088 started in, so a token in the user's shell was readable by any
code the model ran. A refusal said only where commands may write; a protected credential file read the same as a
write limit, the old text said venvs could not be made (they can, inside the shell's folder), and a model that ran
the same refused command again got the same sentence again."""
import base64
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("name, secret", [
    ("GITHUB_TOKEN", True), ("OPENAI_API_KEY", True), ("AWS_SECRET_ACCESS_KEY", True), ("AWS_ACCESS_KEY_ID", True),
    ("NPM_TOKEN", True), ("PGPASSWORD", True), ("DB_PASSWORD", True), ("STRIPE_KEY", True),
    ("GOOGLE_APPLICATION_CREDENTIALS", True), ("HF_TOKEN", True), ("DATABASE_URL", True),
    ("TOKENIZERS_PARALLELISM", False), ("PATH", False), ("HOME", False), ("KEYBOARD_LAYOUT", False),
    ("PYTHONPATH", False), ("AGENT8088_SANDBOX_TEMP", False),
])
def test_which_variables_are_secret(engine, monkeypatch, name, secret):
    monkeypatch.setattr(engine, "SANDBOX_PASS_ENV", frozenset())
    assert (name in engine._secret_env_names({name: "x"})) is secret


def test_a_variable_the_task_needs_can_be_passed(engine, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_PASS_ENV", frozenset({"GITHUB_TOKEN"}))
    assert engine._secret_env_names({"GITHUB_TOKEN": "x", "NPM_TOKEN": "y"}) == ["NPM_TOKEN"]


def test_srt_is_told_to_unset_them_and_never_sees_a_value(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    monkeypatch.setattr(engine, "SANDBOX_PASS_ENV", frozenset())
    monkeypatch.setenv("MY_SERVICE_TOKEN", "value-that-must-not-leak")
    settings = engine._sandbox_settings_data(False, tmp_path)
    assert {"name": "MY_SERVICE_TOKEN", "mode": "deny"} in settings["credentials"]["envVars"]
    assert "value-that-must-not-leak" not in str(settings)


def test_the_linux_and_macos_command_unsets_them_too(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["srt"])
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "v")
    engine._exec_native_sandbox("env", 10, cwd=tmp_path)
    assert "unset " in seen["argv"][-1] and "MY_SERVICE_TOKEN" in seen["argv"][-1]
    assert "=v" not in seen["argv"][-1]


def test_the_windows_bridge_drops_them(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_PASS_ENV", frozenset({"KEEP_ME_TOKEN"}))
    argv = engine._native_sandbox_shell_argv(
        f'"{sys.executable}" -c "import os; print(sorted(k for k in os.environ if k.endswith(\'_TOKEN\')))"')
    env = {**os.environ, "DROP_ME_TOKEN": "a", "KEEP_ME_TOKEN": "b", "AGENT8088_X_TOKEN": "c"}
    out = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60).stdout
    assert "DROP_ME_TOKEN" not in out and "KEEP_ME_TOKEN" in out and "AGENT8088_X_TOKEN" in out


@pytest.fixture
def denial(engine, monkeypatch):
    monkeypatch.setattr(engine, "_sandbox_denials", {})
    monkeypatch.setattr(engine, "_native_piped_spawn_blocked", None)
    return engine


def test_a_protected_credential_file_gets_its_own_reason(denial, tmp_path):
    result = "cat: /home/u/.ssh/id_rsa: Permission denied"
    explained = denial._explain_sandbox_denial(result, tmp_path, "cat ~/.ssh/id_rsa")
    assert "holds credentials" in explained and "/home/u/.ssh/id_rsa" in explained
    assert "may write only" not in explained


def test_the_write_limit_says_how_to_install_packages(denial, tmp_path):
    result = "ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/usr/lib/python3/x'"
    explained = denial._explain_sandbox_denial(result, tmp_path, "pip install x")
    assert f"virtual environment inside {tmp_path}" in explained
    assert "will not work here" not in explained


def test_the_same_refused_command_twice_is_told_to_stop(denial, tmp_path):
    result = "touch: cannot touch '/etc/x': Permission denied"
    first = denial._explain_sandbox_denial(result, tmp_path, "touch /etc/x")
    assert "refused 2 times" not in first
    second = denial._explain_sandbox_denial(result, tmp_path, "touch  /etc/x")
    assert "has now been refused 2 times" in second and "do not run it again" in second
    other = denial._explain_sandbox_denial(result, tmp_path, "touch /etc/y")
    assert "refused 2 times" not in other


def test_the_count_starts_again_each_turn(denial, tmp_path):
    result = "touch: cannot touch '/etc/x': Permission denied"
    denial._explain_sandbox_denial(result, tmp_path, "touch /etc/x")
    denial.reset_turn_counters()
    assert "refused 2 times" not in denial._explain_sandbox_denial(result, tmp_path, "touch /etc/x")
