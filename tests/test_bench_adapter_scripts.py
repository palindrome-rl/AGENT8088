"""The container-side pieces of the Harbor adapter, run with real bash.

agent.py itself imports Harbor and is exercised by a live `harbor run`; these
cover the parts that do not need Harbor: the config rewrite and the launcher.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench" / "terminal-bench"
sys.path.insert(0, str(BENCH))

from agent8088_harbor.config_edit import config_edit_command  # noqa: E402


def test_config_edit_replaces_keys_and_keeps_the_rest(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("# comment\nprovider.vllm.base_url=http://old/v1\nmemory=0\n"
                   "provider.vllmX.base_url=keep\nmodel_name=old\n", encoding="utf-8")
    edits = {"provider.vllm.base_url": "http://gpu:8000/v1", "model_name": "qwen3.8-27b",
             "max_turn_seconds": "840"}
    subprocess.run(["bash", "-c", config_edit_command(str(cfg), edits)], check=True)
    lines = cfg.read_text(encoding="utf-8").splitlines()
    assert "# comment" in lines and "memory=0" in lines
    assert "provider.vllmX.base_url=keep" in lines   # the dot is literal, not a wildcard
    assert lines.count("provider.vllm.base_url=http://gpu:8000/v1") == 1
    assert "provider.vllm.base_url=http://old/v1" not in lines
    assert lines.count("model_name=qwen3.8-27b") == 1
    assert "max_turn_seconds=840" in lines


def test_config_edit_quotes_hostile_values(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("model_name=x\n", encoding="utf-8")
    subprocess.run(["bash", "-c", config_edit_command(str(cfg), {"model_name": "a'; touch pwned; '"})],
                   check=True, cwd=tmp_path)
    assert not (tmp_path / "pwned").exists()
    assert "model_name=a'; touch pwned; '" in cfg.read_text(encoding="utf-8")


def _launcher(tmp_path, fake_body, budget):
    """Run run-agent.sh with /opt/agent8088/bin/agent8088 replaced by a fake."""
    script = (BENCH / "container" / "run-agent.sh").read_text(encoding="utf-8")
    fake = tmp_path / "agent8088"
    fake.write_text("#!/bin/bash\n" + fake_body, encoding="utf-8")
    fake.chmod(0o755)
    logs = tmp_path / "logs"
    script = script.replace("/opt/agent8088/bin/agent8088", str(fake)).replace("/logs/agent", str(logs))
    launcher = tmp_path / "run-agent.sh"
    launcher.write_text(script, encoding="utf-8")
    instruction = tmp_path / "instruction.md"
    instruction.write_text("do it", encoding="utf-8")
    env = {**os.environ, "AGENT8088_HOME": str(tmp_path / "home"), "AGENT8088_WORKDIR": str(tmp_path)}
    result = subprocess.run(["bash", str(launcher), str(instruction), str(budget)],
                            capture_output=True, text=True, env=env, timeout=60)
    return result, logs / "agent8088.txt"


def test_launcher_passes_the_instruction_file_and_logs(tmp_path):
    result, log = _launcher(tmp_path, 'echo "got $1 $(cat $2)"; pwd\n', budget=0)
    assert result.returncode == 0, result.stderr
    assert f"got --prompt-file do it" in log.read_text()
    assert str(tmp_path) in log.read_text()   # ran in AGENT8088_WORKDIR


def test_launcher_budget_stops_the_agent_and_exits_zero(tmp_path):
    if subprocess.run(["bash", "-c", "command -v timeout"], capture_output=True).returncode:
        import pytest
        pytest.skip("coreutils timeout not installed on this host")
    result, _ = _launcher(tmp_path, "sleep 30\n", budget=2)
    assert result.returncode == 0
    assert "stopped at the 2s budget" in result.stdout


def test_launcher_propagates_real_failures(tmp_path):
    result, _ = _launcher(tmp_path, "exit 3\n", budget=0)
    assert result.returncode == 3
