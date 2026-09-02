import os
import subprocess
import sys


def test_gateway_without_enabled_adapters_exits_nonzero(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("sandbox_backend=local\n", encoding="utf-8")
    env = os.environ.copy()
    env["AGENT8088_CONFIG"] = str(config)
    env["AGENT8088_SANDBOX"] = "local"

    result = subprocess.run(
        [sys.executable, "-m", "agent8088.gateway"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "No messaging platforms enabled" in result.stderr
