"""Planning gets ten minutes by default: a slow model takes several minutes to write a plan."""
import os
import subprocess
import sys
from pathlib import Path


def test_the_shipped_planning_ceiling_is_600_seconds(tmp_path):
    """Read in a fresh process with an empty config: the test session pins its own value."""
    from agent8088 import engine
    empty = tmp_path / "config.txt"
    empty.write_text("", encoding="utf-8")
    env = {**os.environ, "AGENT8088_CONFIG": str(empty), "AGENT8088_HOME": str(tmp_path)}
    env["PYTHONPATH"] = str(Path(engine.__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", "from agent8088 import engine; print(engine.PLAN_MODE_TIMEOUT_SECONDS)"],
                         capture_output=True, text=True, env=env, timeout=120, cwd=str(tmp_path))
    assert out.stdout.strip().splitlines()[-1] == "600", out.stderr[-300:]


def test_the_config_template_and_docs_show_the_same_default():
    from agent8088 import engine
    template = (Path(engine.__file__).parent / "config.txt").read_text(encoding="utf-8")
    assert "# plan_mode_timeout_seconds=600" in template
    docs = Path(engine.__file__).resolve().parents[2] / "docs" / "wiki" / "02-configuration.md"
    assert "| `plan_mode_timeout_seconds` | `600` |" in docs.read_text(encoding="utf-8")


def test_a_user_set_value_still_wins(tmp_path):
    from agent8088 import engine
    config = tmp_path / "config.txt"
    config.write_text("plan_mode_timeout_seconds=120\n", encoding="utf-8")
    env = {**os.environ, "AGENT8088_CONFIG": str(config), "AGENT8088_HOME": str(tmp_path)}
    env["PYTHONPATH"] = str(Path(engine.__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", "from agent8088 import engine; print(engine.PLAN_MODE_TIMEOUT_SECONDS)"],
                         capture_output=True, text=True, env=env, timeout=120, cwd=str(tmp_path))
    assert out.stdout.strip().splitlines()[-1] == "120", out.stderr[-300:]
