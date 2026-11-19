"""A shell command used to be stopped after 25 s. Fetching a dataset or running a build takes longer, and the run lost turns on
"Command timed out after 25s" (4 of 59 calls in one recorded run). The shipped default is 60 s; a call can still ask for more, and
tool_timeout.execute_shell in config.txt overrides it."""
from agent8088 import engine


def test_execute_shell_default_is_60_seconds():
    assert engine.TOOL_SPECS["execute_shell"]["timeout"] == 60


def test_other_tools_keep_their_own_limits():
    assert engine.TOOL_SPECS["run_tests"]["timeout"] == 300
    assert engine.TOOL_SPECS["git_init"]["timeout"] == 20


def test_config_documents_the_setting():
    text = (engine.APP_DIR / "config.txt").read_text(encoding="utf-8")
    assert "tool_timeout.execute_shell" in text
