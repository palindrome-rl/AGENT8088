"""A malformed browser knob in config.txt must not take Agent8088 down.

BROWSER_MAX_STEPS / BROWSER_TASK_TIMEOUT_SECONDS are parsed at module import,
so an unparseable value raised ValueError while `import agent8088.engine` was
still running - killing the CLI, the gateway and the MCP server outright, with
a bare traceback and no indication of which setting was at fault. The newer
browser knobs (_browser_max_actions_per_step, _browser_headless) already fall
back to their default rather than raise; these two are brought in line.
"""
import subprocess
import sys
import textwrap

BAD_VALUES = ["50 # steps", "", "abc", "12.5", "  "]


def _config(tmp_path, key, value):
    path = tmp_path / "config.txt"
    path.write_text(f"default_provider=ollama\n{key}={value}\n")
    return path


def _import_engine(tmp_path, config_path):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import agent8088.engine as A
            print(A.BROWSER_MAX_STEPS, A.BROWSER_TASK_TIMEOUT_SECONDS)
        """)],
        capture_output=True, text=True, timeout=180,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "AGENT8088_HOME": str(tmp_path),
             "AGENT8088_CONFIG": str(config_path)},
    )


import pytest


@pytest.mark.parametrize("value", BAD_VALUES)
@pytest.mark.parametrize("key", ["browser_max_steps", "browser_task_timeout_seconds"])
def test_a_malformed_browser_knob_falls_back_instead_of_killing_the_import(
        tmp_path, key, value):
    result = _import_engine(tmp_path, _config(tmp_path, key, value))

    assert result.returncode == 0, f"import died on {key}={value!r}:\n{result.stderr[-1500:]}"
    steps, timeout = result.stdout.strip().split()[-2:]
    assert (steps, timeout) == ("500", "600")


def test_a_valid_browser_knob_is_still_honoured(tmp_path):
    result = _import_engine(tmp_path, _config(tmp_path, "browser_max_steps", "42"))

    assert result.returncode == 0, result.stderr[-1500:]
    assert result.stdout.strip().split()[-2] == "42"
