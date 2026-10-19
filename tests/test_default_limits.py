"""The shipped defaults for how long a request and a single tool call may run."""
from agent8088 import engine


def test_a_request_starts_at_300_rounds_and_can_grow_to_900():
    assert engine.DEFAULT_MAX_TURNS == 300
    assert engine.DYNAMIC_TURNS_CEILING_MULTIPLIER == 3
    assert engine.DEFAULT_MAX_TURNS * engine.DYNAMIC_TURNS_CEILING_MULTIPLIER == 900


def test_one_tool_call_may_run_for_twenty_minutes(tmp_path):
    """Read in a fresh process with an empty config: the test session pins its own value."""
    import os, subprocess, sys
    empty = tmp_path / "config.txt"
    empty.write_text("", encoding="utf-8")
    env = {**os.environ, "AGENT8088_CONFIG": str(empty), "AGENT8088_HOME": str(tmp_path)}
    env.pop("MAX_TOOL_TIMEOUT_SECONDS", None)
    from pathlib import Path
    env["PYTHONPATH"] = str(Path(engine.__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", "from agent8088 import engine; print(engine.MAX_TOOL_TIMEOUT_SECONDS)"],
                         capture_output=True, text=True, env=env, timeout=120, cwd=str(tmp_path))
    assert out.stdout.strip().splitlines()[-1] == "1200", out.stderr[-300:]


def test_the_web_ui_accepts_the_grown_budget():
    from agent8088 import web_server
    assert web_server._MAX_TURNS_CEILING >= engine.DEFAULT_MAX_TURNS * engine.DYNAMIC_TURNS_CEILING_MULTIPLIER


def test_the_config_template_shows_the_same_defaults():
    from pathlib import Path
    text = (Path(engine.__file__).parent / "config.txt").read_text(encoding="utf-8")
    assert "\nmax_turns=300\n" in text
    assert "# dynamic_turns_ceiling_multiplier=3" in text
    assert "# max_tool_timeout_seconds=1200" in text
