"""The Terminal-Bench profile in bench/terminal-bench/ loads as intended and
fits the 32k window it was sized for."""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench" / "terminal-bench"


def _load(monkeypatch, tmp_path):
    workspace = tmp_path / "app"
    workspace.mkdir()
    text = (BENCH / "config.txt").read_text(encoding="utf-8")
    text = text.replace("/opt/agent8088/bench", str(BENCH)).replace("=/app", f"={workspace}")
    cfg = tmp_path / "config.txt"
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setenv("AGENT8088_CONFIG", str(cfg))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_DISPOSABLE_CONTAINER", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.delenv("AGENT8088_PROVIDER", raising=False)
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine
    return importlib.reload(engine)


def test_profile_exposes_only_the_core_tools(monkeypatch, tmp_path):
    e = _load(monkeypatch, tmp_path)
    assert set(e.TOOL_SPECS) == {"execute_shell", "write_file", "edit_file",
                                 "read_text", "last_output", "read_content"}
    shell = e.TOOL_SPECS["execute_shell"]
    assert shell["host"] and shell["timeout"] == 600
    assert "timeout" in shell["optional"]


def test_profile_provider_and_limits(monkeypatch, tmp_path):
    e = _load(monkeypatch, tmp_path)
    assert e._active_model_token_limits("vllm", "qwen3.8-27b") == (32768, 8192)
    body = e._provider_extra_body("vllm")
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert e.MAX_TOOL_TIMEOUT_SECONDS == 1800


def test_profile_prompt_fits_the_budget(monkeypatch, tmp_path):
    """System prompt + tool schemas must leave most of 32k for the task. The
    default profile spends ~14k tokens here."""
    e = _load(monkeypatch, tmp_path)
    prompt = e.compose_system_prompt(native_tools=True)
    schemas = json.dumps(e.build_tools_def(e.TOOL_SPECS))
    tokens = (len(prompt) + len(schemas)) // 4
    print(f"bench profile fixed prompt ~{tokens} tokens")
    assert tokens < 4000, tokens


def test_profile_uses_native_tool_calls(monkeypatch, tmp_path):
    e = _load(monkeypatch, tmp_path)
    assert e._native_tools_enabled(e.build_tools_def(e.TOOL_SPECS), "vllm")
    assert e.DISPOSABLE_CONTAINER and e.PERMISSION_MODE == "full-auto"
